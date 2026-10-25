"""Cache local e persistente de segmentação e respostas semânticas."""

from contextlib import closing
import logging
import sqlite3
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)


class AnalysisCache:
    """Evita recomputar segmentação (SAM 2) e semântica (Gemini) na mesma foto."""

    _lock = RLock()

    def __init__(self, directory: str) -> None:
        self.path = Path(directory).resolve() / "gemini_semantic.sqlite3"
        self._available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._disable(error)
            return
        self._execute(
            """
                CREATE TABLE IF NOT EXISTS semantic_results (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
        )
        self._execute(
            """
                CREATE TABLE IF NOT EXISTS segmentation_results (
                    cache_key TEXT PRIMARY KEY,
                    metadata TEXT NOT NULL,
                    mask_blob BLOB,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
        )

    def _disable(self, error: Exception) -> None:
        self._available = False
        # Preserve derived data for diagnosis; OS/SQLite messages may be private.
        logger.warning(
            "Optional analysis cache unavailable (%s).", type(error).__name__
        )

    def _execute(
        self, statement: str, parameters: tuple = (), *, fetch_one: bool = False
    ) -> tuple | None:
        with self._lock:
            if not self._available:
                return None
            try:
                with closing(self._connect()) as connection, connection:
                    cursor = connection.execute(statement, parameters)
                    return cursor.fetchone() if fetch_one else None
            except (sqlite3.Error, OSError) as error:
                self._disable(error)
                return None

    def get(self, cache_key: str) -> str | None:
        row = self._execute(
            "SELECT payload FROM semantic_results WHERE cache_key = ?",
            (cache_key,),
            fetch_one=True,
        )
        return str(row[0]) if row else None

    def set(self, cache_key: str, payload: str) -> None:
        self._execute(
            """
                INSERT INTO semantic_results (cache_key, payload)
                VALUES (?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = CURRENT_TIMESTAMP
                """,
            (cache_key, payload),
        )

    def get_segmentation(
        self,
        cache_key: str,
    ) -> tuple[str, bytes | None] | None:
        row = self._execute(
            """
                SELECT metadata, mask_blob
                FROM segmentation_results
                WHERE cache_key = ?
                """,
            (cache_key,),
            fetch_one=True,
        )
        if not row:
            return None
        return str(row[0]), (bytes(row[1]) if row[1] is not None else None)

    def set_segmentation(
        self,
        cache_key: str,
        metadata: str,
        mask_blob: bytes | None,
    ) -> None:
        self._execute(
            """
                INSERT INTO segmentation_results (cache_key, metadata, mask_blob)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    metadata = excluded.metadata,
                    mask_blob = excluded.mask_blob,
                    created_at = CURRENT_TIMESTAMP
                """,
            (cache_key, metadata, mask_blob),
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)
