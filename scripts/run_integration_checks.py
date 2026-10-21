"""Run all opt-in integrations against an ephemeral PostgreSQL and local API.

Requires Docker, the project's Python environment, and the sibling Flutter repo.
Reuses the image of gatalogo-postgres, or builds Dockerfile.postgres if absent.
The disposable database uses a bounded tmpfs to avoid host-volume fsync latency.
Never migrates the user's database. Optional arguments select pytest scenarios.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def command(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main(pytest_args=None):
    run_id = uuid4().hex[:12]
    name = f"gatalogo-quality-{run_id}"
    logs = ROOT / ".tmp" / f"integration-{run_id}"
    logs.mkdir(parents=True)
    image = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", "gatalogo-postgres"], text=True
    ).strip()
    available = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if available.returncode != 0:
        image = "gatalogo-quality-postgres:local"
        with (logs / "build.log").open("w", encoding="utf-8") as output:
            command(
                "docker",
                "build",
                "-f",
                "Dockerfile.postgres",
                "-t",
                image,
                ".",
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
    env = dict(os.environ)
    env["POSTGRES_PASSWORD"] = secrets.token_hex(24)
    api = None
    created = False
    try:
        command(
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=1g",
            "--label",
            "gatalogo.purpose=quality-integration",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_USER=gatalogo",
            "-e",
            "POSTGRES_DB=recognition_test",
            "-e",
            "POSTGRES_PASSWORD",
            image,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        created = True
        for _ in range(180):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "gatalogo",
                    "-d",
                    "recognition_test",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            with (logs / "postgres.log").open("w", encoding="utf-8") as output:
                subprocess.run(["docker", "logs", name], stdout=output, stderr=subprocess.STDOUT)
            raise RuntimeError(f"Disposable PostgreSQL did not become ready; see {logs / 'postgres.log'}")
        address = subprocess.check_output(
            ["docker", "port", name, "5432/tcp"], text=True
        ).strip()
        port = address.rsplit(":", 1)[1]
        database = f"postgresql+asyncpg://gatalogo:{env['POSTGRES_PASSWORD']}@127.0.0.1:{port}/recognition_test"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            api_port = sock.getsockname()[1]
        env.update(
            DATABASE_URL=database,
            RECOGNITION_TEST_DATABASE_URL=database,
            QUINTAL_POSTGRES_TEST="1",
            QUINTAL_LIVE_API_TEST="1",
            QUINTAL_LIVE_FLUTTER_TEST="1",
            QUINTAL_LIVE_API_URL=f"http://127.0.0.1:{api_port}",
            SMTP_ENABLED="false",
            JWT_SECRET=secrets.token_hex(32),
            PYTHONUNBUFFERED="1",
        )
        print(f"Isolated PostgreSQL ready; logs: {logs}", flush=True)
        with (logs / "migrations.log").open("w", encoding="utf-8") as output:
            command(
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "head",
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        print("Migrations applied only to the disposable database.", flush=True)
        with (logs / "api.log").open("w", encoding="utf-8") as output:
            api = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for _ in range(180):
                if api.poll() is not None:
                    raise RuntimeError("Isolated API exited; inspect api.log")
                try:
                    with urlopen(
                        f"http://127.0.0.1:{api_port}/health", timeout=2
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Isolated API did not become ready")
            print(
                "Running the full suite with all integration flags enabled.", flush=True
            )
            with (logs / "pytest.log").open("w", encoding="utf-8") as test_output:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-ra", *(pytest_args or [])],
                    cwd=ROOT,
                    env=env,
                    stdout=test_output,
                    stderr=subprocess.STDOUT,
                )
            print(
                f"pytest exit code: {result.returncode}; report: {logs / 'pytest.log'}",
                flush=True,
            )
            return result.returncode
    finally:
        if api is not None:
            if os.name == "nt" and api.poll() is None:
                # A Windows venv executable may wrap another Python process.
                # Terminate only the process tree launched by this runner.
                subprocess.run(
                    ["taskkill", "/PID", str(api.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            elif api.poll() is None:
                api.terminate()
            try:
                api.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()
        if created:
            command(
                "docker", "rm", "--force", "--volumes", name, stdout=subprocess.DEVNULL
            )
            print("Disposable API and PostgreSQL removed.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
