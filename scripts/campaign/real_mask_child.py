"""Small preselected Oxford species/trimap ground truth; no semantic provider."""

import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, config["source"])
from app.config import Settings
from app.services.preprocessing_service import PreprocessingService
from app.services.quality_service import QualityService
from app.services.segmentation_service import SegmentationService
from measurements import resources
import cv2
import numpy as np
from PIL import Image
import torch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root, folder = Path(config["root"]), Path(config["output"])
    folder.mkdir(exist_ok=False)
    data = root / "data/raw/oxford-iiit-pet"
    annotation = data / "annotations/list.txt"
    rows = [line.split() for line in annotation.read_text().splitlines() if line and not line.startswith("#")]
    selected = []
    for species, count in (("1", 6), ("2", 3)):
        breeds = {}
        for row in sorted(rows):
            if row[2] == species:
                breeds.setdefault(row[0].rsplit("_", 1)[0], row[0])
        selected.extend((name, species) for name in list(breeds.values())[:count])
    assert len(selected) == 9
    inputs = [{"image": name, "species": species,
        "image_sha256": sha(data / "images" / (name+".jpg")),
        "trimap_sha256": sha(data / "annotations/trimaps" / (name+".png"))} for name, species in selected]
    (folder / "dataset.json").write_text(json.dumps({"selection": "Sorted first image per breed; first six cat and first three dog breeds. Fixed before inference; convenience diagnostic sample, no population accuracy claim.",
        "annotation_sha256": sha(annotation), "inputs": inputs}, indent=2)+"\n", encoding="utf-8")
    torch.set_num_threads(2)
    def denied(*args, **kwargs):
        raise AssertionError("External model downloads prohibited")
    torch.hub.download_url_to_file = denied
    urllib.request.urlopen = denied
    torch.hub.urlopen = denied
    settings = Settings(_env_file=root / ".env", model_cache_dir=str(root / ".model-cache"),
        sam2_checkpoint=str(root / ".model-cache/sam2.1_hiera_tiny.pt"),
        analysis_cache_dir=str(folder / "cache"), vision_device="cpu").model_copy(update={"gemini_api_key": None})
    preprocessing, quality, segmenter = PreprocessingService(settings), QualityService(settings), SegmentationService(settings)
    observations = []
    for reference in inputs:
        name = reference["image"]
        image_path, trimap_path = data / "images" / (name+".jpg"), data / "annotations/trimaps" / (name+".png")
        with Image.open(image_path) as original:
            assert original.getexif().get(274, 1) == 1, "Align rotated ground truth explicitly before measurement"
        started, before = time.perf_counter(), resources()
        prepared = preprocessing.prepare(image_path.read_bytes())
        assessment = quality.analyze(prepared.vision_image)
        result = segmenter.segment_cat(prepared.vision_image)
        row = {"image": name, "expected_cat": reference["species"] == "1", "detected_cat": result.cat_count > 0,
            "cat_count": result.cat_count, "ambiguous_multiple_cats": result.ambiguous_multiple_cats,
            "quality_usable": assessment.usable, "detector_confidence": float(result.detector_confidence or 0),
            "elapsed_seconds": time.perf_counter()-started, "resources_before": before, "resources_after": resources()}
        if row["expected_cat"]:
            trimap = cv2.imread(str(trimap_path), cv2.IMREAD_GRAYSCALE)
            assert trimap is not None and set(np.unique(trimap)).issubset({1, 2, 3})
            height, width = prepared.vision_image.shape[:2]
            truth = cv2.resize(trimap, (width, height), interpolation=cv2.INTER_NEAREST)
            valid, foreground = truth != 3, truth == 1
            predicted = np.zeros((height, width), dtype=bool) if result.mask is None else result.mask > 0
            assert predicted.shape == foreground.shape
            intersection = int(np.count_nonzero(predicted & foreground & valid))
            union = int(np.count_nonzero((predicted | foreground) & valid))
            row.update(mask_available=result.mask is not None, foreground_iou=intersection/union if union else None,
                intersection_pixels=intersection, union_pixels=union,
                boundary_pixels_excluded=int(np.count_nonzero(~valid)))
        observations.append(row)
        (folder / "observations.json").write_text(json.dumps(observations, indent=2)+"\n", encoding="utf-8")
        assert sha(image_path) == reference["image_sha256"] and sha(trimap_path) == reference["trimap_sha256"]
        print(json.dumps({"role": config["role"], "image": name, "detected_cat": row["detected_cat"]}), flush=True)
    (folder / "summary.json").write_text(json.dumps({"executed": True, "samples": len(observations),
        "source_sha": config["source_sha"], "paid_calls": 0,
        "settings": {key: getattr(settings, key) for key in ("vision_max_side", "detector_confidence", "detector_fallback_confidence", "sam2_config")},
        "scope": "Detection/species and foreground trimap IoU only. Unknown boundary label 3 excluded; GT nearest-resized to actual vision dimensions. Missing foreground mask scores zero on cat GT. Raw segmenter exercised even when quality gate would reject; gate state recorded separately. No coat/identity labels inferred from breed; no paid inference."}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
