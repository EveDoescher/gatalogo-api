import unittest

import numpy as np

from app.config import Settings
from app.services.segmentation_service import (
    CatDetection,
    SegmentationService,
)


class FakeDetector:
    def __init__(self, detections: list[CatDetection]) -> None:
        self.detections = detections

    def detect(self, _image: np.ndarray) -> list[CatDetection]:
        return self.detections


class FakeRefiner:
    def __init__(self, mask: np.ndarray) -> None:
        self.mask = mask
        self.received_bbox: tuple[int, int, int, int] | None = None

    def refine(
        self,
        _image: np.ndarray,
        bbox: tuple[int, int, int, int],
    ) -> np.ndarray:
        self.received_bbox = bbox
        return self.mask


class SegmentationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None)
        self.image = np.zeros((100, 200, 3), dtype=np.uint8)

    def test_refines_the_primary_detection_with_sam_mask(self) -> None:
        mask = np.zeros((100, 200), dtype=np.uint8)
        mask[10:80, 20:160] = 1
        refiner = FakeRefiner(mask)
        service = SegmentationService(
            self.settings,
            detector=FakeDetector([CatDetection((20, 10, 160, 80), 0.93)]),
            refiner=refiner,
        )

        result = service.segment_cat(self.image)

        self.assertEqual(result.cat_count, 1)
        self.assertEqual(result.bbox, (20, 10, 160, 80))
        self.assertEqual(result.detector_confidence, 0.93)
        self.assertEqual(refiner.received_bbox, (20, 10, 160, 80))
        self.assertFalse(result.ambiguous_multiple_cats)
        self.assertEqual(result.mask.dtype, np.uint8)
        self.assertEqual(int(result.mask.max()), 255)

    def test_rejects_ambiguous_multiple_cats_without_refining(self) -> None:
        refiner = FakeRefiner(np.ones((100, 200), dtype=np.uint8))
        service = SegmentationService(
            self.settings,
            detector=FakeDetector(
                [
                    CatDetection((10, 10, 50, 50), 0.9),
                    CatDetection((120, 50, 160, 90), 0.8),
                ]
            ),
            refiner=refiner,
        )

        result = service.segment_cat(self.image)

        self.assertEqual(result.cat_count, 2)
        self.assertTrue(result.ambiguous_multiple_cats)
        self.assertIsNone(refiner.received_bbox)

    def test_rejects_a_prominent_second_cat(self) -> None:
        refiner = FakeRefiner(np.ones((100, 200), dtype=np.uint8))
        service = SegmentationService(
            self.settings,
            detector=FakeDetector(
                [
                    CatDetection((10, 0, 110, 100), 0.99),
                    CatDetection((130, 0, 192, 63), 0.67),
                ]
            ),
            refiner=refiner,
        )

        result = service.segment_cat(self.image)

        self.assertEqual(result.cat_count, 2)
        self.assertTrue(result.ambiguous_multiple_cats)
        self.assertIsNone(refiner.received_bbox)

    def test_returns_empty_result_when_detector_finds_no_cat(self) -> None:
        service = SegmentationService(
            self.settings,
            detector=FakeDetector([]),
            refiner=FakeRefiner(np.ones((100, 200), dtype=np.uint8)),
        )

        result = service.segment_cat(self.image)

        self.assertEqual(result.cat_count, 0)
        self.assertIsNone(result.mask)

    def test_retries_with_fallback_detector_when_primary_finds_nothing(self) -> None:
        mask = np.zeros((100, 200), dtype=np.uint8)
        mask[10:80, 20:160] = 1
        refiner = FakeRefiner(mask)
        service = SegmentationService(
            self.settings,
            detector=FakeDetector([]),
            fallback_detector=FakeDetector(
                [CatDetection((20, 10, 160, 80), 0.4761)]
            ),
            refiner=refiner,
        )

        result = service.segment_cat(self.image)

        self.assertEqual(result.cat_count, 1)
        self.assertTrue(result.detector_fallback_used)
        self.assertEqual(result.detector_confidence, 0.4761)
        self.assertEqual(len(result.warnings), 2)
        self.assertIsNotNone(result.mask)

    def test_warns_about_a_smaller_secondary_detection(self) -> None:
        mask = np.ones((100, 200), dtype=np.uint8)
        service = SegmentationService(
            self.settings,
            detector=FakeDetector(
                [
                    CatDetection((10, 10, 180, 90), 0.9),
                    CatDetection((150, 20, 190, 55), 0.7),
                ]
            ),
            refiner=FakeRefiner(mask),
        )

        result = service.segment_cat(self.image)

        self.assertFalse(result.ambiguous_multiple_cats)
        self.assertTrue(any("segunda detecção" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
