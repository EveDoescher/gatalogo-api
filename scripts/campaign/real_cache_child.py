"""IMG-04: real cached local models and bounded provider; independent cache axes."""

import asyncio
import hashlib
import itertools
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.request

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, config["source"])
from app.config import Settings
from app.exceptions import MissingApiKeyError
from app.services import analysis_service
from app.services.analysis_cache import AnalysisCache
from paid_provider import PaidClient, ledger_summary
from measurements import resources
import torch


def normalized(response):
    value = response.model_dump(mode="json")
    for key in ("analyzed_at", "warnings"):
        value.pop(key, None)
    return value


async def main():
    root, folder = Path(config["root"]), Path(config["output"])
    folder.mkdir(exist_ok=False)
    torch.set_num_threads(2)
    settings = Settings(_env_file=root / ".env", model_cache_dir=str(root / ".model-cache"),
        sam2_checkpoint=str(root / ".model-cache/sam2.1_hiera_tiny.pt"), vision_device="cpu")
    client = PaidClient(api_key=settings.gemini_api_key, ledger=config["ledger"], phase=config["role"])
    analysis_service._get_gemini_client = lambda key: client
    def denied(*args, **kwargs):
        raise AssertionError("Model downloads prohibited")
    torch.hub.download_url_to_file = denied
    urllib.request.urlopen = denied
    torch.hub.urlopen = denied
    references = json.loads((root / "data/own-cats/calibration_references.json").read_text(encoding="utf-8"))
    photos = [root / "tests/fixtures/local_models/chelsea.png", root / references[0]["image_path"]]
    rows = []
    def calls():
        return sum(r["phase"] == config["role"] for r in ledger_summary(config["ledger"]))
    try:
        for photo_index, photo in enumerate(photos):
            data = photo.read_bytes()
            seed_folder = folder / f"photo-{photo_index}-seed"
            service = analysis_service.AnalysisService(settings.model_copy(update={"analysis_cache_dir": str(seed_folder)}))
            start, initial, before = time.perf_counter(), calls(), resources()
            response = await service.analyze(cat_id=f"fixed-{photo_index}", image_bytes=data)
            seed = normalized(response)
            assert calls() == initial + 1, "Choose photos exercising real provider; do not silently cover a local shortcut"
            rows.append({"photo": photo_index, "state": "cold" if photo_index == 0 else "warm",
                "seed": True, "input_sha256": hashlib.sha256(data).hexdigest(),
                "elapsed_seconds": time.perf_counter()-start, "resources_before": before,
                "resources_after": resources(), "provider_calls": calls()-initial})
            for segmentation_full, semantic_full, provider in itertools.product((False, True), repeat=3):
                trial = folder / f"photo-{photo_index}-seg{int(segmentation_full)}-sem{int(semantic_full)}-key{int(provider)}"
                for populated, name in ((segmentation_full, "seg"), (semantic_full, "sem")):
                    if populated:
                        shutil.copytree(seed_folder, trial / name)
                current = settings.model_copy(update={"analysis_cache_dir": str(trial / "sem"),
                    "gemini_api_key": settings.gemini_api_key if provider else None})
                service = analysis_service.AnalysisService(current)
                service.segmentation.cache = AnalysisCache(str(trial / "seg"))
                hits = {"semantic": 0, "segmentation": 0}
                for owner, method, axis in ((service.semantic_cache, "get", "semantic"),
                        (service.segmentation.cache, "get_segmentation", "segmentation")):
                    original = getattr(owner, method)
                    def observed(*args, _original=original, _axis=axis, **kwargs):
                        value = _original(*args, **kwargs)
                        hits[_axis] += int(value is not None)
                        return value
                    setattr(owner, method, observed)
                initial, start, before = calls(), time.perf_counter(), resources()
                row = {"photo": photo_index, "seed": False, "segmentation_full": segmentation_full,
                    "semantic_full": semantic_full, "provider_enabled": provider}
                try:
                    result = await service.analyze(cat_id=f"fixed-{photo_index}", image_bytes=data)
                    row.update(outcome="accepted", equals_seed=normalized(result) == seed)
                    assert semantic_full or provider, "Unexpected success without a semantic cache or API key"
                    if semantic_full:
                        assert row["equals_seed"], "Cached semantics changed final result"
                except MissingApiKeyError:
                    row["outcome"] = "missing-key"
                    assert not provider and not semantic_full
                row.update(elapsed_seconds=time.perf_counter()-start, cache_hits=hits,
                    provider_calls=calls()-initial, resources_before=before, resources_after=resources())
                assert bool(hits["segmentation"]) == segmentation_full
                assert bool(hits["semantic"]) == semantic_full
                assert row["provider_calls"] == int(provider and not semantic_full)
                rows.append(row)
                (folder / "observations.json").write_text(json.dumps(rows, indent=2)+"\n", encoding="utf-8")
                print(json.dumps({"trial": config["role"], **{k: row[k] for k in ("photo", "segmentation_full", "semantic_full", "provider_enabled", "outcome")}}), flush=True)
        (folder / "summary.json").write_text(json.dumps({"passed": True, "variants": len(rows)-len(photos),
            "seed_runs": len(photos), "source_sha": config["source_sha"], "model": settings.gemini_model,
            "seed": None, "versions": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "google-genai", "numpy")},
            "prompt_sha256": hashlib.sha256(analysis_service.COAT_ANALYSIS_PROMPT.encode()).hexdigest(),
            "scope": "Real cached weights, CPU two threads, independent fresh/populated caches. Real provider returns may vary; exact result equality required for cache reuse only. One cold seed per fresh process; no tail percentile/speedup claim."}, indent=2)+"\n", encoding="utf-8")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
