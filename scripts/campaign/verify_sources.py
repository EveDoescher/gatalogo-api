"""Verify measured app/lib files against their Git blobs after an isolated run."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess


def verify(run):
    run = Path(run).resolve()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    results = {}
    for name, source in manifest["sources"].items():
        root = Path(source["path"])
        scope = "app" if name.endswith("api") else "lib"
        listing = subprocess.check_output(["git", "-C", source["repo"], "ls-tree", "-r", source["sha"], "--", scope], text=True)
        count = 0
        eol_only = 0
        for line in listing.splitlines():
            meta, filename = line.split("\t", 1)
            blob = meta.split()[2]
            data = (root / filename).read_bytes()
            actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if actual != blob:
                normalized = data.replace(b"\r\n", b"\n")
                text_file = Path(filename).suffix in (".py", ".dart", ".json", ".txt", ".md", ".yaml", ".toml", ".csv", ".html", ".svg")
                normalized_hash = hashlib.sha1(b"blob " + str(len(normalized)).encode() + b"\0" + normalized).hexdigest()
                if not text_file or normalized_hash != blob:
                    raise AssertionError(f"Measured product source changed: {name}/{filename}")
                eol_only += 1
            count += 1
        results[name] = {"files_verified": count, "sha": source["sha"], "state": "UNCHANGED_CONTENT",
                         "git_crlf_translation_only": eol_only}
    hardware = {"cpu": platform.processor(), "logical_cpus": os.cpu_count(), "os": platform.platform()}
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (key, ctypes.c_ulonglong) for key in ("total_physical", "available_physical", "total_page",
                                                    "available_page", "total_virtual", "available_virtual", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            hardware["physical_ram_bytes"] = memory.total_physical
    results["hardware_observed_at_verification"] = hardware
    target = run / "product_source_verification.json"
    if target.exists():
        raise ValueError("Never overwrite verification evidence")
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    verify(parser.parse_args().run)
