"""Reload the existing debug app through Flutter's machine protocol, then detach."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ADB = "C:/Android/Sdk/platform-tools/adb.exe"


def run(args):
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Private evidence must stay under API/.tmp")
    output.mkdir(parents=True, exist_ok=False)
    logs = Path(args.source_log).read_text(encoding="utf-8")
    matches = re.findall(r"Dart VM service is listening on (http://[^\s]+)", logs)
    assert matches
    uri = urlparse(matches[-1].rstrip(".,"))
    assert uri.hostname in ("127.0.0.1", "localhost")

    def forwards():
        raw = subprocess.check_output([ADB, "forward", "--list"], text=True)
        return {
            tuple(line.split()) for line in raw.splitlines() if len(line.split()) == 3
        }

    before_forwards = forwards()
    events = queue.Queue()
    process, app_id, detached, reader = None, None, False, None
    summary = {
        "full_restart": False,
        "rebuild_apk": False,
        "defines": {"API_BASE_URL": args.api_url},
        "preservation_basis": "User confirmed API_BASE_URL was the only supplied Dart define",
    }
    try:
        command = [
            "C:/flutter/bin/cache/dart-sdk/bin/dart.exe",
            "C:/flutter/bin/cache/flutter_tools.snapshot",
            "attach",
            "--machine",
            "--no-dds",
            "--device-user=0",
            "-d",
            args.serial,
            "--app-id=com.example.catlogue",
            "--debug-url",
            # Attach forwards the DEVICE URI itself; forwarding it beforehand
            # would create a second mapping to a nonexistent device port.
            uri.geturl(),
            "--dart-define=API_BASE_URL=" + args.api_url,
        ]
        process = subprocess.Popen(
            command,
            cwd=ROOT.parent / "catlogue",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        def read():
            with (output / "private-attach.jsonl").open("w", encoding="utf-8") as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    try:
                        items = json.loads(line)
                    except ValueError:
                        continue
                    for item in items if isinstance(items, list) else [items]:
                        if isinstance(item, dict):
                            events.put(item)
            events.put({"process_exit": True})

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def receive(timeout):
            return events.get(timeout=timeout)

        def send(ident, method, params):
            process.stdin.write(
                json.dumps([{"id": ident, "method": method, "params": params}]) + "\n"
            )
            process.stdin.flush()

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            event = receive(max(1, deadline - time.monotonic()))
            if event.get("process_exit"):
                raise RuntimeError("Attach exited before readiness")
            if event.get("event") in ("app.start", "app.started"):
                app_id = event["params"]["appId"]
            if event.get("event") == "app.started":
                break
        assert app_id
        invalidated = {}
        for name in args.invalidate:
            path = (ROOT.parent / "catlogue" / name).resolve()
            if (
                not path.is_relative_to(ROOT.parent / "catlogue" / "lib")
                or not path.is_file()
            ):
                raise ValueError(
                    "Only existing project library sources can be invalidated"
                )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            os.utime(path, None)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            invalidated[name] = digest
        summary["invalidated_content_sha256"] = invalidated
        print("Attached to existing app; requesting hot reload", flush=True)
        send(
            1,
            "app.restart",
            {
                "appId": app_id,
                "fullRestart": False,
                "pause": False,
                "reason": "Validated XP accessibility correction",
            },
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            event = receive(max(1, deadline - time.monotonic()))
            if event.get("process_exit"):
                raise RuntimeError("Attach exited during reload")
            if event.get("id") == 1:
                summary["reload_result"] = event.get("result")
                assert "error" not in event and event.get("result", {}).get("code") == 0
                print("Hot reload succeeded", flush=True)
                break
        assert summary.get("reload_result", {}).get("code") == 0
        send(2, "app.detach", {"appId": app_id})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            event = receive(max(1, deadline - time.monotonic()))
            if event.get("id") == 2:
                detached = event.get("result") is True
                break
            if event.get("process_exit"):
                break
        summary["state"] = "RELOADED" if detached else "RELOADED_DETACH_UNCONFIRMED"
    except Exception as error:
        summary.update(state="INCONCLUSIVE", error_type=type(error).__name__)
    finally:
        if process and process.poll() is None:
            if app_id and not detached:
                try:
                    process.stdin.write(
                        json.dumps(
                            [
                                {
                                    "id": 99,
                                    "method": "app.detach",
                                    "params": {"appId": app_id},
                                }
                            ]
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                except OSError:
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                )
        if process:
            try:
                process.stdin.close()
            except OSError:
                pass
        if reader:
            reader.join(timeout=2)
        removed = []
        for serial, local, remote in forwards() - before_forwards:
            if serial == args.serial and remote == "tcp:" + str(uri.port):
                result = subprocess.run(
                    [ADB, "-s", serial, "forward", "--remove", local],
                    capture_output=True,
                )
                removed.append({"local": local, "removed": result.returncode == 0})
        summary["own_forward_cleanup"] = removed
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary))
    return 0 if summary.get("state") == "RELOADED" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--source-log", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--invalidate", nargs="*", default=[])
    raise SystemExit(run(parser.parse_args()))
