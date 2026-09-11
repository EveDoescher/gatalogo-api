import unittest

import numpy as np

from app.config import Settings
from app.models.analysis import GeminiCoatAnalysis
from app.models.vision import VisionColor
from app.services.color_service import ColorService
from app.services.fusion_service import FusionService


class ColorAndFusionTests(unittest.TestCase):
    def test_extracts_colors_only_inside_the_cat_mask(self) -> None:
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:, :50] = (0, 128, 255)  # laranja em BGR
        image[:, 50:] = (255, 255, 255)
        image[80:, :] = (255, 0, 0)  # fundo azul que deve ser ignorado
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[:80, :] = 255

        colors = ColorService(
            Settings(
                _env_file=None,
                color_cluster_count=2,
                color_min_coverage=0.01,
                color_merge_distance=5,
            )
        ).extract(image, mask)

        self.assertEqual(len(colors), 2)
        self.assertAlmostEqual(sum(color.percentage for color in colors), 100.0)
        self.assertTrue(any(color.hex == "#FF8000" for color in colors))
        self.assertTrue(any(color.hex == "#FFFFFF" for color in colors))
        self.assertFalse(any(color.hex == "#0000FF" for color in colors))

    def test_fusion_uses_measured_percentages_and_merges_same_name(self) -> None:
        semantic = GeminiCoatAnalysis.model_validate(
            {
                "validation": {
                    "accepted": True,
                    "reason": "ACCEPTED",
                    "cat_count": 1,
                    "quality": "GOOD",
                    "message": "ok",
                },
                "coat_type": "Bicolor",
                "color_assignments": [
                    {"cluster_index": 0, "name": "Laranja"},
                    {"cluster_index": 1, "name": "Laranja"},
                    {"cluster_index": 2, "name": "Branco"},
                ],
                "confidence": 0.9,
                "pattern_map": {
                    "base_color": "Laranja",
                    "pattern": "Bicolor",
                },
            }
        )

        response = FusionService().combine(
            cat_id="cat-1",
            measured_colors=[
                VisionColor(hex="#E07030", percentage=45.0),
                VisionColor(hex="#9B4A1D", percentage=15.0),
                VisionColor(hex="#FAFAF8", percentage=40.0),
            ],
            semantic=semantic,
        )

        self.assertEqual(response.primary_color, "Laranja")
        self.assertEqual(
            [(color.name, color.percentage) for color in response.colors],
            [("Laranja", 60.0), ("Branco", 40.0)],
        )

    def test_groups_white_shadows_without_merging_black(self) -> None:
        service = ColorService(
            Settings(
                _env_file=None,
                color_shade_chroma_distance=14,
                color_black_l_threshold=45,
            )
        )
        # Mesmo a/b (neutro), mas L diferente: branco sob luz/sombra.
        centers = np.asarray(
            [
                [208.0, 128.0, 128.0],
                [145.0, 129.0, 127.0],
                [80.0, 127.0, 129.0],
                [18.0, 128.0, 128.0],
            ]
        )
        colors = service._to_vision_colors(
            centers,
            np.asarray([40, 30, 20, 10]),
        )

        self.assertEqual(colors[0].shade_group, colors[1].shade_group)
        self.assertEqual(colors[1].shade_group, colors[2].shade_group)
        self.assertNotEqual(colors[2].shade_group, colors[3].shade_group)


if __name__ == "__main__":
    unittest.main()
