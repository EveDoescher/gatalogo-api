from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from uuid import UUID

from app.config import get_settings


def _safe_suffix(suffix: str, fallback: str = ".jpg") -> str:
    """Keep extensions portable; exclude separators, device streams and NULs."""
    extension = suffix[1:] if suffix.startswith(".") else ""
    if 1 <= len(extension) <= 11 and extension.isascii() and extension.isalnum():
        return suffix
    return fallback


class PrivatePhotoStorage:
    """Armazenamento privado local, intercambiável por object storage depois."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or get_settings().photo_storage_dir)

    def key_for(self, user_id: UUID, cat_id: UUID, suffix: str) -> str:
        safe_suffix = _safe_suffix(suffix)
        return str(Path("users") / str(user_id) / "cats" / f"{cat_id}{safe_suffix}")

    def reference_key_for(
        self, user_id: UUID, cat_id: UUID, kind: str, suffix: str
    ) -> str:
        safe_suffix = _safe_suffix(suffix)
        safe_kind = "".join(char for char in kind if char.isascii() and (char.isalnum() or char in "_-"))
        return str(
            Path("users") / str(user_id) / "cats" / str(cat_id) / "references" / f"{safe_kind}{safe_suffix}"
        )

    def avatar_key_for(
        self, user_id: UUID, cat_id: UUID, version: int, name: str, suffix: str
    ) -> str:
        safe_suffix = _safe_suffix(suffix, ".bin")
        safe_name = "".join(char for char in name if char.isascii() and (char.isalnum() or char in "_-"))
        return str(
            Path("users") / str(user_id) / "cats" / str(cat_id) / "avatar" / str(version) / f"{safe_name}{safe_suffix}"
        )

    def absolute_path(self, key: str) -> Path:
        # Old Windows records used backslashes; Docker workers use POSIX paths.
        resolved = (self.root / key.replace("\\", "/")).resolve()
        root = self.root.resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError("Chave de foto inválida.")
        return resolved

    def save(self, *, user_id: UUID, cat_id: UUID, data: bytes, suffix: str) -> tuple[str, str]:
        return self._save_hashed_photo(self.key_for(user_id, cat_id, suffix), data)

    def save_reference(
        self, *, user_id: UUID, cat_id: UUID, kind: str, data: bytes, suffix: str
    ) -> tuple[str, str]:
        return self._save_hashed_photo(self.reference_key_for(user_id, cat_id, kind, suffix), data)

    def save_avatar_asset(
        self,
        *,
        user_id: UUID,
        cat_id: UUID,
        version: int,
        name: str,
        data: bytes,
        suffix: str,
    ) -> tuple[str, str]:
        key = self.avatar_key_for(user_id, cat_id, version, name, suffix)
        self._write(key, data)
        return key, hashlib.sha256(data).hexdigest()

    def profile_key_for(self, user_id: UUID, suffix: str) -> str:
        safe_suffix = _safe_suffix(suffix)
        return str(Path("users") / str(user_id) / "profile" / f"avatar{safe_suffix}")

    def save_profile_photo(
        self, *, user_id: UUID, data: bytes, suffix: str
    ) -> tuple[str, str]:
        return self._save_hashed_photo(self.profile_key_for(user_id, suffix), data)

    def chat_photo_key_for(self, conversation_id: UUID, message_id: UUID, suffix: str) -> str:
        safe_suffix = _safe_suffix(suffix)
        return str(Path("conversations") / str(conversation_id) / f"{message_id}{safe_suffix}")

    def save_chat_photo(
        self, *, conversation_id: UUID, message_id: UUID, data: bytes, suffix: str
    ) -> tuple[str, str]:
        return self._save_hashed_photo(self.chat_photo_key_for(conversation_id, message_id, suffix), data)

    def post_photo_key_for(self, post_id: UUID, suffix: str) -> str:
        safe_suffix = _safe_suffix(suffix)
        return str(Path("posts") / f"{post_id}{safe_suffix}")

    def save_post_photo(
        self, *, post_id: UUID, data: bytes, suffix: str
    ) -> tuple[str, str]:
        return self._save_hashed_photo(self.post_photo_key_for(post_id, suffix), data)

    def _save_hashed_photo(self, base_key: str, data: bytes) -> tuple[str, str]:
        content_hash = hashlib.sha256(data).hexdigest()
        base = Path(base_key)
        key = base.with_name(f"{base.stem}-{content_hash}{base.suffix}").as_posix()
        self._write(key, data)
        return key, content_hash

    def _write(self, key: str, data: bytes) -> None:
        destination = self.absolute_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Keep the previous complete file visible until all new bytes are written.
        # Same-directory replacement stays on one filesystem, including Windows.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".upload-", suffix=".tmp", delete=False
        ) as temporary:
            pending = Path(temporary.name)
        try:
            pending.write_bytes(data)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)

    def open(self, key: str) -> Path:
        path = self.absolute_path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path

    def delete(self, key: str | None) -> None:
        if not key:
            return
        path = self.absolute_path(key)
        if path.is_file():
            path.unlink(missing_ok=True)


photo_storage = PrivatePhotoStorage()
