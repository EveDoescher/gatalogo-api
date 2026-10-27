"""Recover local cat-file cleanup without coupling filesystem failure to DB commit.

Candidates are recorded before commit, not unconditional delete commands. Readers
recheck committed references under the same Cat lock used by photo writers. The
journal lives beside the private files and needs no business-schema migration.
"""

import asyncio
from contextlib import suppress
import hashlib
import json
import logging
import os
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import CatAvatar, CatPhoto, RemoteCat
from app.services.storage_service import photo_storage

logger = logging.getLogger(__name__)
JOURNAL = ".photo-cleanup"


def queue_photo_cleanup(cat: RemoteCat, keys) -> None:
    """Call while holding the Cat lock, before committing its replacement/deletion."""
    for key in set(keys):
        if not key:
            continue
        key = key.replace("\\", "/")
        _validate_key(cat.user_id, cat.id, key)
        name = hashlib.sha256(key.encode()).hexdigest() + ".json"
        record = {"user_id": str(cat.user_id), "cat_id": str(cat.id), "key": key}
        photo_storage._write(f"{JOURNAL}/{name}", json.dumps(record).encode())


def _validate_key(user_id, cat_id, key):
    if ".." in key.split("/"):
        raise ValueError("Cleanup candidate contains parent traversal")
    photo_storage.absolute_path(key)
    prefix = f"users/{user_id}/cats/{cat_id}"
    if not (key.startswith(prefix + "/") or key.startswith(prefix + "-")):
        # The original non-content-addressed primary key is still supported.
        if not key.startswith(prefix + ".") or "/" in key[len(prefix):]:
            raise ValueError("Cleanup candidate is outside its cat namespace")


async def _referenced(session, key, cat):
    primary = await session.scalar(select(RemoteCat.id).where(
        RemoteCat.deleted_at.is_(None),
        func.replace(RemoteCat.photo_key, "\\", "/") == key,
    ).limit(1))
    if primary is not None:
        return True
    reference = await session.scalar(select(CatPhoto.id).join(RemoteCat).where(
        RemoteCat.deleted_at.is_(None), CatPhoto.deleted_at.is_(None),
        func.replace(CatPhoto.storage_key, "\\", "/") == key,
    ).limit(1))
    if reference is not None:
        return True
    if cat is not None and cat.deleted_at is None:
        avatar = await session.get(CatAvatar, cat.id)
        if avatar is not None:
            keys = [avatar.preview_key, *(avatar.texture_keys or {}).values()]
            return any(value and value.replace("\\", "/") == key for value in keys)
    return False


async def drain_photo_cleanup(session: AsyncSession, *, limit=25) -> dict[str, int]:
    """Use an independent transaction; keep failed candidates for a later attempt."""
    counts = {"deleted": 0, "retained": 0, "failed": 0}
    directory = photo_storage.absolute_path(JOURNAL)
    try:
        paths = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
    except OSError as error:
        logger.warning("Photo cleanup scan deferred (%s)", type(error).__name__)
        counts["failed"] += 1
        return counts
    for path in paths[:limit]:
        try:
            record = json.loads(path.read_bytes())
            cat_id, user_id = UUID(record["cat_id"]), UUID(record["user_id"])
            key = record["key"]
            _validate_key(user_id, cat_id, key)
            expected = hashlib.sha256(key.encode()).hexdigest() + ".json"
            if path.name != expected:
                raise ValueError("Cleanup candidate checksum mismatch")
            async with AsyncSession(bind=session.bind, expire_on_commit=False) as check:
                async with check.begin():
                    cat = await check.scalar(select(RemoteCat).where(
                        RemoteCat.id == cat_id,
                    ).with_for_update(nowait=True))
                    # A different worker may have completed it while we waited.
                    if not path.exists():
                        continue
                    if cat is not None and cat.user_id != user_id:
                        raise ValueError("Cleanup candidate owner mismatch")
                    retained = await _referenced(check, key, cat)
                    if not retained:
                        photo_storage.delete(key)
                    path.unlink(missing_ok=True)
                    counts["retained" if retained else "deleted"] += 1
        except FileNotFoundError:
            continue
        except (OSError, SQLAlchemyError, ValueError, KeyError, TypeError) as error:
            counts["failed"] += 1
            logger.warning("Photo cleanup deferred (%s)", type(error).__name__)
            # Failed records move behind other candidates, avoiding starvation.
            with suppress(OSError):
                os.utime(path, None)
    return counts


class PhotoCleanupWorker:
    def __init__(self, factory, interval=30):
        self.factory = factory
        self.interval = interval
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _run(self):
        while True:
            async with self.factory() as session:
                await drain_photo_cleanup(session)
            await asyncio.sleep(self.interval)
