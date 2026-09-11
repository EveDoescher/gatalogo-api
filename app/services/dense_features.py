"""Bounded DINOv2 patch evidence, restricted to the segmented animal.

Patch similarity is an uncalibrated visual signal, not an identity probability.
The compact payload is internal; only aggregate signals go to API clients.
"""
from __future__ import annotations

import base64
import binascii

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_PATCHES = 192
DIMENSION = 384
ENCODING = "dino-patches-f16-v1"


def encode_dense_features(tokens: np.ndarray, mask: np.ndarray) -> dict:
    tokens = np.asarray(tokens, dtype=np.float32)
    grid = 37  # DINOv2 ViT-S/14 on the existing 518 x 518 input.
    if tokens.shape != (grid * grid, DIMENSION) or not np.isfinite(tokens).all():
        raise ValueError("DINOv2 retornou patches inválidos.")
    if mask.ndim != 2 or min(mask.shape) == 0:
        raise ValueError("Máscara inválida para os patches.")
    # Same contain/padding geometry as the RGB tensor, with binary sampling.
    padded = ImageOps.pad(Image.fromarray(mask.astype(np.uint8) * 255), (518, 518),
                         method=Image.Resampling.NEAREST, color=0)
    pixels = np.asarray(padded, dtype=np.float32) / 255
    coverage = pixels.reshape(grid, 14, grid, 14).mean(axis=(1, 3)).ravel()
    indices = np.flatnonzero(coverage >= 0.9)
    if len(indices) > MAX_PATCHES:
        # Deterministic spatial sampling, never learned attention without labels.
        indices = indices[np.linspace(0, len(indices)-1, MAX_PATCHES, dtype=int)]
    selected = tokens[indices]
    norms = np.linalg.norm(selected, axis=1, keepdims=True)
    valid = norms.ravel() > 1e-12
    indices, selected, norms = indices[valid], selected[valid], norms[valid]
    selected = selected / norms
    points = np.column_stack(((indices % grid + 0.5) / grid, (indices // grid + 0.5) / grid))
    return {"encoding": ENCODING, "count": len(indices), "dimension": DIMENSION,
            "points": points.round(6).tolist(),
            "data": base64.b64encode(selected.astype("<f2").tobytes()).decode("ascii")}


def _decode(payload: dict | None):
    if not isinstance(payload, dict) or payload.get("encoding") != ENCODING:
        return None
    count = payload.get("count")
    if type(count) is not int or not 1 <= count <= MAX_PATCHES or payload.get("dimension") != DIMENSION:
        return None
    data = payload.get("data")
    expected_length = count * DIMENSION * 2
    if not isinstance(data, str) or len(data) != 4 * ((expected_length + 2) // 3):
        return None
    try:
        raw = base64.b64decode(data, validate=True)
        if len(raw) != expected_length:
            return None
        descriptors = np.frombuffer(raw, dtype="<f2").astype(np.float32).reshape(count, DIMENSION)
        points = np.asarray(payload.get("points"), dtype=np.float32)
        if (points.shape != (count, 2) or not np.isfinite(points).all()
                or not np.isfinite(descriptors).all() or np.any(points < 0) or np.any(points > 1)):
            return None
        norms = np.linalg.norm(descriptors, axis=1, keepdims=True)
        if np.any(norms <= 1e-12):
            return None
        return descriptors / norms, points
    except (ValueError, TypeError, binascii.Error):
        return None


def compare_dense_features(left: dict | None, right: dict | None) -> dict:
    result = {"available": False, "similarity": None, "mutual_matches": 0,
              "inliers": 0, "coverage": 0.0, "geometry_consistent": False,
              "calibrated": False}
    a, b = _decode(left), _decode(right)
    if a is None or b is None:
        return result
    da, pa = a
    db, pb = b
    scores = np.clip(da @ db.T, -1.0, 1.0)
    result.update(available=True, similarity=float((scores.max(axis=1).mean() + scores.max(axis=0).mean()) / 2))
    if min(len(da), len(db)) < 8:
        return result
    forward, backward = scores.argmax(axis=1), scores.argmax(axis=0)
    second_a = np.partition(scores, -2, axis=1)[:, -2]
    second_b = np.partition(scores, -2, axis=0)[-2]
    pairs = [(i, int(j)) for i, j in enumerate(forward)
             if backward[j] == i and scores[i, j] >= 0.65
             and scores[i, j] - second_a[i] >= 0.05
             and scores[i, j] - second_b[j] >= 0.05]
    result["mutual_matches"] = len(pairs)
    if len(pairs) < 8:
        return result
    source = np.asarray([pa[i] for i, _ in pairs], dtype=np.float32)
    target = np.asarray([pb[j] for _, j in pairs], dtype=np.float32)
    try:
        _, inliers = cv2.findHomography(source, target, cv2.RANSAC, 0.04)
        if inliers is None:
            return result
        accepted = inliers.ravel().astype(bool)
        count = int(accepted.sum())
        coverage = min(float(cv2.contourArea(cv2.convexHull(points[accepted])))
                       for points in (source, target)) if count >= 3 else 0.0
        result.update(inliers=count, coverage=round(coverage, 4),
                      geometry_consistent=count >= 8 and count / len(pairs) >= 0.6 and coverage >= 0.03)
    except cv2.error:
        pass
    return result
