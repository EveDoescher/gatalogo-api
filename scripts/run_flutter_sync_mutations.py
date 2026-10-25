"""Bounded synchronization mutants in separate copies; never edits the app checkout."""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from campaign.critical_report import flutter_tests
from campaign.subprocesses import run_logged
from run_flutter_regression import hashes, FLUTTER, ROOT

FILE = "lib/services/catalog_sync_service.dart"
RECIPES = {
    "SYNC-BATCH-01": (
        "static const _pushBatchSize = 500;",
        "static const _pushBatchSize = 1000;",
    ),
    "SYNC-BATCH-02": (
        "final ordered = _orderForBatches(cats);",
        "final ordered = cats;",
    ),
    "SYNC-BATCH-03": ("start += _pushBatchSize", "start += _pushBatchSize + 1"),
    "SYNC-MEDIA-01": (
        "throw const HttpException('Foto complementar ainda não recebida.');",
        "continue;",
    ),
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def snapshot(target):
    target.mkdir(parents=True, exist_ok=False)
    shutil.copytree(FLUTTER / "lib", target / "lib")
    for name in [
        "pubspec.yaml",
        "pubspec.lock",
        "test/catalog_push_batches_test.dart",
        "test/reference_media_sync_test.dart",
        "tool/campaign_resilience_test.dart",
        ".dart_tool/package_config.json",
        ".dart_tool/package_graph.json",
    ]:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FLUTTER / name, destination)


def execute(folder):
    sdk = Path(os.environ.get("FLUTTER_SDK", "C:/flutter"))
    with (folder / "tests.jsonl").open("w", encoding="utf-8") as log:
        result = run_logged(
            [
                str(sdk / "bin/cache/dart-sdk/bin/dart.exe"),
                str(sdk / "bin/cache/flutter_tools.snapshot"),
                "test",
                "--no-pub",
                "--no-test-assets",
                "--machine",
                "test/catalog_push_batches_test.dart",
                "test/reference_media_sync_test.dart",
            ],
            cwd=folder,
            env=dict(os.environ),
            log=log,
            timeout=120,
        )
    cases = flutter_tests(folder / "tests.jsonl")
    return {"exit_code": result.returncode, "cases": cases}


def run(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Evidence must remain private under API/.tmp")
    output.mkdir(parents=True, exist_ok=False)
    before = hashes()
    save(
        output / "source_manifest.json",
        {
            "flutter_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=FLUTTER, text=True
            ).strip(),
            "evaluated_content_sha256": before,
            "assets": "Not built: pure protocol tests, no rendering",
            "external_network": "No API endpoints; MockClient only",
        },
    )
    base = output / "baseline"
    snapshot(base)
    baseline = execute(base)
    save(base / "summary.json", baseline)
    assert (
        baseline["exit_code"] == 0
        and len(baseline["cases"]) == 17
        and all(
            c["result"] == "success" and not c["skipped"] for c in baseline["cases"]
        )
    )
    expected = {c["name"] for c in baseline["cases"]}
    results = []
    for name, (old, new) in RECIPES.items():
        target = output / name
        snapshot(target)
        source = target / FILE
        original = source.read_text(encoding="utf-8")
        assert original.count(old) == 1
        modified = original.replace(old, new, 1)
        source.write_text(modified, encoding="utf-8")
        (target / "mutation.diff").write_text(
            "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    modified.splitlines(keepends=True),
                    fromfile=FILE,
                    tofile=FILE,
                )
            ),
            encoding="utf-8",
        )
        result = execute(target)
        log = target / "tests.jsonl"
        names, assertions = {}, set()
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "testStart":
                names[item["test"]["id"]] = item["test"]["name"]
            if item.get("type") == "error" and item.get("isFailure"):
                assertions.add(names.get(item["testID"]))
        failed = [
            c["name"]
            for c in result["cases"]
            if c["name"] in expected & assertions
            and c["result"] in ("failure", "error")
            and not c["skipped"]
        ]
        complete = (
            len(result["cases"]) == len(expected)
            and {c["name"] for c in result["cases"]} == expected
        )
        verdict = (
            "KILLED"
            if complete and result["exit_code"] == 1 and failed
            else "SURVIVED"
            if complete and result["exit_code"] == 0
            else "INCONCLUSIVE"
        )
        result.update(
            mutant=name,
            classification=verdict,
            verified_assertions=failed,
            jsonl_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
        )
        save(target / "summary.json", result)
        results.append(result)
        print(name + ": " + verdict, flush=True)
    unchanged = hashes() == before
    save(
        output / "summary.json",
        {
            "baseline": baseline,
            "mutants": results,
            "evaluated_sources_unchanged": unchanged,
        },
    )
    assert unchanged and all(r["classification"] == "KILLED" for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    run(parser.parse_args().output)
