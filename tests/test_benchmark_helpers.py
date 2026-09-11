import unittest
from pathlib import Path

import numpy as np

from app.config import Settings
from scripts.evaluate_oxford_pet_segmentation import (
    intersection_over_union,
    summarize,
    trimap_to_masks,
)
from scripts.prepare_oxford_pet_benchmark import build_manifest_rows


class BenchmarkHelperTests(unittest.TestCase):
    def test_model_paths_are_resolved_from_project_root(self) -> None:
        settings = Settings(_env_file=None)

        self.assertTrue(Path(settings.model_cache_dir).is_absolute())
        self.assertTrue(Path(settings.sam2_checkpoint).is_absolute())
        self.assertEqual(Path(settings.model_cache_dir).name, ".model-cache")

    def test_manifest_is_balanced_between_calibration_and_evaluation(self) -> None:
        groups = {
            "Breed_A": [f"Breed_A_{number}" for number in range(1, 13)],
            "Breed_B": [f"Breed_B_{number}" for number in range(1, 13)],
        }

        rows = build_manifest_rows(
            groups,
            per_breed=12,
            calibration_per_breed=8,
            seed=20260826,
        )

        self.assertEqual(len(rows), 24)
        self.assertEqual(sum(row["split"] == "calibration" for row in rows), 16)
        self.assertEqual(sum(row["split"] == "evaluation" for row in rows), 8)
        self.assertTrue(all(row["expected_is_cat"] == "true" for row in rows))
        self.assertTrue(all(row["trimap_path"].endswith(".png") for row in rows))

    def test_trimap_masks_and_iou_handle_the_uncertain_border(self) -> None:
        trimap = np.array([[1, 3, 2], [1, 3, 2]], dtype=np.uint8)
        strict, inclusive = trimap_to_masks(trimap, (2, 3))
        predicted = np.array([[1, 1, 0], [1, 0, 0]], dtype=bool)

        self.assertEqual(strict.tolist(), [[True, False, False], [True, False, False]])
        self.assertEqual(inclusive.tolist(), [[True, True, False], [True, True, False]])
        self.assertAlmostEqual(intersection_over_union(predicted, strict), 2 / 3)
        self.assertAlmostEqual(intersection_over_union(predicted, inclusive), 3 / 4)

    def test_summary_keeps_detection_failures_out_of_iou_average(self) -> None:
        summary = summarize(
            [
                {"status": "segmented", "iou_strict": 0.8, "iou_inclusive": 0.9},
                {"status": "segmented", "iou_strict": 0.6, "iou_inclusive": 0.7},
                {"status": "not_detected"},
            ]
        )

        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["segmented"], 2)
        self.assertEqual(summary["segmentation_rate"], 0.6667)
        self.assertEqual(summary["iou_strict"]["mean"], 0.7)
        self.assertEqual(summary["status_counts"], {"segmented": 2, "not_detected": 1})


if __name__ == "__main__":
    unittest.main()
