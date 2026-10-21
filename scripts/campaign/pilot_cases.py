"""Explicit opt-in cases for the disposable runner; not normal pytest collection."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
ATTEMPT = Path(os.environ.get("QUALITY_CAMPAIGN_ATTEMPT", str(RUN)))


@pytest.mark.parametrize("label,arm", [("01-A", "A"), ("02-B", "B"), ("03-B", "B"), ("04-A", "A")])
def test_frozen_api_probe(label, arm):
    output = ATTEMPT / "results" / label / "probe.json"
    output.parent.mkdir(parents=True, exist_ok=False)
    with (output.parent / "process.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [sys.executable, str(ATTEMPT / "executors" / "api_probe.py"),
             "--source", str(RUN / "sources" / arm / "api"),
             "--output", str(output), "--label", label],
            env=dict(os.environ), cwd=RUN / "sources" / arm / "api",
            stdout=log, stderr=subprocess.STDOUT, timeout=240,
        )
    assert result.returncode == 0, f"Probe infrastructure failed; inspect {output.parent}"
    report = json.loads(output.read_text(encoding="utf-8"))
    assert not report.get("infrastructure_error"), "Probe failed; inspect retained evidence"
    # A retained DB connection is an explicit historical diagnostic, not an expectation weakened for B.
    failures = [r for r in report["cases"] if r["status"] != "PASS"
                and not (arm == "A" and r["scenario"] == "DB-05")]
    assert not failures, failures
