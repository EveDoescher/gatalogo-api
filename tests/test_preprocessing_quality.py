from io import BytesIO
import unittest

from PIL import Image

from app.config import Settings
from app.exceptions import InvalidImageError
from app.services.analysis_service import AnalysisService
from app.services.preprocessing_service import PreprocessingService
from app.services.quality_service import QualityService
from app.models.vision import VisionColor


def jpeg_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", size, color)
    encoded = BytesIO()
    image.save(encoded, format="JPEG")
    return encoded.getvalue()


class PreprocessingAndQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            _env_file=None,
            vision_max_side=1280,
            gemini_max_side=1024,
        )
        self.preprocessing = PreprocessingService(self.settings)
        self.quality = QualityService(self.settings)

    def test_prepares_bounded_image_for_local_vision(self) -> None:
        prepared = self.preprocessing.prepare(
            jpeg_bytes((2400, 1200), (125, 90, 60))
        )

        self.assertEqual(
            (prepared.original_width, prepared.original_height),
            (2400, 1200),
        )
        self.assertEqual(prepared.vision_image.shape[:2], (640, 1280))

    def test_rejects_small_images(self) -> None:
        with self.assertRaises(InvalidImageError):
            self.preprocessing.prepare(jpeg_bytes((127, 128), (255, 255, 255)))

    def test_marks_completely_dark_and_flat_image_as_unusable(self) -> None:
        prepared = self.preprocessing.prepare(
            jpeg_bytes((300, 300), (0, 0, 0))
        )

        metrics = self.quality.analyze(prepared.vision_image)

        self.assertFalse(metrics.usable)
        self.assertEqual(metrics.underexposed_ratio, 1.0)

    def test_encodes_a_bounded_crop_for_gemini(self) -> None:
        image = self.preprocessing.prepare(
            jpeg_bytes((1600, 900), (125, 90, 60))
        ).vision_image
        encoded = AnalysisService(self.settings)._encode_gemini_image(image)

        with Image.open(BytesIO(encoded)) as crop:
            self.assertEqual(crop.size, (1024, 576))

    def test_local_gate_classifies_an_obvious_solid_coat(self) -> None:
        service = AnalysisService(
            Settings(
                _env_file=None,
                local_solid_dominance_threshold=0.96,
                local_solid_min_detector_confidence=0.85,
            )
        )

        result = service._try_local_semantic_shortcut(
            [VisionColor(hex="#242424", percentage=97.0)],
            detector_confidence=0.94,
            cat_count=1,
            segmentation_warnings=(),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.coat_type, "Sólido")
        self.assertEqual(result.pattern_map.base_color, "Preto")

    def test_marks_dns_failures_as_retryable(self) -> None:
        self.assertTrue(
            AnalysisService._is_retryable_gemini_error(
                RuntimeError("ConnectError: getaddrinfo failed")
            )
        )


if __name__ == "__main__":
    unittest.main()
