"""SEC-05 path confinement, portable file names and real upload controls."""

from datetime import timedelta
import hashlib
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

pytest_plugins = ["security_cases"]
from security_cases import NOW, evidence, uid
from extended_cases import seed
from app.persistence_models import CatPhoto, RemoteCat
from app.routers import sync
from app.services.storage_service import PrivatePhotoStorage


@pytest.mark.parametrize(
    "style",
    [
        "parent",
        "windows-parent",
        "nested-posix",
        "nested-windows",
        "absolute-native",
        "absolute-posix",
    ],
)
def test_SEC05_read_write_delete_outside_sentinels_are_untouched(tmp_path, style):
    storage = PrivatePhotoStorage(tmp_path / "private")
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_bytes(b"DO_NOT_CHANGE")
    keys = {
        "parent": "../sentinel.txt",
        "windows-parent": "..\\sentinel.txt",
        "nested-posix": "users/../../sentinel.txt",
        "nested-windows": "users\\..\\..\\sentinel.txt",
        "absolute-native": str(sentinel.resolve()),
        "absolute-posix": sentinel.resolve().as_posix(),
    }
    key = keys[style]
    for operation in (storage.absolute_path, storage.open, storage.delete):
        with pytest.raises(ValueError):
            operation(key)
    with pytest.raises(ValueError):
        storage._write(key, b"corrupt")
    assert sentinel.read_bytes() == b"DO_NOT_CHANGE"
    evidence(
        "SEC05-sentinel-" + style,
        {
            "scenario": "SEC-05",
            "variant": style,
            "read_write_delete_rejected": True,
            "outside_owned_sentinel_unchanged": True,
        },
    )


def assert_portable(key):
    assert all(
        part not in {".", ".."}
        and not part.endswith((".", " "))
        and not any(char in part for char in '<>:"|?*\0')
        for part in key.replace("\\", "/").split("/")
    )


@pytest.mark.parametrize(
    "kind", ["primary", "reference", "avatar", "profile", "chat", "post"]
)
@pytest.mark.parametrize(
    "suffix",
    [
        ".png",
        ".JPEG",
        "",
        ".extensionistoolong",
        ".pn:g",
        ".png:ads",
        ".pn\0g",
        ".\\..\\x",
        "./../x",
        ".",
    ],
)
def test_SEC05_all_storage_writers_use_confined_portable_keys(tmp_path, kind, suffix):
    storage = PrivatePhotoStorage(tmp_path / "private")
    data = b"synthetic-byte-exact-photo"
    if kind == "primary":
        saved = storage.save(
            user_id=uid("B"), cat_id=uid("safe-cat"), data=data, suffix=suffix
        )
    elif kind == "reference":
        saved = storage.save_reference(
            user_id=uid("B"),
            cat_id=uid("safe-cat"),
            kind="../../front\\name",
            data=data,
            suffix=suffix,
        )
    elif kind == "avatar":
        saved = storage.save_avatar_asset(
            user_id=uid("B"),
            cat_id=uid("safe-cat"),
            version=1,
            name="../preview\\name",
            data=data,
            suffix=suffix,
        )
    elif kind == "profile":
        saved = storage.save_profile_photo(user_id=uid("B"), data=data, suffix=suffix)
    elif kind == "chat":
        saved = storage.save_chat_photo(
            conversation_id=uid("chat"),
            message_id=uid("message"),
            data=data,
            suffix=suffix,
        )
    else:
        saved = storage.save_post_photo(post_id=uid("post"), data=data, suffix=suffix)
    key, digest = saved
    assert_portable(key)
    assert storage.absolute_path(key).is_relative_to(storage.root.resolve())
    assert (
        storage.open(key).read_bytes() == data
        and digest == hashlib.sha256(data).hexdigest()
    )
    storage.delete(key)
    assert not storage.absolute_path(key).exists()
    evidence(
        f"SEC05-writer-{kind}-{suffix.encode().hex() or 'empty'}",
        {
            "scenario": "SEC-05",
            "writer": kind,
            "suffix_hex": suffix.encode().hex(),
            "confined_portable_key": True,
            "bytes_and_hash_preserved": True,
            "delete_positive": True,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["primary", "reference"])
async def test_SEC05_http_upload_names_never_escape_or_raise_unhandled_errors(
    db, monkeypatch, variant
):
    data = await seed(db)
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    monkeypatch.setattr(sync, "rebuild_analysis_from_photos", AsyncMock())
    sentinel = db.storage.root.parent / "sentinel.png"
    sentinel.write_bytes(b"EXTERNAL_SENTINEL")
    names = [
        "../sentinel.png",
        "..\\sentinel.png",
        str(sentinel.resolve()),
        sentinel.resolve().as_posix(),
        "CON.png",
        "gato🐈.png",
        "cat.pn:g",
        "cat.png:ads",
        "cat.pn\0g",
        "cat.extensionistoolong",
    ]
    base = f"/sync/cats/{uid('matrix-client')}"
    path = base + ("/photo" if variant == "primary" else "/reference-photos/front")
    observed = []
    for index, filename in enumerate(names):
        kwargs = {"files": {"image": (filename, data, "image/png")}}
        if variant == "primary":
            kwargs["data"] = {
                "updated_at": (NOW + timedelta(seconds=index + 1)).isoformat(),
                "device_id": "safe-upload",
            }
        response = await db.client.put(path, headers=db.headers("B"), **kwargs)
        assert response.status_code in {202, 204, 400, 413, 415, 422}, response.text
        assert sentinel.read_bytes() == b"EXTERNAL_SENTINEL"
        if response.status_code < 300:
            async with db.factory() as session:
                if variant == "primary":
                    key = (await session.get(RemoteCat, uid("matrix-cat"))).photo_key
                else:
                    photo = await session.scalar(
                        select(CatPhoto).where(
                            CatPhoto.cat_id == uid("matrix-cat"),
                            CatPhoto.kind == "front",
                        )
                    )
                    key = photo.storage_key
            assert_portable(key)
            assert db.storage.open(key).read_bytes() == data
            downloaded = await db.client.get(path, headers=db.headers("B"))
            assert downloaded.status_code == 200 and downloaded.content == data
        label = [
            "parent-posix",
            "parent-windows",
            "absolute-native",
            "absolute-posix",
            "reserved-base",
            "unicode-base",
            "invalid-colon",
            "alternate-stream",
            "null-byte",
            "long-extension",
        ][index]
        observed.append({"filename_variant": label, "status": response.status_code})
    evidence(
        "SEC05-http-filenames-" + variant,
        {
            "scenario": "SEC-05",
            "variant": variant,
            "checks": observed,
            "outside_owned_sentinel_unchanged": True,
            "owner_downloaded_exact_bytes": True,
            "scope": "Only synthetic own files and disposable schema; no inference or real host sentinel paths.",
        },
    )
