"""Events delivered to clients that are already using the app.

Database notifications remain the source of truth.  WebSocket events are only a
prompt for a connected client to refresh its already-authorized data.
"""
from __future__ import annotations

from collections.abc import Mapping
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import AppNotification
from app.services.websocket_manager import ws_manager
from app.services.fcm_service import fcm_sender


_PENDING_EVENTS_KEY = "gatalogo_realtime_events"
logger = logging.getLogger(__name__)


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
        try:
            await ws_manager.send_to_user(user_id, event, data)
        except Exception:
            # The transaction is already committed; clients can refresh via HTTP.
            logger.exception("Could not deliver committed WebSocket event")
        if event == "new_notification" and data.get('notification_type') != 'quintal_ready':
            notification_id = data.get("notification_id")
            if isinstance(notification_id, str):
                try:
                    await fcm_sender.send_notification(
                        user_id=user_id,
                        notification_id=notification_id,
                        notification_type=str(data.get("notification_type", "notification")),
                    )
                except Exception:
                    logger.exception("Could not deliver committed push notification")
