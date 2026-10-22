"""Bounded subprocesses with cleanup limited to the process tree we launched."""

import os
import subprocess


def run_logged(command, *, cwd, env, log, timeout):
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt" and process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.write(
            "\n[campaign] Timeout: only the launched process tree was terminated.\n"
        )
        log.flush()
        code = 124
    return subprocess.CompletedProcess(command, code)
