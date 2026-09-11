from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import UUID

from app.config import get_settings


class PrivatePhotoStorage:
    """Armazenamento privado local, intercambiável por object storage depois."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or get_settings().photo_storage_dir)

    def key_for(self, user_id: UUID, cat_id: UUID, suffix: str) -> str:
        safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 12 else ".jpg"
        return str(Path("users") / str(user_id) / "cats" / f"{cat_id}{safe_suffix}")

    def reference_key_for(
        self, user_id: UUID, cat_id: UUID, kind: str, suffix: str
    ) -> str:
        safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 12 else ".jpg"
        safe_kind = "".join(char for char in kind if char.isascii() and (char.isalnum() or char in "_-"))
        return str(
            Path("users") / str(user_id) / "cats" / str(cat_id) / "references" / f"{safe_kind}{safe_suffix}"
        )

    def avatar_key_for(
        self, user_id: UUID, cat_id: UUID, version: int, name: str, suffix: str
    ) -> str:
        safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 12 else ".bin"
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
        content_hash = hashlib.sha256(data).hexdigest()
        base = Path(self.key_for(user_id, cat_id, suffix))
        key = base.with_name(f"{base.stem}-{content_hash}{base.suffix}").as_posix()
        destination = self.absolute_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return key, content_hash

    def save_reference(
        self, *, user_id: UUID, cat_id: UUID, kind: str, data: bytes, suffix: str
    ) -> tuple[str, str]:
        content_hash = hashlib.sha256(data).hexdigest()
        base = Path(self.reference_key_for(user_id, cat_id, kind, suffix))
        key = base.with_name(f"{base.stem}-{content_hash}{base.suffix}").as_posix()
        destination = self.absolute_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return key, content_hash

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
        destination = self.absolute_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return key, hashlib.sha256(data).hexdigest()

    def profile_key_for(self, user_id: UUID, suffix: str) -> str:
        safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 12 else ".jpg"
        return str(Path("users") / str(user_id) / "profile" / f"avatar{safe_suffix}")

    def save_profile_photo(
        self, *, user_id: UUID, data: bytes, suffix: str
    ) -> tuple[str, str]:
        content_hash = hashlib.sha256(data).hexdigest()
        base = Path(self.profile_key_for(user_id, suffix))
        key = base.with_name(f"{base.stem}-{content_hash}{base.suffix}").as_posix()
        destination = self.absolute_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return key, content_hash

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
            path.unlink()


photo_storage = PrivatePhotoStorage()
