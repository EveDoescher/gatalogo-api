"""Bounded B-only desktop catalog observations; no phone/online latency claim."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import time

from critical_report import flutter_tests
from subprocesses import run_logged


def run(campaign, attempt):
    campaign = Path(campaign).resolve()
    stage = campaign / "attempts" / attempt
    output = stage / "flutter_catalog"
    output.mkdir(exist_ok=False)
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    root = Path(manifest["sources"]["B-flutter"]["path"])
    for filename in ("package_config.json", "package_graph.json"):
        shutil.copy2(repo / ".dart_tool" / filename, root / ".dart_tool" / filename)
    tool = stage / "executors/campaign_probe_test.dart"
    shutil.copy2(tool, root / "test" / tool.name)
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))
    rows = []
    try:
        for count in [0, 40, 501]:
            folder = output / str(count)
            folder.mkdir()
            started = time.perf_counter()
            with (folder / "tests.jsonl").open("w", encoding="utf-8") as log:
                result = run_logged(
                    [
                        shutil.which("flutter"),
                        "test",
                        "--no-pub",
                        "--reporter",
                        "json",
                        f"--dart-define=API_BASE_URL=http://127.0.0.1:{reserved.getsockname()[1]}",
                        "test/" + tool.name,
                    ],
                    cwd=root,
                    env=dict(
                        os.environ,
                        QUALITY_FLUTTER_OUTPUT=str(folder / "samples.json"),
                        QUALITY_FLUTTER_COUNT=str(count),
                        QUALITY_FLUTTER_NO_GUEST="1",
                    ),
                    log=log,
                    timeout=240,
                )
            cases = flutter_tests(folder / "tests.jsonl")
            rows.append(
                {
                    "count": count,
                    "exit_code": result.returncode,
                    "elapsed_seconds": time.perf_counter() - started,
                    "cases": cases,
                }
            )
            if result.returncode:
                break
            data = json.loads((folder / "samples.json").read_text(encoding="utf-8"))
            assert data["expected_visible"] == count
            assert all(s["visible"] == count for s in data["samples"])
        summary = {
            "scenario": "LAT-01",
            "exit_code": int(any(r["exit_code"] for r in rows)),
            "volumes": rows,
            "cases": [c for row in rows for c in row["cases"]],
            "source_sha": manifest["sources"]["B-flutter"]["sha"],
            "scope": "real desktop SQLite/public loadCats, fresh process+connection then 22 repeats (first3 excluded from warm summary); OS disk cache not controlled. Offline paths overridden; no online, UI frame, phone latency, confidence/P99/gain claim.",
        }
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"exit_code": summary["exit_code"], "volumes": len(rows)}))
        return summary["exit_code"]
    finally:
        reserved.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.run, args.attempt))
