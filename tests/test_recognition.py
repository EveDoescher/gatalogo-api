import math
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.services.recognition_features import (local_features, model_version,
    prepare_embedding_image, verify_local_features, foreground_quality, perceptual_hash, near_duplicate)
from app.services.recognition_service import (compare_embeddings, cosine_similarity,
    distance_meters, is_solid_analysis, valid_coordinates)
from app.services.vision_worker import Sam2DinoWorker


def row(*, solid=False, quality=0.9, version=None, view="unknown", content=None, vector=None, local=None):
    return SimpleNamespace(embedding=vector if vector is not None else [1.0] + [0.0]*383,
        model_version=version or model_version(Settings(_env_file=None)), region="body",
        view=view, photo_hash=content or str(uuid4()), photo_id=uuid4(),
        is_solid_coat=solid, quality_score=quality, features={"local": local or {}})


def compare(a, b, **kwargs):
    return compare_embeddings(a, b, Settings(_env_file=None), **kwargs)


def test_geography_boundary_dateline_poles_and_invalid():
    assert distance_meters(0, 0, 0, 0) == 0
    assert 220 < distance_meters(0, 179.999, 0, -179.999) < 225
    assert math.isfinite(distance_meters(90, 0, -90, 180))
    assert distance_meters(float("nan"), 0, 0, 0) == math.inf
    assert not valid_coordinates(True, 0)


@pytest.mark.parametrize("vector", [[], [0.0]*384, [float("nan")]*384, [float("inf")]*384])
def test_invalid_vectors_do_not_create_suggestions(vector):
    assert compare([row(vector=vector)], [row()]) is None


def test_cosine_rejects_mismatched_dimensions_and_normalizes():
    with pytest.raises(ValueError):
        cosine_similarity([1], [1, 2])
    assert cosine_similarity([5, 0], [2, 0]) == 1


def test_unknown_analysis_is_not_mislabeled_solid():
    assert not is_solid_analysis({"confidence": 0.1})
    assert is_solid_analysis({"coat_type": "Sólido"})


def test_solid_and_unknown_coats_never_confirm_from_body_similarity():
    for solid, unknown in [(True, False), (False, True)]:
        result = compare([row(solid=solid), row()], [row(), row()], uncertain_coat=unknown)
        assert result["restricted"]
        assert not result["evidence"]["identity_confirmed"]
        assert not result["evidence"]["calibrated"]


def test_single_high_cosine_is_only_restricted_candidate():
    result = compare([row()], [row()])
    assert result["similarity"] == 1
    assert result["restricted"]


def test_duplicate_content_does_not_manufacture_independent_support():
    result = compare([row(content="duplicate")]*3, [row(content="duplicate")]*3)
    assert result["restricted"]
    assert result["evidence"]["distinct_photo_pairs"] == 0


def test_one_query_against_many_references_is_one_pair():
    result = compare([row()], [row(), row(), row()])
    assert result["evidence"]["distinct_photo_pairs"] == 1
    assert result["restricted"]


def test_two_distinct_pairs_corroborate_patterned_candidate_but_not_identity():
    result = compare([row(), row()], [row(), row()])
    assert not result["restricted"]
    assert not result["evidence"]["identity_confirmed"]


def test_low_quality_incompatible_versions_and_opposite_sides_are_excluded():
    assert compare([row(quality=0.1)], [row()]) is None
    assert compare([row(version="legacy")], [row()]) is None
    assert compare([row(view="left")], [row(view="right")]) is None


def test_letterbox_preserves_extremities():
    image = Image.new("RGB", (100, 400), "black")
    image.paste("red", (0, 0, 100, 30))
    image.paste("blue", (0, 370, 100, 400))
    prepared = np.asarray(prepare_embedding_image(image))
    assert prepared.shape == (518, 518, 3)
    assert prepared[10, 259, 0] > 240
    assert prepared[508, 259, 2] > 240


def test_background_features_are_not_cat_evidence():
    rng = np.random.default_rng(43)
    image = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    mask = np.zeros((200, 200), dtype=bool)
    mask[70:130, 70:130] = True
    image[mask] = 100
    assert local_features(image, mask)["points"] == []


def test_local_geometry_rejects_scrambled_layout_and_accepts_transformation():
    rng = np.random.default_rng(10)
    points = rng.uniform(0.1, 0.8, (30, 2)).astype(np.float32)
    descriptors = rng.uniform(0, 1, (30, 128)).astype(np.float32)
    a = {"points": points.tolist(), "descriptors": descriptors.tolist()}
    b = {"points": (points * 0.8 + 0.05).tolist(), "descriptors": descriptors.tolist()}
    assert verify_local_features(a, b)["verified"]
    b["points"] = rng.uniform(0, 1, (30, 2)).tolist()
    assert not verify_local_features(a, b)["verified"]
    assert not verify_local_features({}, {})["verified"]


def test_multiple_cats_rejected_even_if_segmenter_returns_crop(monkeypatch):
    worker = Sam2DinoWorker(Settings(_env_file=None))
    monkeypatch.setattr(worker, "_load_models", lambda: None)
    worker._segmenter = SimpleNamespace(segment_cat=lambda image: SimpleNamespace(ambiguous_multiple_cats=True))
    with pytest.raises(ValueError, match="mais de um"):
        worker._primary_cat_crop(Image.new("RGB", (200, 200)))


def test_foreground_quality_is_not_inflated_by_background_texture():
    rng = np.random.default_rng(22)
    image = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    mask = np.zeros((200, 200), dtype=bool)
    mask[50:150, 50:150] = True
    image[mask] = 100
    quality = foreground_quality(image, mask, Settings(_env_file=None))
    assert quality.blur_score == 0
    assert quality.contrast == 0
    assert quality.usable  # Smooth coat is not a failed cat detection.


def test_reencoded_copy_does_not_count_as_a_new_observation():
    from io import BytesIO
    rng = np.random.default_rng(12)
    original = Image.fromarray(rng.integers(0, 255, (128, 128, 3), dtype=np.uint8))
    data = BytesIO()
    original.save(data, format="JPEG", quality=95)
    data.seek(0)
    a, b = row(), row()
    a.features["perceptual_hash"] = perceptual_hash(original)
    b.features["perceptual_hash"] = perceptual_hash(Image.open(data))
    assert near_duplicate(a, b)
    assert compare([a], [b])["evidence"]["distinct_photo_pairs"] == 0


def test_photo_storage_preserves_previous_content_and_windows_keys(tmp_path):
    from app.services.storage_service import PrivatePhotoStorage
    storage = PrivatePhotoStorage(tmp_path)
    user, cat = uuid4(), uuid4()
    first, _ = storage.save(user_id=user, cat_id=cat, data=b"old", suffix=".jpg")
    second, _ = storage.save(user_id=user, cat_id=cat, data=b"new", suffix=".jpg")
    assert first != second
    assert storage.open(first).read_bytes() == b"old"
    assert storage.open(second.replace("/", "\\")).read_bytes() == b"new"


def test_fine_verification_is_bounded_and_does_not_return_probability(monkeypatch):
    from app.services import recognition_service
    calls = []
    def dense(a, b):
        if a is not None:
            calls.append(1)
        return {"available": False, "similarity": None, "inliers": 0, "geometry_consistent": False}
    monkeypatch.setattr(recognition_service, "compare_dense_features", dense)
    sources, targets = [row() for _ in range(5)], [row() for _ in range(5)]
    for item in sources + targets:
        item.features["dense"] = {}
    result = compare_embeddings(sources, targets, Settings(_env_file=None, match_fine_pair_limit=3))
    assert len(calls) == 3
    assert result["evidence"]["fine_pairs_checked"] == 3
    assert result["evidence"]["probability"] is None
    assert not result["evidence"]["notification_eligible"]


def test_dense_geometry_corroborates_but_does_not_identify_solid_cat(monkeypatch):
    from app.services import recognition_service
    def dense(a, b):
        return {"available": a is not None, "similarity": 0.9 if a is not None else None,
                "inliers": 12 if a is not None else 0, "geometry_consistent": a is not None}
    monkeypatch.setattr(recognition_service, "compare_dense_features", dense)
    a, b = row(solid=True), row()
    a.features["dense"], b.features["dense"] = {}, {}
    result = compare([a], [b])
    assert result["evidence"]["dense"]["geometry_consistent"]
    assert result["restricted"]
    assert result["evidence"]["capture_guidance"]
