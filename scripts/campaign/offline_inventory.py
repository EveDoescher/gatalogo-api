"""Record offline dependencies and release inputs without reading local secrets."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess


def collect(api, flutter, output):
    api, flutter, output = (
        Path(api).resolve(),
        Path(flutter).resolve(),
        Path(output).resolve(),
    )
    if output.exists():
        raise ValueError("Never overwrite inventory")
    entries = {}
    for repo in (api, flutter):
        revision = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        inputs = {}
        for name in (
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "pubspec.yaml",
            "pubspec.lock",
            "lib/config/api_config.dart",
        ):
            p = repo / name
            if p.is_file():
                inputs[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        entries[repo.name] = {"commit": revision, "inputs_sha256": inputs}
    result = {
        "scenario": "SEC-09",
        "state": "OFFLINE_INVENTORY_ONLY",
        "repositories": entries,
        "python": platform.python_version(),
        "installed_python_distributions": sorted(
            [
                {"name": d.metadata["Name"], "version": d.version}
                for d in importlib.metadata.distributions()
            ],
            key=lambda d: d["name"].lower(),
        ),
        "limitations": [
            "No online advisory lookup or vulnerability absence claim",
            "No production release build/device test",
            "Local secrets and service accounts were not read",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--flutter", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    collect(args.api, args.flutter, args.output)
