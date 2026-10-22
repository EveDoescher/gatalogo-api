"""Run tracked frozen headless tests with no reachable application backend."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from subprocesses import run_logged
import time


def run(campaign, attempt):
    campaign = Path(campaign).resolve()
    stage = campaign / "attempts" / attempt
    output = stage / "flutter_full"
    output.mkdir(exist_ok=False)
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
    source = manifest["sources"]["B-flutter"]
    target = Path(source["path"])
    repo = Path(source["repo"])
    files = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "--name-only",
            source["sha"],
            "--",
            "test",
        ],
        text=True,
    ).splitlines()
    files = [f for f in files if f.endswith("_test.dart")]
    # Excludes injected audit files by using Git's frozen file list, not globs.
    assert files and all((target / f).is_file() for f in files)
    for filename in ("package_config.json", "package_graph.json"):
        shutil.copy2(repo / ".dart_tool" / filename, target / ".dart_tool" / filename)

    def preserve_previews(label):
        # Keep generated visual evidence outside reproducible sources for archival.
        for folder in (
            target / "test" / "previews",
            target / "build" / "test_previews",
        ):
            if folder.is_dir():
                shutil.copytree(folder, output / (label + "-" + folder.name))

    preserve_previews("before")
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))
    try:
        base = "http://127.0.0.1:" + str(reserved.getsockname()[1])
        write = {
            "source_sha": source["sha"],
            "files": files,
            "application_base": base,
            "note": "Socket bound without listening; no user backend can receive unmocked requests. No emulator/device.",
        }
        (output / "invocation.json").write_text(
            json.dumps(write, indent=2) + "\n", encoding="utf-8"
        )
        started = time.perf_counter()
        with (output / "tests.jsonl").open("w", encoding="utf-8") as log:
            result = run_logged(
                [
                    shutil.which("flutter"),
                    "test",
                    "--no-pub",
                    "--reporter",
                    "json",
                    "--dart-define=API_BASE_URL=" + base,
                    *files,
                ],
                cwd=target,
                env=dict(
                    os.environ,
                    GEMINI_API_KEY="",
                    FCM_SERVICE_ACCOUNT_FILE="",
                    SMTP_ENABLED="false",
                ),
                log=log,
                timeout=1800,
            )
        names, cases = {}, []
        for line in (
            (output / "tests.jsonl")
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        ):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "testStart":
                names[d["test"]["id"]] = d["test"]["name"]
            if d.get("type") == "testDone" and not d.get("hidden"):
                cases.append(
                    {
                        "name": names.get(d["testID"], "unknown"),
                        "result": d["result"],
                        "skipped": d["skipped"],
                    }
                )
        summary = {
            "exit_code": result.returncode,
            "elapsed_seconds": time.perf_counter() - started,
            "cases": cases,
            "source_sha": source["sha"],
            "scope": "Headless regression; skipped live integration is not counted as executed",
        }
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return result.returncode
    finally:
        reserved.close()
        preserve_previews("after")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.run, args.attempt))
