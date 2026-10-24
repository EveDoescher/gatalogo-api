"""Run SEC-01 regression on disposable PostgreSQL without API lifespan or providers."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def source_hashes():
    paths = [*ROOT.glob("app/**/*.py"), ROOT / ".env.example", ROOT / "scripts/run_security_regression.py"]
    paths.extend(ROOT / "tests" / name for name in (
        "test_apple_identity.py", "test_auth_security.py", "test_conversation_events.py", "test_websocket_manager.py"))
    return {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def run(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Evidence must remain under .tmp")
    output.mkdir(parents=True, exist_ok=False)
    evaluated = source_hashes()
    (output / "source_manifest.json").write_text(json.dumps({
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "evaluated_content_sha256": evaluated,
        "note": "Working candidate identified by content hashes; commit recorded after validation.",
    }, indent=2) + "\n", encoding="utf-8")
    name = "gatalogo-sec01-" + uuid4().hex[:12]
    image = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", "gatalogo-postgres"], text=True
    ).strip()
    available = subprocess.run(["docker", "image", "inspect", image],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if available.returncode != 0:
        image = "gatalogo-quality-postgres:local"
        cached = subprocess.run(["docker", "image", "inspect", image], timeout=30,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if cached.returncode != 0:
            context = output / "build-context"
            context.mkdir()
            shutil.copy2(ROOT / "Dockerfile.postgres", context / "Dockerfile")
            with (output / "image-build.log").open("w", encoding="utf-8") as log:
                subprocess.run(["docker", "build", "-t", image, str(context)], timeout=300,
                               cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT)
    env = dict(os.environ, POSTGRES_PASSWORD=secrets.token_hex(24))
    created = False
    try:
        subprocess.run([
            "docker", "run", "--detach", "--name", name,
            "--tmpfs", "/var/lib/postgresql/data:rw,size=512m",
            "--label", "gatalogo.purpose=quality-integration",
            "-p", "127.0.0.1::5432", "-e", "POSTGRES_USER=gatalogo",
            "-e", "POSTGRES_DB=recognition_test", "-e", "POSTGRES_PASSWORD", image,
        ], env=env, check=True, stdout=subprocess.DEVNULL)
        created = True
        for _ in range(90):
            ready = subprocess.run([
                "docker", "exec", name, "pg_isready", "-U", "gatalogo", "-d", "recognition_test",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError("Disposable database did not become ready")
        address = subprocess.check_output(["docker", "port", name, "5432/tcp"], text=True).strip()
        port = address.rsplit(":", 1)[1]
        url = f"postgresql+asyncpg://gatalogo:{env['POSTGRES_PASSWORD']}@127.0.0.1:{port}/recognition_test"
        env.update(DATABASE_URL=url, RECOGNITION_TEST_DATABASE_URL=url,
                   JWT_SECRET=secrets.token_hex(32), GEMINI_API_KEY="", SMTP_ENABLED="false",
                   FCM_SERVICE_ACCOUNT_FILE="", PHOTO_STORAGE_DIR=str(output / "storage"),
                   ANALYSIS_CACHE_DIR=str(output / "cache"), PYTHONUTF8="1")
        with (output / "migrations.log").open("w", encoding="utf-8") as log:
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                           cwd=ROOT, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
        with (output / "pytest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run([
                sys.executable, "-m", "pytest", "-q", "-ra",
                "tests/test_apple_identity.py", "tests/test_auth_security.py",
                "tests/test_conversation_events.py", "tests/test_websocket_manager.py",
                "--junitxml", str(output / "junit.xml"),
            ], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=360)
        (output / "summary.json").write_text(json.dumps({
            "scenario": "SEC-01", "exit_code": result.returncode,
            "database": "own random-port PostgreSQL, migrations applied; fixture schema per case",
            "providers": "local RSA/JWKS and verifier doubles; no live identity provider",
            "api_lifespan_started": False, "physical_device": False,
            "evaluated_sources_unchanged": evaluated == source_hashes(),
        }, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"exit_code": result.returncode, "output": str(output)}), flush=True)
        return result.returncode if evaluated == source_hashes() else 2
    finally:
        if created:
            subprocess.run(["docker", "rm", "--force", "--volumes", name],
                           check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    raise SystemExit(run(parser.parse_args().output))
