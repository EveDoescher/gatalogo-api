"""Cache local e persistente das respostas semânticas do Gemini."""

import sqlite3
from pathlib import Path
from threading import RLock


class AnalysisCache:
    """Evita chamadas repetidas para o mesmo recorte e contexto semântico."""

    _lock = RLock()

    def __init__(self, directory: str) -> None:
        self.path = Path(directory).resolve() / "gemini_semantic.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_results (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get(self, cache_key: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM semantic_results WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return str(row[0]) if row else None

    def set(self, cache_key: str, payload: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO semantic_results (cache_key, payload)
                VALUES (?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = CURRENT_TIMESTAMP
                """,
                (cache_key, payload),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)
