"""Compute fresh evidence from an unlabeled list of photographs, never CSV/JSON."""
import argparse
from itertools import combinations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.services.recognition_features import near_duplicate, verify_local_features
from app.services.dense_features import compare_dense_features
from app.services.recognition_service import cosine_similarity
from app.services.vision_worker import Sam2DinoWorker
from scripts.compare_cats import extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    worker = Sam2DinoWorker(get_settings())
    rows, failures = [], []
    for path in args.photos:
        print(f"Extraindo {path.name}", flush=True)
        try:
            rows.append((path, extract(worker, path)))
        except Exception as error:
            failures.append((path, str(error)))
    lines = ["# Verificação direta das fotos", "",
        "Todas as evidências foram recalculadas dos pixels. Nenhuma planilha, CSV ou JSON anterior foi usado.", "",
        "**As identidades são desconhecidas.** Estes números não medem acurácia nem probabilidade de ser o mesmo gato.", "",
        "| Foto | Qualidade técnica | Detalhes locais | Patches do gato |", "|---|---:|---:|---:|"]
    for path, row in rows:
        lines.append(f"| [{path.name}]({path.resolve().as_posix()}) | {row.quality_score:.3f} | {len(row.features['local']['points'])} | {row.features['dense']['count']} |")
    lines += ["", "| Fotos | Global | Densa | Pares densos distintos | Inliers densos | Geometria densa | Inliers RootSIFT | Quase duplicada |",
              "|---|---:|---:|---:|---:|---|---:|---|"]
    for (a_path, a), (b_path, b) in combinations(rows, 2):
        local = verify_local_features(a.features["local"], b.features["local"])
        dense = compare_dense_features(a.features.get("dense"), b.features.get("dense"))
        dense_score = f"{dense['similarity']:.4f}" if dense["similarity"] is not None else "indisponível"
        score = cosine_similarity(a.embedding, b.embedding)
        lines.append(f"| {a_path.stem} / {b_path.stem} | {score:.4f} | {dense_score} | {dense['mutual_matches']} | {dense['inliers']} | {'sim' if dense['geometry_consistent'] else 'não'} | {local['inliers']} | {'sim' if near_duplicate(a, b) else 'não'} |")
    if failures:
        lines += ["", "Fotos recusadas:"] + [f"- {path.name}: {error}" for path, error in failures]
    lines += ["", "Detalhes locais ausentes são inconclusivos, especialmente com pelagem uniforme ou mudança de pose. Geometria consistente também não comprova identidade.",
              "As correspondências densas também são semânticas: regiões comuns a gatos diferentes podem ser semelhantes. Somente uma avaliação com identidade conhecida permite calibrar esse sinal.",
              "Para avaliar identificação entre dias e ambientes será necessário confirmar os indivíduos e separar fotos de cadastro das fotos de teste."]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Concluído: {len(rows)} fotos processadas, {len(failures)} recusadas. {args.output}")


if __name__ == "__main__":
    main()
