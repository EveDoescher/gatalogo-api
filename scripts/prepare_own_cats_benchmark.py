"""Gera o manifesto privado de fotos autorais para validação humana.

Fotos capturadas em sequência curta permanecem no mesmo split para reduzir o
vazamento entre calibração e avaliação. O script não envia imagens à rede e
não altera os arquivos originais.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
from datetime import datetime
from pathlib import Path


CAMERA_FILENAME = re.compile(r"^IMG_(\d{8})_(\d{6})$")
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cria um manifesto privado de fotos autorais de gatos."
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=Path("data/own-cats/images"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/own-cats/own_cats_v1.csv"),
    )
    parser.add_argument(
        "--evaluation-count",
        type=int,
        default=10,
    )
    parser.add_argument("--seed", type=int, default=20260826)
    return parser.parse_args()


def capture_time(path: Path) -> datetime | None:
    match = CAMERA_FILENAME.match(path.stem)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")


def group_similar_captures(files: list[Path]) -> list[list[Path]]:
    """Agrupa capturas separadas por no máximo cinco minutos."""
    groups: list[list[Path]] = []
    current: list[Path] = []
    previous_time: datetime | None = None
    for path in files:
        current_time = capture_time(path)
        close_to_previous = (
            current_time is not None
            and previous_time is not None
            and (current_time - previous_time).total_seconds() <= 5 * 60
        )
        if current and not close_to_previous:
            groups.append(current)
            current = []
        current.append(path)
        previous_time = current_time
    if current:
        groups.append(current)
    return groups


def split_groups(
    groups: list[list[Path]],
    *,
    evaluation_count: int,
    seed: int,
) -> tuple[list[list[Path]], list[list[Path]]]:
    total = sum(len(group) for group in groups)
    if not 0 < evaluation_count < total:
        raise ValueError("--evaluation-count deve estar entre 1 e total - 1.")

    candidates = groups.copy()
    random.Random(seed).shuffle(candidates)
    evaluation: list[list[Path]] = []
    calibration: list[list[Path]] = []
    selected = 0
    for group in candidates:
        if selected + len(group) <= evaluation_count:
            evaluation.append(group)
            selected += len(group)
        else:
            calibration.append(group)
    if selected != evaluation_count:
        raise ValueError(
            "Não foi possível atingir a quantidade de avaliação preservando "
            "os grupos de fotos próximas. Ajuste --evaluation-count."
        )
    return calibration, evaluation


def build_rows(
    calibration: list[list[Path]],
    evaluation: list[list[Path]],
    images_dir: Path,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for split, groups in (("calibration", calibration), ("evaluation", evaluation)):
        for group_number, group in enumerate(groups, start=1):
            group_id = f"capture-{split[:3]}-{group_number:02d}"
            for path in group:
                timestamp = capture_time(path)
                rows.append(
                    {
                        "sample_id": f"own-{path.stem.lower().replace('_', '-')}",
                        "image_path": str(path.relative_to(images_dir.parents[2])).replace("\\", "/"),
                        "capture_timestamp": timestamp.isoformat() if timestamp else "",
                        "similar_capture_group": group_id,
                        "split": split,
                        "source": "Foto autoral fornecida pela equipe",
                        "subject_id": "",
                        "is_kitten": "",
                        "expected_primary_color": "",
                        "expected_colors": "",
                        "expected_coat_type": "",
                        "review_status": "Pendente",
                        "notes": "",
                    }
                )
    return sorted(rows, key=lambda row: row["image_path"])


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_arguments()
    files = sorted(
        path for path in args.images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        raise SystemExit(f"Nenhuma imagem suportada em {args.images_dir}.")
    groups = group_similar_captures(files)
    calibration, evaluation = split_groups(
        groups,
        evaluation_count=args.evaluation_count,
        seed=args.seed,
    )
    rows = build_rows(calibration, evaluation, args.images_dir)
    write_manifest(args.output, rows)
    print(
        f"Manifesto criado: {len(rows)} fotos; "
        f"{sum(row['split'] == 'calibration' for row in rows)} calibração; "
        f"{sum(row['split'] == 'evaluation' for row in rows)} avaliação."
    )


if __name__ == "__main__":
    main()
