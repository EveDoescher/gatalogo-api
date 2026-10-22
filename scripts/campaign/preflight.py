"""Bounded Windows host observation; never runs requests or authorizes a benchmark."""

import argparse
import ctypes
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time


def system_times():
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        raise OSError("GetSystemTimes failed")
    return idle.value, kernel.value + user.value


def available_memory():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (key, ctypes.c_ulonglong)
            for key in (
                "total_physical",
                "available_physical",
                "total_page",
                "available_page",
                "total_virtual",
                "available_virtual",
                "extended",
            )
        ]

    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError("Memory observation failed")
    return memory.available_physical


def observe(destination, samples, interval, label):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Never overwrite observation")
    if os.name != "nt" or not 3 <= samples <= 100 or not 0.1 <= interval <= 1:
        raise ValueError("Bounded Windows-only collector")
    rows = []
    previous = system_times()
    for _ in range(samples):
        time.sleep(interval)
        current = system_times()
        total = current[1] - previous[1]
        rows.append(
            {
                "utc": datetime.now(UTC).isoformat(),
                "cpu_busy_fraction": 1 - (current[0] - previous[0]) / total
                if total
                else None,
                "available_physical_bytes": available_memory(),
            }
        )
        previous = current
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "label": label,
                "samples": rows,
                "confirmatory_ready": False,
                "limitations": [
                    "Observes host CPU/memory only; thermal/power mode/IO stationarity require dedicated preconditions",
                    "No endpoint requests or product changes",
                    "Concurrent functional tests invalidate comparative performance interpretation",
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    observe(args.output, args.samples, args.interval, args.label)
