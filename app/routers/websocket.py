from __future__ import annotations

import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status

import json
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy import select

from app.database import get_session_factory
from app.persistence_models import ConversationMember, ConversationMessage, User
from app.services.auth_service import authenticated_user
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            user = await authenticated_user(token, session)
    except Exception as error:
        logger.warning("Falha na autenticação do WebSocket: %s", error)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.connect(user.id, websocket)
    try:
        while True:
            raw_text = await websocket.receive_text()
            if raw_text == "ping":
                await websocket.send_text("pong")
                continue
            try:
                data = json.loads(raw_text)
            except Exception:
                continue

            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_text("pong")
            elif msg_type == "typing":
                conv_id_str = data.get("conversation_id")
                is_typing = bool(data.get("is_typing", True))
                if conv_id_str:
                    try:
                        conv_id = UUID(conv_id_str)
                        async with session_factory() as session:
                            members = (await session.execute(
                                select(ConversationMember.user_id).where(
                                    ConversationMember.conversation_id == conv_id,
                                    ConversationMember.user_id != user.id,
                                )
                            )).scalars().all()
                            for recipient_id in members:
                                await ws_manager.send_to_user(
                                    recipient_id,
                                    "user_typing",
                                    {
                                        "conversation_id": str(conv_id),
                                        "user_id": str(user.id),
                                        "username": user.username or "amigo",
                                        "is_typing": is_typing,
                                    },
                                )
                    except Exception as err:
                        logger.warning("Erro no typing do websocket: %s", err)
            elif msg_type == "read":
                conv_id_str = data.get("conversation_id")
                if conv_id_str:
                    try:
                        conv_id = UUID(conv_id_str)
                        now = datetime.now(UTC)
                        async with session_factory() as session:
                            messages = (await session.execute(
                                select(ConversationMessage).where(
                                    ConversationMessage.conversation_id == conv_id,
                                    ConversationMessage.sender_id != user.id,
                                    ConversationMessage.read_at.is_(None),
                                )
                            )).scalars().all()
                            for m in messages:
                                m.read_at = now
                            if messages:
                                await session.commit()
                            members = (await session.execute(
                                select(ConversationMember.user_id).where(
                                    ConversationMember.conversation_id == conv_id,
                                    ConversationMember.user_id != user.id,
                                )
                            )).scalars().all()
                            for recipient_id in members:
                                await ws_manager.send_to_user(
                                    recipient_id,
                                    "messages_read",
                                    {
                                        "conversation_id": str(conv_id),
                                        "reader_id": str(user.id),
                                        "read_at": now.isoformat(),
                                    },
                                )
                    except Exception as err:
                        logger.warning("Erro no read do websocket: %s", err)
    except WebSocketDisconnect:
        await ws_manager.disconnect(user.id, websocket)
        try:
            async with session_factory() as session:
                db_user = await session.get(User, user.id)
                if db_user:
                    db_user.last_seen_at = datetime.now(UTC)
                    await session.commit()
        except Exception:
            pass
    except Exception as error:
        logger.warning("Erro na conexão WebSocket para user %s: %s", user.id, error)
        await ws_manager.disconnect(user.id, websocket)
        try:
            async with session_factory() as session:
                db_user = await session.get(User, user.id)
                if db_user:
                    db_user.last_seen_at = datetime.now(UTC)
                    await session.commit()
        except Exception:
            pass
