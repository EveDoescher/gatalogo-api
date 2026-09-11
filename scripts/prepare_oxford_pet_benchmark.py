"""Cria um recorte reprodutível de gatos do Oxford-IIIT Pet.

O download do conjunto não é automatizado de propósito: são cerca de 800 MB
e a licença CC BY-SA 4.0 exige preservação da atribuição. Depois de extrair
os dois arquivos oficiais em ``data/raw/oxford-iiit-pet``, execute este
arquivo para gerar o manifesto versionável do benchmark.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path


SOURCE_URL = "https://www.robots.ox.ac.uk/~vgg/data/pets/"
LICENSE = "CC BY-SA 4.0"
CAT_SPECIES_ID = "1"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera um manifesto estratificado de gatos do Oxford-IIIT Pet."
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/raw/oxford-iiit-pet"),
        help="Pasta que contém images/ e annotations/.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark/manifests/oxford_iiit_pet_cats_v1.csv"),
        help="Arquivo CSV a gerar.",
    )
    parser.add_argument(
        "--per-breed",
        type=int,
        default=12,
        help="Quantidade de imagens por raça de gato (padrão: 12).",
    )
    parser.add_argument(
        "--calibration-per-breed",
        type=int,
        default=8,
        help="Quantidade por raça reservada para calibração.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260826,
        help="Semente usada para tornar a seleção reproduzível.",
    )
    return parser.parse_args()


def load_cat_image_ids(annotation_list: Path) -> dict[str, list[str]]:
    """Lê annotations/list.txt e agrupa IDs de imagem por raça felina."""
    groups: dict[str, list[str]] = defaultdict(list)
    for raw_line in annotation_list.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        image_id, _class_id, species_id, _breed_id = line.split()
        if species_id != CAT_SPECIES_ID:
            continue

        breed, _number = image_id.rsplit("_", maxsplit=1)
        groups[breed].append(image_id)
    return dict(groups)


def build_manifest_rows(
    groups: dict[str, list[str]],
    *,
    per_breed: int,
    calibration_per_breed: int,
    seed: int,
) -> list[dict[str, str]]:
    if per_breed <= 0:
        raise ValueError("--per-breed deve ser maior que zero.")
    if not 0 <= calibration_per_breed < per_breed:
        raise ValueError(
            "--calibration-per-breed deve ser maior ou igual a zero e menor "
            "que --per-breed."
        )

    rows: list[dict[str, str]] = []
    for breed in sorted(groups):
        candidates = sorted(groups[breed])
        if len(candidates) < per_breed:
            raise ValueError(
                f"A raça {breed} só possui {len(candidates)} imagens; "
                f"são necessárias {per_breed}."
            )

        # Uma semente por raça evita que a inclusão de outra raça altere as
        # amostras das demais quando o conjunto for atualizado.
        random.Random(f"{seed}:{breed}").shuffle(candidates)
        for position, image_id in enumerate(candidates[:per_breed]):
            split = "calibration" if position < calibration_per_breed else "evaluation"
            rows.append(
                {
                    "sample_id": f"oxpet-{image_id.lower()}",
                    "source": "Oxford-IIIT Pet",
                    "license": LICENSE,
                    "source_url": SOURCE_URL,
                    "breed": breed.replace("_", " "),
                    "split": split,
                    "expected_is_cat": "true",
                    "image_path": f"images/{image_id}.jpg",
                    "trimap_path": f"annotations/trimaps/{image_id}.png",
                }
            )
    return rows


def write_manifest(output: Path, rows: list[dict[str, str]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sample_id",
        "source",
        "license",
        "source_url",
        "breed",
        "split",
        "expected_is_cat",
        "image_path",
        "trimap_path",
    ]
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_arguments()
    annotation_list = args.dataset_root / "annotations" / "list.txt"
    if not annotation_list.is_file():
        raise SystemExit(
            "Arquivo de anotações não encontrado: "
            f"{annotation_list}. Baixe e extraia annotations.tar.gz primeiro."
        )

    groups = load_cat_image_ids(annotation_list)
    rows = build_manifest_rows(
        groups,
        per_breed=args.per_breed,
        calibration_per_breed=args.calibration_per_breed,
        seed=args.seed,
    )
    write_manifest(args.output, rows)

    evaluation_count = sum(row["split"] == "evaluation" for row in rows)
    print(
        f"Manifesto criado em {args.output}: {len(rows)} imagens, "
        f"{len(groups)} raças, {evaluation_count} reservadas para avaliação."
    )


if __name__ == "__main__":
    main()
