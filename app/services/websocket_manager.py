from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import UUID
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.persistence_models import AppNotification

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = {}
        self._notification_cursors: dict[UUID, datetime] = {}
        self._lock = asyncio.Lock()
        self._bridge_task: asyncio.Task[None] | None = None

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = set()
                # Old notifications are loaded from the inbox. The bridge only
                # wakes the UI for notifications committed after this session.
                self._notification_cursors[user_id] = datetime.now(UTC)
            self._connections[user_id].add(websocket)
        logger.info("WebSocket conectado para user %s (total: %d)", user_id, len(self._connections[user_id]))

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            if user_id in self._connections:
                self._connections[user_id].discard(websocket)
                if not self._connections[user_id]:
                    del self._connections[user_id]
                    self._notification_cursors.pop(user_id, None)
        logger.info("WebSocket desconectado para user %s", user_id)

    async def send_to_user(self, user_id: UUID, event: str, payload: dict) -> None:
        """Envia um evento JSON para todas as conexões ativas do usuário."""
        async with self._lock:
            sockets = list(self._connections.get(user_id, set()))

        if not sockets:
            return

        message = json.dumps({"event": event, "data": payload}, default=str)
        dead_sockets: list[WebSocket] = []

        for ws in sockets:
            try:
                await ws.send_text(message)
            except Exception as error:
                logger.warning("Erro enviando WebSocket para %s: %s", user_id, error)
                dead_sockets.append(ws)

        if dead_sockets:
            async with self._lock:
                if user_id in self._connections:
                    for dead in dead_sockets:
                        self._connections[user_id].discard(dead)
                    if not self._connections[user_id]:
                        del self._connections[user_id]
                        self._notification_cursors.pop(user_id, None)

    async def broadcast(self, event: str, payload: dict) -> None:
        """Envia um evento para todos os usuários conectados."""
        async with self._lock:
            all_sockets = [
                (uid, ws)
                for uid, sockets in self._connections.items()
                for ws in sockets
            ]

        message = json.dumps({"event": event, "data": payload}, default=str)
        for uid, ws in all_sockets:
            try:
                await ws.send_text(message)
            except Exception:
                await self.disconnect(uid, ws)

    async def start_notification_bridge(
        self,
        session_factory: async_sessionmaker,
    ) -> None:
        """Forward notifications committed by a separate worker process.

        The recognition worker cannot access this process' in-memory sockets.
        Polling only the small, indexed notification table keeps the durable
        inbox as source of truth while making a connected app react promptly.
        """
        if self._bridge_task is None or self._bridge_task.done():
            self._bridge_task = asyncio.create_task(
                self._notification_bridge(session_factory),
                name="gatalogo-websocket-notification-bridge",
            )

    async def stop_notification_bridge(self) -> None:
        task, self._bridge_task = self._bridge_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _notification_bridge(self, session_factory: async_sessionmaker) -> None:
        while True:
            try:
                async with self._lock:
                    cursors = dict(self._notification_cursors)
                for user_id, cursor in cursors.items():
                    async with session_factory() as session:
                        notifications = (
                            await session.execute(
                                select(AppNotification)
                                .where(
                                    AppNotification.user_id == user_id,
                                    AppNotification.created_at > cursor,
                                )
                                .order_by(AppNotification.created_at)
                                .limit(100)
                            )
                        ).scalars().all()
                    if not notifications:
                        continue
                    for notification in notifications:
                        await self.send_to_user(
                            user_id,
                            "new_notification",
                            {"notification_id": str(notification.id)},
                        )
                    latest = notifications[-1].created_at
                    async with self._lock:
                        if self._notification_cursors.get(user_id, cursor) <= latest:
                            self._notification_cursors[user_id] = latest
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Falha na ponte de notificações WebSocket")
            await asyncio.sleep(3)


ws_manager = ConnectionManager()
