"""Explicit opt-in mutation cases with unique source copies and evidence."""

import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
ATTEMPT = Path(os.environ.get("QUALITY_CAMPAIGN_ATTEMPT", str(RUN)))
MUTANTS = [
    ("MUT-01", "app/routers/sync.py",
     ".where(SyncChange.user_id == user.id, SyncChange.revision > cursor)",
     ".where(SyncChange.revision > cursor)", "SEC-02"),
    ("MUT-12", "app/services/quintal_production.py",
     'if old.details.get("request") != fingerprint:',
     'if False and old.details.get("request") != fingerprint:', "DATA-04"),
]


@pytest.mark.parametrize("mid,path,old,new,expected", MUTANTS)
def test_directed_mutation(mid, path, old, new, expected):
    baseline_paths = [ATTEMPT / "results" / label / "probe.json" for label in ("02-B", "03-B")]
    baselines = [json.loads(p.read_text(encoding="utf-8")) for p in baseline_paths]
    assert all(not b.get("infrastructure_error") and all(c["status"] == "PASS" for c in b["cases"])
               for b in baselines), "Mutation requires a passing baseline"
    target = RUN / "sources" / f"{ATTEMPT.name}-{mid}" / "api"
    shutil.copytree(RUN / "sources" / "B" / "api", target,
                    ignore=shutil.ignore_patterns(".tmp", "__pycache__", ".pytest_cache"))
    source = target / path
    original = source.read_text(encoding="utf-8")
    assert original.count(old) == 1, "Mutation must match exactly once"
    modified = original.replace(old, new, 1)
    source.write_text(modified, encoding="utf-8")
    output = ATTEMPT / "results" / mid / "probe.json"
    output.parent.mkdir(parents=True, exist_ok=False)
    (output.parent / "mutation.diff").write_text("".join(difflib.unified_diff(
        original.splitlines(keepends=True), modified.splitlines(keepends=True), fromfile=path, tofile=path)), encoding="utf-8")
    started = time.perf_counter()
    with (output.parent / "process.log").open("w", encoding="utf-8") as log:
        result = subprocess.run([sys.executable, str(ATTEMPT / "executors" / "api_probe.py"),
                                 "--source", str(target), "--output", str(output), "--label", mid],
                                cwd=target, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    failed = [c for c in report.get("cases", []) if c["status"] == "FAIL"]
    classification = "INCONCLUSIVE_INFRASTRUCTURE" if result.returncode or report.get("infrastructure_error") else (
        "KILLED" if any(c["scenario"] == expected for c in failed) else "SURVIVED")
    summary = {"mutant": mid, "classification": classification, "expected_scenario": expected,
               "elapsed_seconds": time.perf_counter() - started, "failed_cases": failed,
               "baseline_evidence": [str(p.relative_to(RUN)) for p in baseline_paths],
               "line_exercised": "behavioral path verified by baseline and failed scenario" if classification == "KILLED" else "not proven"}
    (output.parent / "classification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    assert classification == "KILLED", summary
