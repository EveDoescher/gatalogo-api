"""Optional caches must not turn valid analysis into a storage outage."""

import logging
from pathlib import Path
import sqlite3

import pytest

from app.services.analysis_cache import AnalysisCache


@pytest.mark.parametrize("fault", ["corrupt", "parent-file", "permission"])
def test_RES08_cache_initialization_preserves_unavailable_storage(
    tmp_path, monkeypatch, caplog, fault
):
    folder = tmp_path / "private-cache"
    if fault == "corrupt":
        folder.mkdir()
        path = folder / "gemini_semantic.sqlite3"
        path.write_bytes(b"controlled corrupt cache")
    elif fault == "parent-file":
        path = folder
        path.write_bytes(b"existing file must survive")
    else:
        path = folder
        original = Path.mkdir

        def denied(self, *args, **kwargs):
            if self == folder:
                raise PermissionError("PRIVATE_PATH_CANARY")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", denied)
    original_bytes = path.read_bytes() if path.is_file() else None
    with caplog.at_level(logging.WARNING):
        cache = AnalysisCache(str(folder))
        assert cache.get("private-key") is None
        assert cache.get_segmentation("private-key") is None
        cache.set("private-key", "PRIVATE_PAYLOAD_CANARY")
        cache.set_segmentation("private-key", "metadata", b"mask")
    if original_bytes is not None:
        assert path.read_bytes() == original_bytes
    assert "PRIVATE_PATH_CANARY" not in caplog.text
    assert "PRIVATE_PAYLOAD_CANARY" not in caplog.text


@pytest.mark.parametrize(
    "operation", ["get", "set", "get_segmentation", "set_segmentation"]
)
@pytest.mark.parametrize("fault", ["sqlite", "permission"])
def test_RES08_cache_operation_failure_is_optional_and_recoverable(
    tmp_path, monkeypatch, caplog, operation, fault
):
    cache = AnalysisCache(str(tmp_path))
    cache.set("existing", "preserved")
    cache.set_segmentation("existing", "metadata", b"mask")
    before = cache.path.read_bytes()

    def unavailable():
        error = sqlite3.OperationalError if fault == "sqlite" else PermissionError
        raise error("PRIVATE_CACHE_ERROR_CANARY")

    monkeypatch.setattr(cache, "_connect", unavailable)
    with caplog.at_level(logging.WARNING):
        if operation == "get":
            assert cache.get("existing") is None
        elif operation == "get_segmentation":
            assert cache.get_segmentation("existing") is None
        elif operation == "set":
            cache.set("new", "PRIVATE_PAYLOAD_CANARY")
        else:
            cache.set_segmentation("new", "PRIVATE_PAYLOAD_CANARY", b"mask")
    assert cache.path.read_bytes() == before
    assert "PRIVATE_CACHE_ERROR_CANARY" not in caplog.text
    assert "PRIVATE_PAYLOAD_CANARY" not in caplog.text
    reopened = AnalysisCache(str(tmp_path))
    assert reopened.get("existing") == "preserved"
    assert reopened.get_segmentation("existing") == ("metadata", b"mask")
    assert reopened.get("new") is None


@pytest.mark.parametrize("operation", ["get", "get_segmentation"])
def test_RES08_cache_corrupted_after_open_does_not_erase_evidence(tmp_path, operation):
    cache = AnalysisCache(str(tmp_path))
    cache.set("existing", "preserved")
    original = cache.path.read_bytes()
    broken = b"controlled runtime SQLite corruption"
    cache.path.write_bytes(broken)
    assert getattr(cache, operation)("existing") is None
    cache.set("ignored", "payload")
    assert cache.path.read_bytes() == broken
    cache.path.write_bytes(original)  # Repair this test's own cache, not product data.
    assert AnalysisCache(str(tmp_path)).get("existing") == "preserved"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["corrupt", "write-readonly", "read-permission", "parent-file"]
)
async def test_RES08_analysis_output_survives_cache_fault(tmp_path, monkeypatch, fault):
    from image_cases import configured, image_bytes, semantic

    service = configured(tmp_path, monkeypatch)
    calls = []

    def provider(*args):
        calls.append(1)
        return semantic()

    monkeypatch.setattr(service, "_analyze_with_gemini", provider)
    first = await service.analyze(cat_id="fixture", image_bytes=image_bytes())
    folder = tmp_path / "fault"
    if fault == "parent-file":
        folder.write_bytes(b"preserve unrelated file")
    else:
        folder.mkdir()
        if fault == "corrupt":
            (folder / "gemini_semantic.sqlite3").write_bytes(b"controlled bad cache")
    cache = AnalysisCache(str(folder))
    if fault == "write-readonly":
        monkeypatch.setattr(
            cache,
            "_connect",
            lambda: sqlite3.connect(cache.path.as_uri() + "?mode=ro", uri=True),
        )
    if fault == "read-permission":

        def denied():
            raise PermissionError("controlled permission failure")

        monkeypatch.setattr(cache, "_connect", denied)
    service.semantic_cache = cache
    second = await service.analyze(cat_id="fixture", image_bytes=image_bytes())
    assert first.model_dump(exclude={"analyzed_at"}) == second.model_dump(
        exclude={"analyzed_at"}
    )
    assert (
        len(calls) == 2
    )  # Both are cache misses; provider is a local deterministic fake.
