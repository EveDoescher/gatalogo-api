"""Bounded real detector/SAM/Gemini observations in a fresh frozen-source process."""

import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import sys
import time
import urllib.request

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, config["source"])
from app.config import Settings
from app.services import analysis_service
from app.exceptions import RejectedImageError
from paid_provider import PaidClient, ledger_summary
from run_own_cats_calibration import build_comparison
from measurements import resources
import numpy as np
import torch


async def main():
    logging.basicConfig(level=logging.ERROR)
    torch.set_num_threads(2)
    root, output = Path(config["root"]), Path(config["output"])
    output.mkdir(exist_ok=False)
    settings = Settings(_env_file=root / ".env", model_cache_dir=str(root / ".model-cache"),
        sam2_checkpoint=str(root / ".model-cache/sam2.1_hiera_tiny.pt"),
        analysis_cache_dir=str(output / "cache"), vision_device="cpu")
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert settings.gemini_max_output_tokens == 1024
    client = PaidClient(api_key=settings.gemini_api_key, ledger=config["ledger"], phase=config["role"])
    analysis_service._get_gemini_client = lambda key: client
    # Existing weights only. Provider HTTP is separately restricted by PaidClient.
    def deny(*args, **kwargs):
        raise AssertionError("New model downloads are outside this campaign")
    torch.hub.download_url_to_file = deny
    urllib.request.urlopen = deny
    torch.hub.urlopen = deny
    service = analysis_service.AnalysisService(settings)
    references = json.loads((root / "data/own-cats/calibration_references.json").read_text(encoding="utf-8"))
    mask_folder = output / "masks"
    mask_folder.mkdir()
    observations, active = [], {}
    # Instance wrappers measure actual stages without altering results or thresholds.
    for owner, name in ((service.preprocessing, "prepare"), (service.quality, "analyze"),
            (service.segmentation, "segment_cat"), (service.colors, "extract"), (service.fusion, "combine")):
        original = getattr(owner, name)
        def instrument(*args, _original=original, _name=name, **kwargs):
            started = time.perf_counter()
            try:
                value = _original(*args, **kwargs)
                if _name == "segment_cat":
                    mask = np.asarray(value.mask)
                    np.savez_compressed(mask_folder / (active["sample_id"] + ".npz"), mask=mask)
                    active["segmentation"] = {"cat_count": value.cat_count,
                        "ambiguous_multiple_cats": value.ambiguous_multiple_cats,
                        "detector_confidence": float(value.detector_confidence or 0),
                        "mask_shape": list(mask.shape), "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}
                return value
            finally:
                active.setdefault("stages_seconds", {})[_name] = time.perf_counter() - started
        setattr(owner, name, instrument)
    try:
        for index, reference in enumerate(references):
            # Anonymous stable ID; filenames and original photos stay private.
            active = {"sample_id": f"sample-{index+1:02}", "process_state": "cold" if index == 0 else "warm",
                "resources_before": resources()}
            data = (root / reference["image_path"]).read_bytes()
            active["input_sha256"] = hashlib.sha256(data).hexdigest()
            started = time.perf_counter()
            try:
                response = await service.analyze(cat_id=active["sample_id"], image_bytes=data)
                active.update(outcome="accepted", response=response.model_dump(mode="json"),
                    comparison=build_comparison(reference, response))
            except RejectedImageError as error:
                active.update(outcome="rejected", reason=error.reason)
            except Exception as error:
                # Never persist provider exception text, prompts, key or original filenames.
                active.update(outcome="error", error_type=type(error).__name__)
            active.update(elapsed_seconds=time.perf_counter()-started, resources_after=resources())
            observations.append(active)
            (output / "samples.json").write_text(json.dumps(observations, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            print(json.dumps({"role": config["role"], "sample": active["sample_id"], "outcome": active["outcome"]}), flush=True)
    finally:
        client.close()
    safe_fields = ("gemini_model", "gemini_max_output_tokens", "gemini_max_retries", "vision_device",
        "vision_max_side", "gemini_max_side", "jpeg_quality", "detector_confidence", "detector_fallback_confidence")
    manifest = {"role": config["role"], "source_sha": config["source_sha"], "pid": os.getpid(),
        "settings": {key: getattr(settings, key) for key in safe_fields}, "seed": None,
        "torch_threads": torch.get_num_threads(), "prompt_sha256": hashlib.sha256(analysis_service.COAT_ANALYSIS_PROMPT.encode()).hexdigest(),
        "versions": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "google-genai", "numpy")},
        "usage": [r for r in ledger_summary(config["ledger"]) if r["phase"] == config["role"]],
        "samples": len(observations), "scope": "Authorial pre-existing human attribute labels; no individual identity labels, no reidentification claim. One observation per version/image; stochastic provider; no reliable tail percentile or accuracy generalization."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
