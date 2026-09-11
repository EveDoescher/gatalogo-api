"""Versioned, local visual evidence. Scores are not identity probabilities."""

from __future__ import annotations

import cv2
import hashlib
import numpy as np
from PIL import Image, ImageOps
from app.models.vision import ImageQualityMetrics

PIPELINE_VERSION = "sam2-dinov2-letterbox-dense-v3"


def model_version(settings) -> str:
    # Changing extraction settings invalidates old vectors instead of silently
    # comparing embeddings from different processing pipelines.
    parameters = (settings.dinov2_model_name, settings.vision_max_side,
                  settings.detector_confidence, settings.detector_fallback_confidence,
                  settings.secondary_cat_max_area_ratio, settings.cat_crop_padding_ratio,
                  settings.sam2_config)
    fingerprint = hashlib.sha256(repr(parameters).encode()).hexdigest()[:12]
    return f"{PIPELINE_VERSION}:{settings.dinov2_model_name}:{fingerprint}"


def foreground_quality(image_bgr: np.ndarray, mask: np.ndarray, settings) -> ImageQualityMetrics:
    """Measure the animal, excluding scene detail and segmentation borders."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    interior = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    if int(interior.sum()) < 64:
        raise ValueError("Recorte do gato sem pixels suficientes para reconhecimento.")
    values = gray[interior]
    blur = float(cv2.Laplacian(gray, cv2.CV_64F)[interior].var())
    contrast = float(values.std())
    under, over = float((values <= 15).mean()), float((values >= 240).mean())
    unusable = (max(under, over) >= settings.max_extreme_exposure_ratio
                and blur < settings.min_blur_score and contrast < settings.min_contrast)
    return ImageQualityMetrics(width=gray.shape[1], height=gray.shape[0],
        blur_score=blur, contrast=contrast, brightness=float(values.mean()),
        underexposed_ratio=under, overexposed_ratio=over, usable=not unusable)


def prepare_embedding_image(image: Image.Image) -> Image.Image:
    # Fit used to crop off ears, paws and tails on elongated body crops.
    return ImageOps.pad(image.convert("RGB"), (518, 518),
                        method=Image.Resampling.BICUBIC, color=(255, 255, 255))


def perceptual_hash(image: Image.Image) -> str:
    gray = np.asarray(image.convert("L").resize((32, 32)), dtype=np.float32)
    block = cv2.dct(gray)[:8, :8].ravel()
    bits = block > np.median(block[1:])
    bits[0] = False
    return f"{sum(int(bit) << index for index, bit in enumerate(bits)):016x}"


def near_duplicate(a, b) -> bool:
    if a.photo_hash and a.photo_hash == b.photo_hash:
        return True
    left, right = (a.features or {}).get("perceptual_hash"), (b.features or {}).get("perceptual_hash")
    if not isinstance(left, str) or not isinstance(right, str) or len(left) != 16 or len(right) != 16:
        return False
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count() <= 4
    except ValueError:
        return False


def local_features(image_bgr: np.ndarray, mask: np.ndarray) -> dict:
    """RootSIFT only inside the cat; exclude mask borders and background."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    interior = cv2.erode(mask.astype(np.uint8), np.ones((9, 9), np.uint8))
    keypoints, descriptors = cv2.SIFT_create(nfeatures=256).detectAndCompute(gray, interior * 255)
    if descriptors is None:
        return {"points": [], "descriptors": []}
    # Reject features whose descriptor support reaches outside the foreground.
    distances = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    indices = [i for i, point in enumerate(keypoints)
               if distances[min(int(point.pt[1]), gray.shape[0]-1),
                            min(int(point.pt[0]), gray.shape[1]-1)] > point.size * 3]
    if not indices:
        return {"points": [], "descriptors": []}
    descriptors = descriptors[indices]
    descriptors = np.sqrt(descriptors / np.maximum(descriptors.sum(axis=1, keepdims=True), 1e-12))
    height, width = gray.shape
    return {"points": [[keypoints[i].pt[0] / width, keypoints[i].pt[1] / height] for i in indices],
            "descriptors": descriptors.round(6).tolist()}


def verify_local_features(left: dict | None, right: dict | None) -> dict:
    """Geometric corroboration, never proof that two cats are the same.

    Lack of matches is inconclusive: pose, smooth coats and lighting can remove
    keypoints. Mutual ratio matching and spatial coverage limit repeated fur.
    """
    result = {"verified": False, "inliers": 0, "matches": 0, "coverage": 0.0}
    try:
        a = np.asarray((left or {}).get("descriptors", []), dtype=np.float32)
        b = np.asarray((right or {}).get("descriptors", []), dtype=np.float32)
        pa = np.asarray((left or {}).get("points", []), dtype=np.float32)
        pb = np.asarray((right or {}).get("points", []), dtype=np.float32)
        if (a.ndim != 2 or b.ndim != 2 or a.shape[1] != 128 or b.shape[1] != 128
                or len(a) < 8 or len(b) < 8 or pa.shape != (len(a), 2) or pb.shape != (len(b), 2)
                or not all(np.isfinite(x).all() for x in (a, b, pa, pb))):
            return result
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        def ratio_matches(source, target):
            return {m.queryIdx: m.trainIdx for pair in matcher.knnMatch(source, target, k=2)
                    if len(pair) == 2 for m, n in [pair] if m.distance < 0.7 * n.distance}
        forward, backward = ratio_matches(a, b), ratio_matches(b, a)
        pairs = [(i, j) for i, j in forward.items() if backward.get(j) == i]
        result["matches"] = len(pairs)
        if len(pairs) < 8:
            return result
        source = np.asarray([pa[i] for i, _ in pairs])
        target = np.asarray([pb[j] for _, j in pairs])
        _, inliers = cv2.findHomography(source, target, cv2.RANSAC, 0.025)
        if inliers is None:
            return result
        accepted = inliers.ravel().astype(bool)
        count = int(accepted.sum())
        coverage = min(float(cv2.contourArea(cv2.convexHull(points[accepted])))
                       for points in (source, target)) if count >= 3 else 0.0
        result.update(inliers=count, coverage=round(coverage, 4),
                      verified=count >= 8 and count / len(pairs) >= 0.6 and coverage >= 0.03)
    except (ValueError, TypeError, cv2.error):
        pass
    return result
