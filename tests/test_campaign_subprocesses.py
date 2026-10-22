"""Check that bounded executors report timeout separately from assertions."""

import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    "campaign_subprocesses",
    Path(__file__).resolve().parents[1] / "scripts/campaign/subprocesses.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_logged_success(tmp_path):
    with (tmp_path / "log").open("w", encoding="utf-8") as log:
        result = module.run_logged(
            [sys.executable, "-c", "print('completed')"],
            cwd=tmp_path,
            env=dict(os.environ),
            log=log,
            timeout=10,
        )
    assert result.returncode == 0
    assert "completed" in (tmp_path / "log").read_text()


def test_timeout_is_infrastructure_code_and_child_exits(tmp_path):
    with (tmp_path / "log").open("w", encoding="utf-8") as log:
        result = module.run_logged(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=dict(os.environ),
            log=log,
            timeout=0.2,
        )
    assert result.returncode == 124
    assert "Timeout" in (tmp_path / "log").read_text()
