"""Firebase Cloud Messaging sender for durable in-app notifications."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import get_session_factory
from app.persistence_models import DeviceToken

logger = logging.getLogger(__name__)
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _copy_for(notification_type: str) -> tuple[str, str]:
    return {
        "possible_match": ("Nova pista no Gatálogo", "Há um possível avistamento para você conferir."),
        "match_confirmed": ("Seu avistamento ajudou", "Uma pessoa confirmou a pista que você registrou."),
        "message": ("Nova mensagem", "Você recebeu uma mensagem no Gatálogo."),
        "friend_invite": ("Novo convite", "Alguém quer ser seu amigo no Gatálogo."),
        "friend_accepted": ("Novo amigo", "Seu convite foi aceito."),
        "achievement": ("Nova conquista", "Uma conquista foi liberada no Gatálogo."),
    }.get(notification_type, ("Novidade no Gatálogo", "Há uma atualização para você conferir."))


def _enabled_for_device(preferences: dict, notification_type: str) -> bool:
    if notification_type in {"possible_match", "match_confirmed"}:
        return preferences.get("sighting_notifications", True) is not False
    if notification_type in {"message", "friend_invite", "friend_accepted"}:
        return preferences.get("social_notifications", True) is not False
    return True


class FcmSender:
    def __init__(self) -> None:
        self._credentials = None

    def _access_token(self) -> tuple[str, str] | None:
        settings = get_settings()
        if not settings.fcm_service_account_file:
            return None
        path = Path(settings.fcm_service_account_file)
        if not path.is_absolute() and not path.is_file():
            project_root = Path(__file__).resolve().parent.parent.parent
            candidate = project_root / path
            if candidate.is_file():
                path = candidate
        if not path.is_file():
            logger.warning("Arquivo de conta de serviço FCM não encontrado: %s", path)
            return None
        if self._credentials is None:
            self._credentials = service_account.Credentials.from_service_account_file(
                path,
                scopes=[_SCOPE],
            )
        if not self._credentials.valid or self._credentials.expired:
            self._credentials.refresh(Request())
        project_id = settings.fcm_project_id or self._credentials.project_id
        return self._credentials.token, project_id

    async def send_notification(self, *, user_id: UUID, notification_id: str, notification_type: str) -> None:
        credentials = await asyncio.to_thread(self._access_token)
        if credentials is None:
            return
        access_token, project_id = credentials
        title, body = _copy_for(notification_type)
        async with get_session_factory()() as session:
            devices = (
                await session.execute(select(DeviceToken).where(DeviceToken.user_id == user_id))
            ).scalars().all()
            tokens = [
                (device.id, device.token)
                for device in devices
                if _enabled_for_device(device.preferences or {}, notification_type)
            ]
        if not tokens:
            return
        url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        headers = {"Authorization": f"Bearer {access_token}"}
        invalid_device_ids = []
        async with httpx.AsyncClient(timeout=15) as client:
            for device_id, device_token in tokens:
                message = {
                    "message": {
                        "token": device_token,
                        "notification": {"title": title, "body": body},
                        "data": {"notification_id": notification_id},
                        "android": {
                            "priority": "HIGH",
                            "notification": {"channel_id": "gatalogo_alerts"},
                        },
                        "apns": {"payload": {"aps": {"sound": "default"}}},
                    }
                }
                try:
                    response = await client.post(url, headers=headers, json=message)
                    if response.status_code in {400, 404}:
                        invalid_device_ids.append(device_id)
                    elif response.is_error:
                        logger.warning("FCM respondeu %s para um dispositivo.", response.status_code)
                except httpx.HTTPError:
                    logger.warning("Não foi possível enviar uma notificação FCM.")
        if invalid_device_ids:
            async with get_session_factory()() as session:
                await session.execute(delete(DeviceToken).where(DeviceToken.id.in_(invalid_device_ids)))
                await session.commit()


fcm_sender = FcmSender()
