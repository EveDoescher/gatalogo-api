"""Raw samples first; small pilot intentionally omits P99 and inferential claims."""

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import time


def resources():
    result = {"process_cpu_seconds": time.process_time(), "pid": os.getpid()}
    if os.name == "nt":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        process = kernel.GetCurrentProcess()
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            result.update(rss_bytes=counters.WorkingSetSize, peak_rss_bytes=counters.PeakWorkingSetSize)
            result.update(private_commit_bytes=counters.PagefileUsage, peak_private_commit_bytes=counters.PeakPagefileUsage)
        handles = wintypes.DWORD()
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        if kernel.GetProcessHandleCount(process, ctypes.byref(handles)):
            result["handles"] = handles.value
    return result


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize(samples):
    values = sorted(s["elapsed_ms"] for s in samples)
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50_ms_exploratory": values[math.ceil(len(values) * .5) - 1],
            "p95_ms_exploratory": values[math.ceil(len(values) * .95) - 1],
            "max_ms": values[-1], "p99": None,
            "percentile_method": "nearest-rank ceil(q*n)-1",
            "uncertainty": "pilot only; no confidence or P99 claim"}
