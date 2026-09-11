from __future__ import annotations

import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status

from app.database import get_session_factory
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
            data = await websocket.receive_text()
            # Suporta heartbeat básico para manter viva a conexão
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(user.id, websocket)
    except Exception as error:
        logger.warning("Erro na conexão WebSocket para user %s: %s", user.id, error)
        await ws_manager.disconnect(user.id, websocket)
