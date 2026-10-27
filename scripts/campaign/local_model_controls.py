"""Shared offline controls: real local models and a schema-valid semantic stub."""

import re
import urllib.request

from app.config import Settings
from app.models.analysis import GeminiCoatAnalysis
from app.services import analysis_service
from image_cases import semantic


def model_settings(cache, analysis_cache):
    return Settings(
        _env_file=None, model_cache_dir=str(cache),
        sam2_checkpoint=str(cache / "sam2.1_hiera_tiny.pt"),
        vision_device="cpu", analysis_cache_dir=str(analysis_cache),
        gemini_api_key="controlled-provider-never-sent", smtp_enabled=False,
        vision_worker_poll_seconds=0.05,
    )


def palette_semantics(image_bytes, mime_type, context):
    payload = semantic().model_dump()
    payload["color_assignments"] = [
        {"cluster_index": int(index), "name": "Marrom"}
        for index in re.findall(r"cluster_index=(\d+);", context)
    ]
    return GeminiCoatAnalysis.model_validate(payload)


def prohibit_external_models(monkeypatch):
    import torch

    attempts = []

    def denied(*args, **kwargs):
        attempts.append("external-model-or-provider-request")
        raise AssertionError("External downloads/inference prohibited in this experiment")

    monkeypatch.setattr(torch.hub, "download_url_to_file", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    monkeypatch.setattr(torch.hub, "urlopen", denied)
    monkeypatch.setattr(analysis_service, "_get_gemini_client", denied)
    return attempts
