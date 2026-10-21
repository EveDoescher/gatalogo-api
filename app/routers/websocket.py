from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Query,
    status,
)

from app.database import get_session_factory
from app.persistence_models import User
from app.services.auth_service import authenticated_user
from app.services.conversations import conversation_recipients, mark_messages_read
from app.services.realtime_events import publish_committed_events
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


async def _conversation_event(factory, user, data):
    raw_id = data.get("conversation_id")
    if not isinstance(raw_id, str):
        return
    try:
        conversation_id = UUID(raw_id)
    except ValueError:
        return
    async with factory() as session:
        if data["type"] == "typing":
            recipients = await conversation_recipients(
                session, conversation_id, user.id
            )
            for recipient in recipients:
                await ws_manager.send_to_user(
                    recipient,
                    "user_typing",
                    {
                        "conversation_id": str(conversation_id),
                        "user_id": str(user.id),
                        "username": user.username or "amigo",
                        "is_typing": bool(data.get("is_typing", True)),
                    },
                )
        else:
            await mark_messages_read(session, conversation_id, user.id)
            await session.commit()
            await publish_committed_events(session)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
    factory = get_session_factory()
    try:
        async with factory() as session:
            user = await authenticated_user(token, session)
    except HTTPException:
        logger.info("WebSocket authentication rejected")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    except Exception:
        logger.exception("WebSocket authentication unavailable")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await ws_manager.connect(user.id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            if raw == "ping":
                await websocket.send_text("pong")
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            msg_type = data.get("type")
            if not isinstance(msg_type, str):
                continue
            if msg_type == "ping":
                await websocket.send_text("pong")
            elif msg_type in {"typing", "read"}:
                try:
                    await _conversation_event(factory, user, data)
                except HTTPException:
                    logger.info(
                        "WebSocket conversation access rejected for %s", user.id
                    )
                except Exception:
                    logger.exception(
                        "WebSocket conversation event failed for %s", user.id
                    )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket connection failed for %s", user.id)
    finally:
        await ws_manager.disconnect(user.id, websocket)
        try:
            async with factory() as session:
                db_user = await session.get(User, user.id)
                if db_user:
                    db_user.last_seen_at = datetime.now(UTC)
                    await session.commit()
        except Exception:
            logger.exception("Could not persist WebSocket last_seen for %s", user.id)
