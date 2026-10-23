"""Run SDK guideline audits on more real pages; red findings remain explicit."""

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
    output = stage / "accessibility_flows"
    output.mkdir(exist_ok=False)
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
    root = Path(manifest["sources"]["B-flutter"]["path"])
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    for filename in ["package_config.json", "package_graph.json"]:
        shutil.copy2(repo / ".dart_tool" / filename, root / ".dart_tool" / filename)
    for filename in [
        "campaign_accessibility_test.dart",
        "campaign_flow_accessibility_test.dart",
    ]:
        shutil.copy2(stage / "executors" / filename, root / "test" / filename)
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))
    started = time.perf_counter()
    try:
        with (output / "tests.jsonl").open("w", encoding="utf-8") as log:
            result = run_logged(
                [
                    shutil.which("flutter"),
                    "test",
                    "--no-pub",
                    "--reporter",
                    "json",
                    "--concurrency=1",
                    f"--dart-define=API_BASE_URL=http://127.0.0.1:{reserved.getsockname()[1]}",
                    "test/campaign_flow_accessibility_test.dart",
                ],
                cwd=root,
                env=dict(os.environ, QUALITY_ACC_OUTPUT=str(output / "observations")),
                log=log,
                timeout=900,
            )
        summary = {
            "exit_code": result.returncode,
            "elapsed_seconds": time.perf_counter() - started,
            "cases": flutter_tests(output / "tests.jsonl"),
            "observation_files": len(list((output / "observations").glob("*.json"))),
            "scope": "real B widgets/fixture data, SDK guidelines in 48 cells plus evidence-path check; two scroll positions; no reader/device or animation-compliance certification",
        }
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "exit_code": summary["exit_code"],
                    "cases": len(summary["cases"]),
                    "observation_files": summary["observation_files"],
                }
            )
        )
        return result.returncode
    finally:
        reserved.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.run, args.attempt))
