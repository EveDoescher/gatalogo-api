"""Bounded malformed input probes; no model, provider or user data."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases", "extended_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from extended_cases import seed
from app import main
from app.config import get_settings
from app.persistence_models import SyncChange, VisionJob
from app.services.analysis_service import AnalysisService
from app.services.preprocessing_service import PreprocessingService
from app.exceptions import InvalidImageError


async def unchanged(db, files):
    async with db.factory() as session:
        for model in (SyncChange, VisionJob):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert {
        p.relative_to(db.storage.root): p.read_bytes()
        for p in db.storage.root.rglob("*")
        if p.is_file()
    } == files


def inventory(db):
    return {
        p.relative_to(db.storage.root): p.read_bytes()
        for p in db.storage.root.rglob("*")
        if p.is_file()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["html-mime", "empty", "truncated", "pixels"])
async def test_SEC06_analysis_rejects_before_model_or_write(db, monkeypatch, defect):
    await seed(db)
    settings = get_settings()
    monkeypatch.setattr(settings, "max_image_pixels", 128 * 128)
    image = BytesIO()
    Image.new("RGB", (129, 128)).save(image, format="PNG")
    body = {
        "html-mime": b"<html>not a photo</html>",
        "empty": b"",
        "truncated": image.getvalue()[:30],
        "pixels": image.getvalue(),
    }[defect]
    downstream = Mock(side_effect=AssertionError("Invalid photo reached model/quality"))
    service = AnalysisService.__new__(AnalysisService)
    service.settings = settings
    service.preprocessing = PreprocessingService(settings)
    service.quality = SimpleNamespace(analyze=downstream)
    service.segmentation = SimpleNamespace(segment_cat=downstream)
    monkeypatch.setattr(main, "AnalysisService", lambda _: service)
    files = inventory(db)
    response = await db.client.post(
        "/analyze",
        headers=db.headers("B"),
        data={"cat_id": str(uid("matrix-client"))},
        files={"image": ("fake.png", body, "image/png")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_IMAGE"
    downstream.assert_not_called()
    await unchanged(db, files)
    # Positive boundary proves the pixel limit does not reject every image.
    valid = BytesIO()
    Image.new("RGB", (128, 128)).save(valid, format="PNG")
    assert service.preprocessing.prepare(valid.getvalue()).original_width == 128
    evidence(
        "SEC06-analysis-" + defect,
        {
            "status": 400,
            "declared_mime": "image/png",
            "model_calls": 0,
            "writes": 0,
            "pixel_limit": 16384,
            "largest_generated_dimensions": [129, 128],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["analysis", "primary", "reference"])
@pytest.mark.parametrize("defect", ["oversize", "missing-file", "malformed-multipart"])
async def test_SEC06_upload_transport_bounds(db, monkeypatch, endpoint, defect):
    await seed(db)
    monkeypatch.setattr(get_settings(), "max_image_mb", 1)
    base = f"/sync/cats/{uid('matrix-client')}"
    url = {
        "analysis": "/analyze",
        "primary": base + "/photo",
        "reference": base + "/reference-photos/front",
    }[endpoint]
    data = {
        "cat_id": str(uid("matrix-client")),
        "updated_at": NOW.isoformat(),
        "device_id": "bounded",
    }
    files = inventory(db)
    kwargs = {"headers": db.headers("B")}
    expected = 422
    if defect == "oversize":
        kwargs.update(
            data=data,
            files={"image": ("bounded.png", b"x" * (1024 * 1024 + 1), "image/png")},
        )
        expected = 400 if endpoint == "analysis" else 413
    elif defect == "malformed-multipart":
        kwargs["headers"] = {**kwargs["headers"], "Content-Type": "multipart/form-data"}
        kwargs["content"] = b"bounded invalid multipart without boundary"
        expected = 400
    else:
        kwargs["data"] = data
    response = await db.client.request(
        "POST" if endpoint == "analysis" else "PUT", url, **kwargs
    )
    assert response.status_code == expected, response.text
    await unchanged(db, files)
    evidence(
        f"SEC06-transport-{endpoint}-{defect}",
        {"status": expected, "max_probe_bytes": 1048577, "writes": 0},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["limit=0", "limit=501", "limit=invalid", "cursor=-1", "cursor=invalid"]
)
async def test_SEC06_pagination_invalid_and_positive_boundaries(db, query):
    response = await db.client.get("/sync/pull?" + query, headers=db.headers("A"))
    assert response.status_code == 422
    for limit in (1, 500):
        valid = await db.client.get(
            f"/sync/pull?cursor=0&limit={limit}", headers=db.headers("A")
        )
        assert valid.status_code == 200 and valid.json()["changes"] == []
    evidence(
        "SEC06-query-" + query.replace("=", "-"),
        {"invalid": 422, "valid_limits": [1, 500]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("device", ["", "x" * 129])
async def test_SEC06_sync_field_rejection_has_no_side_effect(db, device):
    response = await db.client.post(
        "/sync/push",
        headers=db.headers("A"),
        json={
            "operations": [
                {
                    "client_id": str(uid("bounded-field")),
                    "updated_at": NOW.isoformat(),
                    "device_id": device,
                }
            ]
        },
    )
    assert response.status_code == 422
    async with db.factory() as session:
        assert await session.scalar(select(func.count()).select_from(SyncChange)) == 0
    evidence(
        "SEC06-device-" + str(len(device)),
        {"device_length": len(device), "status": 422, "revisions": 0},
    )


@pytest.mark.asyncio
async def test_SEC06_analysis_reader_stops_after_bounded_overflow():
    class Stream:
        def __init__(self, data):
            self.file = BytesIO(data)
            self.sizes = []

        async def read(self, size):
            self.sizes.append(size)
            return self.file.read(size)

    limit = 1024 * 1024
    stream = Stream(b"x" * (2 * limit + 1))
    with pytest.raises(InvalidImageError):
        await main._read_image_with_limit(stream, max_bytes=limit)
    assert stream.file.tell() == 2 * limit
    assert stream.sizes == [limit, limit]
    valid = Stream(b"x" * limit)
    assert len(await main._read_image_with_limit(valid, max_bytes=limit)) == limit
    evidence(
        "SEC06-read-bound",
        {
            "application_read_bytes_before_rejection": 2 * limit,
            "limit_bytes": limit,
            "boundary_accepted": True,
            "limitation": "Application reader only; multipart parser/proxy may spool entire request before route. No transport-wide body cap is established by this test.",
        },
    )
