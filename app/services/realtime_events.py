"""Events delivered to clients that are already using the app.

Database notifications remain the source of truth.  WebSocket events are only a
prompt for a connected client to refresh its already-authorized data.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import AppNotification
from app.services.websocket_manager import ws_manager
from app.services.fcm_service import fcm_sender


_PENDING_EVENTS_KEY = "gatalogo_realtime_events"


def queue_event(
    session: AsyncSession,
    *,
    user_id: UUID,
    event: str,
    data: Mapping[str, object],
) -> None:
    """Keep an event local to the transaction until its caller commits."""
    pending = session.info.setdefault(_PENDING_EVENTS_KEY, [])
    pending.append((user_id, event, dict(data)))


def queue_notification(session: AsyncSession, notification: AppNotification) -> None:
    """Queue a compact refresh hint; notification contents stay in the API."""
    queue_event(
        session,
        user_id=notification.user_id,
        event="new_notification",
        data={
            "notification_id": str(notification.id),
            "notification_type": notification.type,
        },
    )


async def publish_committed_events(session: AsyncSession) -> None:
    """Publish events queued by the transaction after a successful commit."""
    pending = session.info.pop(_PENDING_EVENTS_KEY, [])
    for user_id, event, data in pending:
        await ws_manager.send_to_user(user_id, event, data)
        if event == "new_notification":
            notification_id = data.get("notification_id")
            if isinstance(notification_id, str):
                await fcm_sender.send_notification(
                    user_id=user_id,
                    notification_id=notification_id,
                    notification_type=str(data.get("notification_type", "notification")),
                )
