"""Read-only source attestation from the connected debug app; no attach/reload/install."""

import argparse
import asyncio
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
ADB = Path("C:/Android/Sdk/platform-tools/adb.exe")
CLIENT = ROOT.parent / "catlogue"


async def probe(output, serial, source_log=None):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Private evidence must stay under API/.tmp")
    output.mkdir(parents=True, exist_ok=False)

    def adb(*args):
        return (
            subprocess.check_output([str(ADB), "-s", serial, *args], timeout=20)
            .decode("utf-8", errors="replace")
            .strip()
        )

    info = {
        "date_utc": datetime.now(UTC).isoformat(),
        "package": "com.example.catlogue",
        "actions": "Read PID/logs/VM source hashes; own loopback forward only. No code evaluation, attach, reload, install, preferences or app mutation.",
    }
    forwarded = None
    try:
        pid = adb("shell", "pidof", info["package"])
        if not pid.isdecimal():
            raise RuntimeError("App process not available")
        logs = adb("logcat", "-d", "--pid=" + pid, "-s", "flutter:I")
        (output / "private-flutter-log.txt").write_text(logs, encoding="utf-8")
        candidates = re.findall(
            r"(?:Dart VM service is listening on|Dart VM Service[^\n]*available at)\s+(http://[^\s]+)",
            logs,
        )
        if not candidates and source_log:
            saved = Path(source_log).read_text(encoding="utf-8")
            lines = [
                line
                for line in saved.splitlines()
                if re.search(r"\b" + pid + r"\b", line)
            ]
            candidates = re.findall(
                r"Dart VM service is listening on (http://[^\s]+)", "\n".join(lines)
            )
            info["discovery"] = "saved VM log from same currently running Android PID"
        if not candidates:
            info["state"] = "VM_URI_NOT_IN_RETAINED_LOG"
            return info
        uri = urlparse(candidates[-1].rstrip(".,"))
        if uri.hostname not in ("127.0.0.1", "localhost") or not uri.port:
            raise ValueError("Expected loopback VM URI")
        forwarded = adb("forward", "tcp:0", "tcp:" + str(uri.port))
        if not forwarded.isdecimal():
            raise ValueError("Expected own allocated loopback port")
        counter = 0
        async with connect(
            f"ws://127.0.0.1:{forwarded}{uri.path.rstrip('/')}/ws",
            proxy=None,
            open_timeout=5,
            close_timeout=2,
            max_size=8 * 1024 * 1024,
        ) as ws:

            async def rpc(method, params=None):
                nonlocal counter
                counter += 1
                ident = str(counter)
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": ident,
                            "method": method,
                            "params": params or {},
                        }
                    )
                )
                async with asyncio.timeout(5):
                    while True:
                        reply = json.loads(await ws.recv())
                        if reply.get("id") == ident:
                            if "error" in reply:
                                raise RuntimeError("VM read method rejected")
                            return reply["result"]

            vm = await rpc("getVM")
            isolates = [i for i in vm.get("isolates", []) if i.get("name") == "main"]
            if len(isolates) != 1:
                raise ValueError("Expected one main app isolate")
            isolate = isolates[0]["id"]
            scripts = await rpc("getScripts", {"isolateId": isolate})
            results = []
            for script in scripts.get("scripts", []):
                uri = script.get("uri", "")
                if not uri.startswith("package:catlogue/"):
                    continue
                relative = uri.removeprefix("package:catlogue/")
                local = (CLIENT / "lib" / relative).resolve()
                if not local.is_relative_to(CLIENT / "lib") or not local.is_file():
                    continue
                obj = await rpc(
                    "getObject", {"isolateId": isolate, "objectId": script["id"]}
                )
                source = obj.get("source")
                if not isinstance(source, str):
                    results.append(
                        {"path": "lib/" + relative, "state": "SOURCE_UNAVAILABLE"}
                    )
                    continue
                normalized = source.replace("\r\n", "\n").encode("utf-8")
                expected = (
                    local.read_text(encoding="utf-8")
                    .replace("\r\n", "\n")
                    .encode("utf-8")
                )
                results.append(
                    {
                        "path": "lib/" + relative,
                        "state": "MATCH" if normalized == expected else "DIFFERENT",
                        "runtime_sha256": hashlib.sha256(normalized).hexdigest(),
                        "working_tree_sha256": hashlib.sha256(expected).hexdigest(),
                    }
                )
            (output / "source-attestation.json").write_text(
                json.dumps(results, indent=2) + "\n", encoding="utf-8"
            )
            info.update(
                state="READ",
                loaded_app_scripts=len(results),
                matched=sum(r["state"] == "MATCH" for r in results),
                different=sum(r["state"] == "DIFFERENT" for r in results),
                source_unavailable=sum(
                    r["state"] == "SOURCE_UNAVAILABLE" for r in results
                ),
                flutter_head=subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=CLIENT, text=True
                ).strip(),
            )
    except Exception as error:
        info.update(state="INCONCLUSIVE", error_type=type(error).__name__)
    finally:
        if forwarded and forwarded.isdecimal():
            adb("forward", "--remove", "tcp:" + forwarded)
        (output / "summary.json").write_text(
            json.dumps(info, indent=2) + "\n", encoding="utf-8"
        )
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--source-log")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.output, args.serial, args.source_log))))
