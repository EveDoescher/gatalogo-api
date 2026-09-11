"""Avalia detector + SAM 2 com máscaras do Oxford-IIIT Pet."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.services.preprocessing_service import PreprocessingService
from app.services.segmentation_service import SegmentationService


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mede a segmentação do detector aberto + SAM 2."
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/raw/oxford-iiit-pet"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmark/manifests/oxford_iiit_pet_cats_v1.csv"),
    )
    parser.add_argument(
        "--split",
        choices=("calibration", "evaluation", "all"),
        default="evaluation",
        help="Use calibration apenas para ajustar limites; reporte evaluation.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON de resultado. Por padrão, salva em benchmark/results/.",
    )
    return parser.parse_args()


def read_manifest(path: Path, split: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"Manifesto não encontrado: {path}")
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    return rows if split == "all" else [row for row in rows if row["split"] == split]


def trimap_to_masks(trimap: np.ndarray, target_shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Converte trimap: 1=animal, 2=fundo, 3=região não classificada.

    A métrica estrita mede apenas a região de animal. A inclusiva considera
    também a região não classificada da borda, para não penalizar detalhes de
    pelo nas extremidades.
    """
    if trimap.shape != target_shape:
        trimap = cv2.resize(
            trimap,
            (target_shape[1], target_shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    strict = trimap == 1
    inclusive = trimap != 2
    return strict, inclusive


def intersection_over_union(predicted: np.ndarray, expected: np.ndarray) -> float:
    predicted = predicted.astype(bool)
    expected = expected.astype(bool)
    union = np.logical_or(predicted, expected).sum()
    return 1.0 if union == 0 else float(np.logical_and(predicted, expected).sum() / union)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return round(ordered[index], 4)


def summarize(records: list[dict[str, object]]) -> dict[str, object]:
    processed = [record for record in records if record["status"] == "segmented"]
    strict_scores = [float(record["iou_strict"]) for record in processed]
    inclusive_scores = [float(record["iou_inclusive"]) for record in processed]
    total = len(records)

    return {
        "samples": total,
        "segmented": len(processed),
        "segmentation_rate": round(len(processed) / total, 4) if total else 0.0,
        "status_counts": dict(Counter(str(record["status"]) for record in records)),
        "iou_strict": metric_summary(strict_scores),
        "iou_inclusive": metric_summary(inclusive_scores),
    }


def metric_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p10": None}
    return {
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "p10": percentile(values, 0.10),
    }


def evaluate(
    rows: list[dict[str, str]],
    dataset_root: Path,
    settings: Settings,
) -> list[dict[str, object]]:
    preprocessing = PreprocessingService(settings)
    segmentation = SegmentationService(settings)
    records: list[dict[str, object]] = []

    for position, row in enumerate(rows, start=1):
        image_path = dataset_root / row["image_path"]
        trimap_path = dataset_root / row["trimap_path"]
        record: dict[str, object] = {
            "sample_id": row["sample_id"],
            "breed": row["breed"],
            "split": row["split"],
        }
        try:
            prepared = preprocessing.prepare(image_path.read_bytes())
            trimap = cv2.imread(str(trimap_path), cv2.IMREAD_GRAYSCALE)
            if trimap is None:
                raise ValueError(f"Trimap inválido ou ausente: {trimap_path}")

            result = segmentation.segment_cat(prepared.vision_image)
            if result.cat_count == 0:
                record["status"] = "not_detected"
            elif result.ambiguous_multiple_cats:
                record["status"] = "ambiguous_multiple_cats"
            elif result.mask is None:
                record["status"] = "empty_mask"
            else:
                strict, inclusive = trimap_to_masks(
                    trimap,
                    prepared.vision_image.shape[:2],
                )
                record.update(
                    status="segmented",
                    detector_confidence=result.detector_confidence,
                    iou_strict=round(intersection_over_union(result.mask > 0, strict), 4),
                    iou_inclusive=round(intersection_over_union(result.mask > 0, inclusive), 4),
                )
        except Exception as error:  # Relatório deve continuar após uma falha individual.
            record.update(status="error", error=str(error))

        records.append(record)
        print(f"[{position}/{len(rows)}] {row['sample_id']}: {record['status']}")
    return records


def main() -> None:
    args = parse_arguments()
    rows = read_manifest(args.manifest, args.split)
    if not rows:
        raise SystemExit("O recorte escolhido não contém amostras.")

    settings = Settings()
    records = evaluate(rows, args.dataset_root, settings)
    report = {
        "benchmark": "Oxford-IIIT Pet cats v1",
        "split": args.split,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "detector_confidence": settings.detector_confidence,
            "secondary_cat_max_area_ratio": settings.secondary_cat_max_area_ratio,
            "vision_max_side": settings.vision_max_side,
            "sam2_checkpoint": settings.sam2_checkpoint,
        },
        "summary": summarize(records),
        "records": records,
    }
    output = args.output or Path(
        "benchmark/results/oxford_iiit_pet_" f"{args.split}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Relatório salvo em {output}")


if __name__ == "__main__":
    main()
