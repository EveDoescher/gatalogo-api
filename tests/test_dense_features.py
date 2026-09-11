import base64
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from app.services.dense_features import (DIMENSION, ENCODING, MAX_PATCHES,
    compare_dense_features, encode_dense_features)
from app.services.vision_worker import Sam2DinoWorker
from app.config import Settings


def payload(descriptors, points):
    return {"encoding": ENCODING, "count": len(descriptors), "dimension": DIMENSION,
            "points": np.asarray(points).tolist(),
            "data": base64.b64encode(np.asarray(descriptors, dtype="<f2").tobytes()).decode("ascii")}


def known_patches():
    rng = np.random.default_rng(321)
    descriptors = rng.normal(size=(20, DIMENSION)).astype(np.float32)
    descriptors /= np.linalg.norm(descriptors, axis=1, keepdims=True)
    points = rng.uniform(0.15, 0.8, (20, 2))
    return descriptors, points


def test_dense_features_exclude_background_and_letterbox():
    tokens = np.ones((37*37, DIMENSION), dtype=np.float32)
    mask = np.zeros((518, 518), dtype=bool)
    mask[140:280, 140:280] = True
    result = encode_dense_features(tokens, mask)
    assert result["count"] == 100
    assert all(10/37 <= x <= 20/37 and 10/37 <= y <= 20/37 for x, y in result["points"])
    narrow = encode_dense_features(tokens, np.ones((518, 140), dtype=bool))
    assert narrow["count"] <= MAX_PATCHES
    assert all(0.35 < x < 0.65 for x, _ in narrow["points"])


def test_empty_mask_does_not_create_dense_evidence():
    encoded = encode_dense_features(np.ones((1369, 384)), np.zeros((128, 128), dtype=bool))
    assert encoded["count"] == 0
    assert not compare_dense_features(encoded, encoded)["available"]


def test_distinct_mutual_matches_recover_known_transform():
    descriptors, points = known_patches()
    result = compare_dense_features(payload(descriptors, points), payload(descriptors, points*0.8+0.1))
    assert result["available"]
    assert result["similarity"] == pytest.approx(1, abs=1e-5)
    assert result["mutual_matches"] == 20
    assert result["geometry_consistent"]
    assert not result["calibrated"]


def test_repeated_descriptors_cannot_manufacture_distinctive_details():
    descriptors, points = known_patches()
    repeated = np.repeat(descriptors[:1], len(descriptors), axis=0)
    result = compare_dense_features(payload(repeated, points), payload(repeated, points))
    assert result["similarity"] == pytest.approx(1, abs=1e-5)
    assert result["mutual_matches"] == 0
    assert not result["geometry_consistent"]


def test_scrambled_layout_rejects_geometry_even_with_identical_descriptors():
    descriptors, points = known_patches()
    random_points = np.random.default_rng(500).uniform(0, 1, points.shape)
    result = compare_dense_features(payload(descriptors, points), payload(descriptors, random_points))
    assert not result["geometry_consistent"]


@pytest.mark.parametrize("change", [
    {"count": 193}, {"count": -1}, {"count": True}, {"dimension": 128},
    {"data": "!"*20480}, {"points": [[float("nan"), 0]]}, {"encoding": "legacy"},
])
def test_malformed_or_unbounded_payload_is_ignored(change):
    descriptors, points = known_patches()
    good = payload(descriptors, points)
    bad = deepcopy(good)
    bad.update(change)
    assert not compare_dense_features(bad, good)["available"]


def test_worker_extracts_global_and_dense_with_one_forward_pass(monkeypatch):
    torch = pytest.importorskip("torch")
    worker = Sam2DinoWorker(Settings(_env_file=None))
    worker._torch = torch
    calls = []
    class Model:
        def forward_features(self, tensor):
            calls.append(tensor.shape)
            return {"x_norm_clstoken": torch.ones(1, 384), "x_norm_patchtokens": torch.ones(1, 1369, 384)}
    worker._dino = Model()
    monkeypatch.setattr(worker, "_load_models", lambda: None)
    mask = np.ones((200, 400), dtype=bool)
    monkeypatch.setattr(worker, "_segment", lambda image: (image, 0.8, {"local": {}}, mask))
    evidence = worker.extract_evidence(Image.new("RGB", (400, 200), "gray"))
    assert len(calls) == 1
    assert np.linalg.norm(evidence["embedding"]) == pytest.approx(1)
    assert 0 < evidence["features"]["dense"]["count"] <= MAX_PATCHES
