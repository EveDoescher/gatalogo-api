"""Run previously optional local render/capture cases in the frozen Flutter copy."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import time

from critical_report import flutter_tests
from subprocesses import run_logged

FILES = [
    "test/corrections_preview_test.dart",
    "test/product_preview_test.dart",
    "test/habitat_visit_capture_test.dart",
    "test/quintal_visual_validation_test.dart",
    "test/visual_validation_capture_test.dart",
]


def run(campaign, attempt, arm="B", goldens_only=False):
    campaign = Path(campaign).resolve()
    stage = campaign / "attempts" / attempt
    output = stage / "flutter_visual"
    output.mkdir(exist_ok=False)
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
    root = Path(manifest["sources"][arm + "-flutter"]["path"])
    assert root.is_relative_to(campaign / "sources")
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    binary = Path(shutil.which("flutter"))
    font = binary.parent / "cache/artifacts/material_fonts/Roboto-Regular.ttf"
    assert font.is_file() and font.with_name("MaterialIcons-Regular.otf").is_file()
    for name in ["package_config.json", "package_graph.json"]:
        shutil.copy2(repo / ".dart_tool" / name, root / ".dart_tool" / name)

    def preserve(label):
        for suffix in [
            "test/previews",
            "test/failures",
            "build/galeria-visual",
            "build/validation-cache",
        ]:
            original = root / suffix
            if original.is_dir():
                shutil.copytree(original, output / label / suffix)

    selected = FILES[:2] if goldens_only else FILES
    preserve("before")
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))
    base = "http://127.0.0.1:" + str(reserved.getsockname()[1])
    flags = {
        "GATALOGO_PREVIEWS": "1",
        "GATALOGO_PREVIEW_FONT": str(font),
        "GATALOGO_CAPTURE_MOTION": "1",
        "GATALOGO_CAPTURE_QUINTAL": "1",
        "GATALOGO_CAPTURE_ALL": "1",
    }
    (output / "invocation.json").write_text(
        json.dumps(
            {
                "files": selected,
                "arm": arm,
                "flags": flags,
                "source_sha": manifest["sources"][arm + "-flutter"]["sha"],
                "application_base": base,
                "golden_updates": False,
                "scope": "headless local renderer; existing golden comparisons and artifact capture, no device",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    started = time.perf_counter()
    try:
        with (output / "tests.jsonl").open("w", encoding="utf-8") as log:
            result = run_logged(
                [
                    str(binary),
                    "test",
                    "--no-pub",
                    "--reporter",
                    "json",
                    "--concurrency=1",
                    "--dart-define=API_BASE_URL=" + base,
                    *selected,
                ],
                cwd=root,
                env=dict(os.environ, **flags),
                log=log,
                timeout=1200,
            )
        summary = {
            "exit_code": result.returncode,
            "elapsed_seconds": time.perf_counter() - started,
            "cases": flutter_tests(output / "tests.jsonl"),
            "scope": "visual captures and current golden comparison only; differences are findings, never automatically accepted",
        }
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return result.returncode
    finally:
        reserved.close()
        preserve("after")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--arm", choices=["A", "B"], default="B")
    parser.add_argument("--goldens-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.run, args.attempt, args.arm, args.goldens_only))
