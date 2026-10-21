"""Server timestamps and the production calendar."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

CALENDAR = ZoneInfo("America/Sao_Paulo")


def utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def day(now):
    return now.astimezone(CALENDAR).date().isoformat()


def iso(value):
    return utc(value).isoformat() if value else None
