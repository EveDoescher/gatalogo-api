"""Executa a API nas fotos privadas de calibração e compara com a revisão humana."""

from __future__ import annotations

import argparse
import asyncio
import json
import unicodedata
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.config import Settings
from app.services.analysis_service import AnalysisService


PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLOR_NAMES = ("Preto", "Branco", "Cinza", "Laranja", "Marrom", "Creme", "Outro")
COAT_TYPES = (
    "Sólido",
    "Bicolor",
    "Tricolor",
    "Rajado",
    "Tuxedo",
    "Escaminha",
    "Colorpoint",
    "Outro",
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa a análise nas fotos autorais de calibração."
    )
    parser.add_argument(
        "--references",
        type=Path,
        default=PROJECT_ROOT / "data/own-cats/calibration_references.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data/own-cats/calibration_results.json",
    )
    parser.add_argument(
        "--masks-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs/own-cats-benchmark/masks",
    )
    parser.add_argument(
        "--sample-id",
        action="append",
        dest="sample_ids",
        help="Reprocessa somente o sample_id indicado; pode ser repetido.",
    )
    return parser.parse_args()


def normalized(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFD", value.lower())
        if unicodedata.category(character) != "Mn"
    )


def canonical(value: str, options: tuple[str, ...]) -> str | None:
    target = normalized(value.strip())
    return next((option for option in options if normalized(option) == target), None)


def expected_color_set(reference: dict[str, Any]) -> set[str]:
    values = [str(reference.get("expected_primary_color", ""))]
    values.extend(str(reference.get("expected_colors", "")).replace("/", ",").split(","))
    matches: set[str] = set()
    normalized_text = " ".join(values)
    for color in COLOR_NAMES:
        if normalized(color) in normalized(normalized_text):
            matches.add(color)
    return matches


def overlay_mask(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    mask_area = mask > 0
    colored = image.copy()
    colored[mask_area] = (0, 180, 0)
    overlay = cv2.addWeighted(image, 0.60, colored, 0.40, 0)
    contours, _ = cv2.findContours(
        mask.astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    cv2.drawContours(overlay, contours, -1, (0, 255, 255), 2)
    return overlay


def build_comparison(
    reference: dict[str, Any],
    response: Any,
) -> dict[str, Any]:
    expected_primary = canonical(
        str(reference["expected_primary_color"]),
        COLOR_NAMES,
    )
    expected_pattern = canonical(
        str(reference["expected_coat_type"]),
        COAT_TYPES,
    )
    predicted_colors = {color.name for color in response.colors}
    expected_colors = expected_color_set(reference)
    primary_match = expected_primary == response.primary_color
    pattern_match = expected_pattern == response.coat_type
    colors_match = expected_colors == predicted_colors
    return {
        "expected_primary_color": expected_primary,
        "expected_colors": sorted(expected_colors),
        "expected_coat_type": expected_pattern,
        "primary_match": primary_match,
        "colors_match": colors_match,
        "pattern_match": pattern_match,
        "overall_match": primary_match and colors_match and pattern_match,
    }


async def run() -> None:
    args = parse_arguments()
    references: list[dict[str, Any]] = json.loads(args.references.read_text(encoding="utf-8"))
    if args.sample_ids:
        selected = set(args.sample_ids)
        references = [
            reference
            for reference in references
            if reference["sample_id"] in selected
        ]
    if not references:
        raise SystemExit("Nenhuma referência de calibração encontrada.")

    settings = Settings()
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY não está configurada no .env do projeto.")

    service = AnalysisService(settings)
    args.masks_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index, reference in enumerate(references, start=1):
        image_path = PROJECT_ROOT / reference["image_path"]
        item: dict[str, Any] = {"reference": reference}
        try:
            image_bytes = image_path.read_bytes()
            response = await service.analyze(
                cat_id=reference["sample_id"],
                image_bytes=image_bytes,
            )
            item["api"] = response.model_dump(mode="json")
            item["comparison"] = build_comparison(reference, response)

            prepared = service.preprocessing.prepare(image_bytes)
            segmentation = service.segmentation.segment_cat(prepared.vision_image)
            if segmentation.mask is not None:
                preview = overlay_mask(prepared.vision_image, segmentation.mask)
                mask_path = args.masks_dir / f"{reference['sample_id']}.jpg"
                if not cv2.imwrite(str(mask_path), preview):
                    raise RuntimeError("Não foi possível salvar a prévia da máscara.")
                item["mask_preview"] = str(mask_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            item["status"] = "ok"
            print(f"[{index}/{len(references)}] {reference['sample_id']}: ok", flush=True)
        except Exception as error:
            item.update(status="error", error=str(error))
            print(f"[{index}/{len(references)}] {reference['sample_id']}: erro", flush=True)
        results.append(item)

    successful = [item for item in results if item["status"] == "ok"]
    comparisons = [item["comparison"] for item in successful]
    summary = {
        "total": len(results),
        "successful": len(successful),
        "errors": len(results) - len(successful),
        "primary_match_rate": sum(item["primary_match"] for item in comparisons) / len(comparisons) if comparisons else None,
        "colors_match_rate": sum(item["colors_match"] for item in comparisons) / len(comparisons) if comparisons else None,
        "pattern_match_rate": sum(item["pattern_match"] for item in comparisons) / len(comparisons) if comparisons else None,
        "overall_match_rate": sum(item["overall_match"] for item in comparisons) / len(comparisons) if comparisons else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
