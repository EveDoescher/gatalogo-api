"""Benchmark isolado de reidentificação felina.

Não usa aplicativo, conta, localização, alertas ou fotos de usuários. A galeria
e as consultas ficam em pastas diferentes, agrupadas pelo identificador real
do gato, usado apenas para calcular as métricas no fim.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.services.vision_worker import Sam2DinoWorker  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
from app.services.recognition_features import model_version
CACHE_VERSION = model_version(get_settings())


def _images(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        raise ValueError(f"Pasta não encontrada: {root}")
    rows: list[tuple[str, Path]] = []
    for identity_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for image in sorted(identity_dir.rglob("*")):
            if image.is_file() and image.suffix.lower() in IMAGE_EXTENSIONS:
                rows.append((identity_dir.name, image))
    if not rows:
        raise ValueError(f"Nenhuma foto encontrada em {root}")
    return rows


def _own_cats_split(manifest: Path):
    raise ValueError(
        "Benchmark por CSV desativado: os metadados antigos não identificam indivíduos. "
        "Use compare_cats.py ou inspect_photo_gallery.py diretamente nas fotos."
    )


def _distractors(root: Path | None, limit: int, seed: int) -> list[tuple[str, Path]]:
    if root is None or limit == 0:
        return []
    candidates = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
    selected = random.Random(seed).sample(candidates, k=min(limit, len(candidates)))
    return [(f"distractor:{index:04d}", path) for index, path in enumerate(selected, start=1)]


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.9g}" for value in vector) + "]"


def _cached_embedding(
    worker: Sam2DinoWorker,
    path: Path,
    cache_dir: Path,
) -> tuple[list[float], bool]:
    """Evita repetir SAM 2/DINOv2 após uma execução longa ser interrompida."""
    metadata = path.stat()
    fingerprint = hashlib.sha256(
        f"{CACHE_VERSION}|{path.resolve()}|{metadata.st_size}|{metadata.st_mtime_ns}".encode()
    ).hexdigest()
    cache_file = cache_dir / f"{fingerprint}.json"
    if cache_file.is_file():
        vector = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(vector, list) and len(vector) == 384:
            return [float(value) for value in vector], True
    with Image.open(path) as image:
        vector = worker.extract_embedding(image)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(vector), encoding="utf-8")
    return vector, False


async def benchmark(
    gallery: list[tuple[str, Path]],
    queries: list[tuple[str, Path]],
    top_k: int,
    *,
    source: str,
    distractor_count: int,
    embedding_cache_dir: Path,
) -> dict[str, Any]:
    settings = get_settings()
    worker = Sam2DinoWorker(settings)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    failures: list[dict[str, str]] = []
    report_rows: list[dict[str, Any]] = []
    cache_hits = 0
    computed_embeddings = 0

    async with engine.connect() as connection:
        # A tabela temporária garante que nenhum embedding de benchmark vai para
        # o catálogo real. A consulta usa exatamente o operador pgvector (<=>).
        await connection.execute(text("CREATE TEMP TABLE reid_gallery (identity_label text NOT NULL, image_path text NOT NULL, embedding vector(384) NOT NULL) ON COMMIT DROP"))
        for identity, path in gallery:
            try:
                vector, cache_hit = _cached_embedding(worker, path, embedding_cache_dir)
                cache_hits += int(cache_hit)
                computed_embeddings += int(not cache_hit)
                await connection.execute(
                    text("INSERT INTO reid_gallery (identity_label, image_path, embedding) VALUES (:identity, :path, CAST(:embedding AS vector))"),
                    {"identity": identity, "path": str(path), "embedding": _vector_literal(vector)},
                )
            except Exception as error:
                failures.append({"path": str(path), "error": str(error)})
        await connection.execute(text("CREATE INDEX reid_gallery_hnsw ON reid_gallery USING hnsw (embedding vector_cosine_ops)"))
        await connection.execute(text("SET LOCAL hnsw.ef_search = 100"))

        for expected_identity, path in queries:
            try:
                vector, cache_hit = _cached_embedding(worker, path, embedding_cache_dir)
                cache_hits += int(cache_hit)
                computed_embeddings += int(not cache_hit)
                result = await connection.execute(
                    text("SELECT identity_label, image_path, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity FROM reid_gallery ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT :limit"),
                    {"embedding": _vector_literal(vector), "limit": top_k},
                )
                matches = [
                    {"identity": row.identity_label, "path": row.image_path, "similarity": round(float(row.similarity), 6)}
                    for row in result
                ]
                rank = next((index + 1 for index, item in enumerate(matches) if item["identity"] == expected_identity), None)
                report_rows.append({
                    "query": str(path),
                    "expected_identity": expected_identity,
                    "rank": rank,
                    "top_1_correct": rank == 1,
                    "top_k_correct": rank is not None,
                    "matches": matches,
                })
            except Exception as error:
                failures.append({"path": str(path), "error": str(error)})
        await connection.rollback()
    await engine.dispose()

    tested = len(report_rows)
    return {
        "source": source,
        "gallery_images": len(gallery),
        "distractor_images": distractor_count,
        "embedding_cache_dir": str(embedding_cache_dir),
        "cache_hits": cache_hits,
        "computed_embeddings": computed_embeddings,
        "queries_tested": tested,
        "top_1_accuracy": round(sum(item["top_1_correct"] for item in report_rows) / tested, 4) if tested else 0.0,
        "recall_at_k": round(sum(item["top_k_correct"] for item in report_rows) / tested, 4) if tested else 0.0,
        "top_k": top_k,
        "results": report_rows,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia SAM 2 + DINOv2 + pgvector sem localização.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--own-cats-csv", type=Path, help="CSV já existente com os pares de fotos reais.")
    source_group.add_argument("--gallery", type=Path, help="Pasta com uma subpasta por gato de referência.")
    parser.add_argument("--queries", type=Path, help="Obrigatória junto com --gallery.")
    parser.add_argument("--distractor-dir", type=Path, default=None, help="Pasta de fotos extras para aumentar a galeria.")
    parser.add_argument("--distractor-limit", type=int, default=200, choices=range(0, 5001))
    parser.add_argument("--seed", type=int, default=20260828, help="Semente para escolher os distraidores.")
    parser.add_argument(
        "--embedding-cache",
        type=Path,
        default=Path(".model-cache/reid-embeddings"),
        help="Cache persistente de vetores; retoma um benchmark interrompido.",
    )
    parser.add_argument("--top-k", type=int, default=5, choices=range(1, 51))
    parser.add_argument("--output", type=Path, default=None, help="Arquivo JSON opcional para salvar o relatório.")
    args = parser.parse_args()
    if args.own_cats_csv:
        gallery, queries = _own_cats_split(args.own_cats_csv)
        source = str(args.own_cats_csv)
    else:
        if args.queries is None:
            parser.error("--queries é obrigatória quando --gallery é usada.")
        gallery, queries = _images(args.gallery), _images(args.queries)
        source = f"gallery={args.gallery}; queries={args.queries}"
    distractors = _distractors(args.distractor_dir, args.distractor_limit, args.seed)
    result = asyncio.run(
        benchmark(
            gallery + distractors,
            queries,
            args.top_k,
            source=source,
            distractor_count=len(distractors),
            embedding_cache_dir=args.embedding_cache,
        )
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
