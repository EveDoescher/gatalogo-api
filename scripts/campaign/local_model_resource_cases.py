"""Cold/warm local-model resource campaign; each role/trial has a new process."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest
from security_cases import evidence
from subprocesses import run_logged


@pytest.mark.asyncio
async def test_RSC01_fresh_api_and_worker_processes():
    root = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    root.mkdir(parents=True, exist_ok=True)
    cache = Path(os.environ["QUALITY_MODEL_CACHE"])
    weights = [cache / name for name in (
        "sam2.1_hiera_tiny.pt", "checkpoints/dinov2_vits14_pretrain.pth",
        "checkpoints/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth",
    )]
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in weights}
    results = []
    for role in ("api", "worker"):
        for trial in range(2):
            folder = root / f"{role}-{trial}"
            folder.mkdir()
            env = dict(os.environ, QUALITY_CASE_EVIDENCE=str(folder))

            def execute():
                with (folder / "pytest.log").open("w", encoding="utf-8") as log:
                    return run_logged([
                        sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-ra",
                        str(Path(__file__).with_name("local_model_probe_cases.py")) + f"::test_RSC01_real_local_models[{role}]",
                        "--confcutdir", str(Path(__file__).parent),
                        "--junitxml", str(folder / "junit.xml"),
                    ], cwd=os.environ["QUALITY_TARGET_SOURCE"], env=env, log=log, timeout=260)

            started = time.perf_counter()
            result = await asyncio.to_thread(execute)
            assert result.returncode == 0, f"{role}/{trial} failed; preserve and inspect {folder / 'pytest.log'}"
            observation = json.loads((folder / "RSC01-local-models.json").read_text())
            observation["trial"] = trial
            observation["process_wall_seconds_including_import_startup_and_teardown"] = time.perf_counter() - started
            results.append(observation)
            print(f"RSC01 {role}/{trial}: cold + three warm + idle passed", flush=True)
    assert len({row["pid"] for row in results}) == 4
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[path.name] for path in weights)
    evidence("RSC01-fresh-processes", {
        "scenario": "RSC-01", "weights_sha256": hashes, "trials": results,
        "paid_calls": 0, "processes": 4,
        "sampling": "Two fresh process trials per role; one cold and three warm executions each; 5s idle at 10Hz. Bounded resource observations, no inferential percentile/speedup claims. OS file cache remains warm, including SHA preflight reads.",
    })
