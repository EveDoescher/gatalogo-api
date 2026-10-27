"""Limits for this disposable Python process only; never change user processes.

Win32 reference:
https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getprocessaffinitymask
"""

import ctypes
from ctypes import wintypes
import os

_JOBS = []  # Keep handles alive until this disposable process exits.


def kernel():
    if os.name != "nt":
        raise RuntimeError("This executor requires Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p]
    api.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    return api


def affinity():
    api = kernel()
    process, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not api.GetProcessAffinityMask(api.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    return process.value


def set_affinity(mask):
    api = kernel()
    if not api.SetProcessAffinityMask(api.GetCurrentProcess(), mask):
        raise ctypes.WinError(ctypes.get_last_error())
    assert affinity() == mask


def cap_private_commit(size):
    class Basic(ctypes.Structure):
        _fields_ = [
            ("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSet", ctypes.c_size_t),
            ("MaximumWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperations", "WriteOperations", "OtherOperations",
            "ReadBytes", "WriteBytes", "OtherBytes",
        )]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", Basic), ("IO", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    api = kernel()
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.Basic.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY, not an RSS cap.
    info.ProcessMemoryLimit = size
    try:
        if not api.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = Extended()
        if not api.QueryInformationJobObject(handle, 9, ctypes.byref(actual), ctypes.sizeof(actual), None):
            raise ctypes.WinError(ctypes.get_last_error())
        assert actual.ProcessMemoryLimit == size and actual.Basic.LimitFlags & 0x100
    except BaseException:
        api.CloseHandle(handle)
        raise
    _JOBS.append(handle)
    return {"private_commit_limit_bytes": actual.ProcessMemoryLimit, "limit_verified_by_win32_query": True}
