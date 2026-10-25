"""Bounded desktop Flutter regression with source guards and private evidence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from campaign.subprocesses import run_logged

ROOT = Path(__file__).resolve().parents[1]
FLUTTER = ROOT.parent / "catlogue"


def hashes():
    paths = [FLUTTER / "pubspec.yaml", FLUTTER / "pubspec.lock"]
    for folder in ("lib", "test", "tool"):
        paths.extend((FLUTTER / folder).rglob("*.dart"))
    return {
        p.relative_to(FLUTTER).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def run(output, files, name=None):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Keep raw evidence private under API/.tmp")
    for file in files:
        target = (FLUTTER / file).resolve()
        if not target.is_relative_to(FLUTTER) or target.suffix != ".dart":
            raise ValueError("Only local Dart test paths are allowed")
    output.mkdir(parents=True, exist_ok=False)
    before = hashes()
    sdk = Path(os.environ.get("FLUTTER_SDK", "C:/flutter"))
    command = [
        str(sdk / "bin/cache/dart-sdk/bin/dart.exe"),
        str(sdk / "bin/cache/flutter_tools.snapshot"),
        "test",
        "--no-pub",
        "--machine",
        *files,
    ]
    if name:
        command.extend(["--name", name])
    manifest = {
        "flutter_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=FLUTTER, text=True
        ).strip(),
        "evaluated_content_sha256": before,
        "command": command,
        "physical_device": False,
        "network": "test mocks only; does not attach, reload or rebuild Android app",
    }
    (output / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "tests.jsonl").open("w", encoding="utf-8") as log:
        result = run_logged(
            command,
            cwd=FLUTTER,
            env=dict(os.environ, PUB_ENVIRONMENT="quality-campaign"),
            log=log,
            timeout=240,
        )
    names, cases = {}, []
    for line in (
        (output / "tests.jsonl")
        .read_text(encoding="utf-8", errors="replace")
        .splitlines()
    ):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "testStart":
            names[item["test"]["id"]] = item["test"]["name"]
        if item.get("type") == "testDone" and not item.get("hidden"):
            cases.append(
                {
                    "name": names.get(item["testID"], "unknown"),
                    "result": item["result"],
                    "skipped": item["skipped"],
                }
            )
    unchanged = before == hashes()
    summary = {
        "exit_code": result.returncode,
        "evaluated_sources_unchanged": unchanged,
        "cases": cases,
        "physical_device": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary), flush=True)
    return result.returncode if unchanged else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--name")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    sys.exit(run(args.output, args.files, args.name))
