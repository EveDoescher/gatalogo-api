"""IMG-05 real local rejection paths, boundaries and business-effect oracles."""

from io import BytesIO
import os
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageFilter
import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("pipeline_cases", "image_cases", "input_bounds_cases", "security_cases")
pytest_plugins = ["security_cases", "extended_cases"]
from security_cases import evidence, uid
from image_cases import test_IMG05_SEC06_invalid_image_matrix  # noqa: F401
from input_bounds_cases import test_SEC06_upload_transport_bounds as test_IMG05_HTTP_transport_bounds  # noqa: F401
from pipeline_cases import cat, analyze, test_IMG05_blur_alone_follows_existing_quality_rule  # noqa: F401
from local_model_controls import model_settings, palette_semantics, prohibit_external_models
from measurements import resources
from windows_limits import cap_private_commit
from app import main
from app.config import Settings
from app.exceptions import InvalidImageError
from app.persistence_models import CatPhoto, ProgressEvent, RemoteCat, VisionJob
from app.services.analysis_service import AnalysisService
from app.services.preprocessing_service import PreprocessingService


@pytest.fixture(scope="module", autouse=True)
def own_memory_ceiling():
    return cap_private_commit(2 * 1024 * 1024 * 1024)


def png(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["no-cat", "multiple", "dark", "white", "blur", "invalid"])
async def test_IMG05_real_local_rejection_and_repeat_integrity(db, tmp_path, monkeypatch, defect):
    await cat(db)
    service = AnalysisService(model_settings(Path(os.environ["QUALITY_MODEL_CACHE"]), tmp_path / "cache"))
    network = prohibit_external_models(monkeypatch)
    calls = []

    def controlled(*args):
        calls.append(1)
        return palette_semantics(*args)

    monkeypatch.setattr(service, "_analyze_with_gemini", controlled)
    monkeypatch.setattr(main, "AnalysisService", lambda _: service)
    photo = Image.open(os.environ["QUALITY_MODEL_PHOTO"]).convert("RGB")
    if defect == "no-cat":
        data = png(Image.fromarray(np.random.default_rng(2102026).integers(0, 256, (300, 451, 3), dtype=np.uint8)))
        expected = (422, "NOT_A_CAT")
    elif defect == "multiple":
        composite = Image.new("RGB", (photo.width * 2, photo.height))
        composite.paste(photo, (0, 0))
        composite.paste(photo, (photo.width, 0))
        data, expected = png(composite), (422, "MULTIPLE_CATS")
    elif defect in {"dark", "white"}:
        data = png(Image.new("RGB", photo.size, (0, 0, 0) if defect == "dark" else (255, 255, 255)))
        expected = (422, "LOW_QUALITY")
    elif defect == "blur":
        data, expected = png(photo.filter(ImageFilter.GaussianBlur(1))), (200, None)
        prepared = service.preprocessing.prepare(data)
        blurred_quality = service.quality.analyze(prepared.vision_image)
        assert blurred_quality.usable, "Blur alone must preserve the existing usability rule"
    else:
        data, expected = b"not an image", (400, "INVALID_IMAGE")
    before = resources()
    started = time.perf_counter()
    first, repeated = await analyze(db, data), await analyze(db, data)
    elapsed = time.perf_counter() - started
    assert first.status_code == repeated.status_code == expected[0], (first.text, repeated.text)
    if expected[1]:
        assert first.json()["detail"]["code"] == repeated.json()["detail"]["code"] == expected[1]
        assert calls == []
    async with db.factory() as session:
        stored = await session.get(RemoteCat, uid("pipeline-cat"))
        photos = await session.scalar(select(func.count()).select_from(CatPhoto))
        jobs = await session.scalar(select(func.count()).select_from(VisionJob))
        events = list((await session.scalars(select(ProgressEvent).where(
            ProgressEvent.user_id == uid("A"), ProgressEvent.kind == "discovery",
        ))).all())
        if expected[0] != 200:
            assert stored.analysis is None and stored.photo_key is None and photos == jobs == 0
            assert len(events) == (0 if defect == "invalid" else 1)
            assert not events or events[0].evidence["eligible"] is False
            assert not list(db.storage.root.rglob("*.png")) and not list(db.storage.root.rglob("*.jpg"))
        else:
            assert stored.analysis is not None and photos == jobs == 1
            # The fixture is an owned cat: accepting its photo is not a discovery reward.
            assert len(events) == 1 and events[0].evidence["eligible"] is False
            assert len(calls) <= 1
    assert network == []
    evidence("IMG05-real-" + defect, {
        "scenario": "IMG-05", "defect": defect, "status": expected[0], "code": expected[1],
        "repeat_status": repeated.status_code, "controlled_semantic_calls": len(calls),
        "photos": photos, "jobs": jobs, "discovery_events": len(events),
        "elapsed_two_requests_seconds": elapsed, "resources_before": before,
        "resources_after": resources(), "paid_calls": 0,
        "scope": "Real cached Faster R-CNN/SAM2 and quality. Controlled semantics only for accepted blur. Repeated synthetic noise and a two-copy CC0 composition test rejection behavior, not population-level detection accuracy or identity.",
    })


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_IMG05_exact_byte_boundary(delta):
    settings = Settings(_env_file=None, max_image_mb=1)
    original = Path(os.environ["QUALITY_MODEL_PHOTO"]).read_bytes()
    data = original + b"\0" * (settings.max_image_bytes + delta - len(original))
    assert len(data) == settings.max_image_bytes + delta
    service = PreprocessingService(settings)
    if delta > 0:
        with pytest.raises(InvalidImageError, match="limite"):
            service.prepare(data)
    else:
        prepared = service.prepare(data)
        assert (prepared.original_width, prepared.original_height) == (451, 300)
    evidence("IMG05-bytes-" + str(delta), {
        "scenario": "IMG-05", "boundary": settings.max_image_bytes, "delta": delta,
        "accepted_preprocessing": delta <= 0, "fixture": "Valid PNG with trailing padding; no inference",
    })


@pytest.mark.parametrize("delta,size", [(-1, (128, 8386)), (0, (129, 8321)), (1, (130, 8257))])
def test_IMG05_exact_pixel_boundary(delta, size):
    boundary = 1073409
    assert size[0] * size[1] == boundary + delta and min(size) >= 128
    service = PreprocessingService(Settings(_env_file=None, max_image_pixels=boundary))
    data = png(Image.new("RGB", size, (30, 80, 120)))
    if delta > 0:
        with pytest.raises(InvalidImageError, match="pixels demais"):
            service.prepare(data)
    else:
        prepared = service.prepare(data)
        assert (prepared.original_width, prepared.original_height) == size
    evidence("IMG05-pixels-" + str(delta), {
        "scenario": "IMG-05", "boundary": boundary, "delta": delta,
        "size": size, "accepted_preprocessing": delta <= 0, "model_calls": 0,
    })
