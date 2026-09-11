"""Compare actual photographs without spreadsheets, prior JSON or a database.

python scripts/compare_cats.py --query photo.jpg --reference other.jpg
Multiple --reference options describe one candidate cat, not a mixed gallery.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.services.recognition_service import compare_embeddings, cosine_similarity
from app.services.vision_worker import Sam2DinoWorker


def extract(worker, path):
    with path.open("rb") as source:
        data = source.read(worker.settings.max_image_bytes + 1)
    evidence = worker.extract_bytes(data)
    return SimpleNamespace(**evidence, region="body", view="unknown",
                           photo_id=uuid4(), photo_hash=hashlib.sha256(data).hexdigest(),
                           is_solid_coat=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", type=Path, action="append", required=True)
    parser.add_argument("--reference", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, help="Optional Markdown report")
    args = parser.parse_args()
    worker = Sam2DinoWorker(get_settings())
    queries, references = [], []
    lines = ["# Comparação de fotografias", "", "Calculada diretamente das imagens, sem rótulos anteriores.", ""]
    for paths, rows, label in ((args.query, queries, "Consulta"), (args.reference, references, "Referência")):
        for path in paths:
            print(f"Processando {label.lower()}: {path.name}", flush=True)
            row = extract(worker, path)
            rows.append(row)
            lines.append(f"- {label}: {path.name}; qualidade técnica {row.quality_score:.3f}; detalhes locais {len(row.features['local']['points'])}.")
    result = compare_embeddings(queries, references, worker.settings, uncertain_coat=True)
    best = max(cosine_similarity(a.embedding, b.embedding) for a in queries for b in references)
    lines += ["", f"Similaridade visual máxima: {best:.4f} (não é probabilidade de identidade).", ""]
    if result is None:
        lines.append("Não há evidência suficiente para sugerir correspondência pelos critérios atuais. Isso não exclui que seja o mesmo gato.")
    else:
        evidence = result["evidence"]
        lines += [f"Pares de fotografias distintas com suporte: {evidence['distinct_photo_pairs']}.",
                  f"Detalhes consistentes geometricamente: {evidence['local']['inliers']}.",
                  f"Sinal denso: {evidence['dense']['similarity']}; patches com correspondência distinta: {evidence['dense']['mutual_matches']}.",
                  "Identidade: inconclusiva. Pelagem e identidade não foram rotuladas; confira as fotos e obtenha outras vistas."]
    lines += ["", "Limitação: os limiares ainda não foram calibrados com indivíduos conhecidos. Este teste verifica extração/comparação, não mede acurácia."]
    report = "\n".join(lines) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
