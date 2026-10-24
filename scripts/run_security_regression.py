"""Run bounded security/integrity cases on disposable PostgreSQL and frozen sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def source_hashes():
    paths = [
        *ROOT.glob("app/**/*.py"),
        ROOT / ".env.example",
        ROOT / "scripts/run_security_regression.py",
    ]
    paths.extend(
        ROOT / "tests" / name
        for name in (
            "test_apple_identity.py",
            "test_auth_security.py",
            "test_conversation_events.py",
            "test_websocket_manager.py",
        )
    )
    paths.extend(ROOT.glob("scripts/campaign/*.py"))
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(paths)
    }


def selection(scenario, output):
    if scenario == "SEC-01":
        return (
            ROOT,
            [
                "tests/test_apple_identity.py",
                "tests/test_auth_security.py",
                "tests/test_conversation_events.py",
                "tests/test_websocket_manager.py",
            ],
            {},
        )
    campaign = next(
        parent for parent in output.parents if (parent / "manifest.json").is_file()
    )
    source = campaign / "sources" / output.name / "api"
    source.mkdir(parents=True, exist_ok=False)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    archive = output / "candidate.tar"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=tar",
            "-o",
            str(archive),
            sha,
            "--",
            "app",
        ],
        cwd=ROOT,
        check=True,
    )
    with tarfile.open(archive) as snapshot:
        snapshot.extractall(source, filter="data")
    executors = output / "executors"
    executors.mkdir()
    hashes = {}
    names = [
        "security_cases.py",
        "extended_cases.py",
        "fixtures.py",
    ]
    case_file = {
        "SEC-02": "isolation_cases.py",
        "SEC-03": "websocket_cases.py",
        "SEC-05": "storage_cases.py",
        "SEC-07": "auth_flow_cases.py",
        "DATA-02": "rollback_cases.py",
        "DATA-05": None,
        "DATA-06": "fencing_cases.py",
        "DATA-07": "production_boundary_cases.py",
    }[scenario]
    if case_file:
        names.append(case_file)
    if scenario == "SEC-03":
        names.append("test_auth_security.py")
    if scenario == "SEC-07":
        names.append("delivery_cases.py")
    for name in names:
        destination = executors / name
        origin = (
            ROOT
            / ("tests" if name == "test_auth_security.py" else "scripts/campaign")
            / name
        )
        shutil.copy2(origin, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output / "candidate_manifest.json").write_text(
        json.dumps({"source_sha": sha, "executor_sha256": hashes}, indent=2) + "\n",
        encoding="utf-8",
    )
    selected = ["security_cases.py", "extended_cases.py"]
    if case_file:
        selected.append(case_file)
    if scenario == "SEC-07":
        selected.append("delivery_cases.py")
    selectors = [str(executors / name) for name in selected]
    selectors += ["-k", scenario.replace("-", ""), "--confcutdir", str(executors)]
    return (
        source,
        selectors,
        dict(
            QUALITY_CAMPAIGN_RUN=str(campaign),
            QUALITY_CAMPAIGN_ATTEMPT=str(output),
            QUALITY_TARGET_SOURCE=str(source),
            QUALITY_CASE_EVIDENCE=str(output / "observations"),
        ),
    )


def run(output, scenario="SEC-01"):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Evidence must remain under .tmp")
    output.mkdir(parents=True, exist_ok=False)
    evaluated = source_hashes()
    (output / "source_manifest.json").write_text(
        json.dumps(
            {
                "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "evaluated_content_sha256": evaluated,
                "note": "Working candidate identified by content hashes; commit recorded after validation.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    source, selectors, selection_env = selection(scenario, output)
    name = "gatalogo-" + scenario.lower().replace("-", "") + "-" + uuid4().hex[:12]
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
        cached = subprocess.run(
            ["docker", "image", "inspect", image],
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if cached.returncode != 0:
            context = output / "build-context"
            context.mkdir()
            shutil.copy2(ROOT / "Dockerfile.postgres", context / "Dockerfile")
            with (output / "image-build.log").open("w", encoding="utf-8") as log:
                subprocess.run(
                    ["docker", "build", "-t", image, str(context)],
                    timeout=300,
                    cwd=ROOT,
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
    env = dict(os.environ, POSTGRES_PASSWORD=secrets.token_hex(24))
    created = False
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=512m",
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
            ],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        created = True
        for _ in range(90):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
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
            raise TimeoutError("Disposable database did not become ready")
        address = subprocess.check_output(
            ["docker", "port", name, "5432/tcp"], text=True
        ).strip()
        port = address.rsplit(":", 1)[1]
        url = f"postgresql+asyncpg://gatalogo:{env['POSTGRES_PASSWORD']}@127.0.0.1:{port}/recognition_test"
        env.update(
            DATABASE_URL=url,
            RECOGNITION_TEST_DATABASE_URL=url,
            JWT_SECRET=secrets.token_hex(32),
            GEMINI_API_KEY="",
            SMTP_ENABLED="false",
            FCM_SERVICE_ACCOUNT_FILE="",
            PHOTO_STORAGE_DIR=str(output / "storage"),
            ANALYSIS_CACHE_DIR=str(output / "cache"),
            PYTHONUTF8="1",
        )
        env.update(selection_env)
        print(scenario + ": disposable PostgreSQL ready", flush=True)
        with (output / "migrations.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                env=env,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        with (output / "pytest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-ra",
                    *selectors,
                    "--junitxml",
                    str(output / "junit.xml"),
                ],
                cwd=source,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=360,
            )
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "exit_code": result.returncode,
                    "database": "own random-port PostgreSQL, migrations applied; fixture schema per case",
                    "providers": "local RSA/JWKS; no live identity provider"
                    if scenario == "SEC-01"
                    else "isolated local Mailpit/SMTP; generated JWTs; no external recipients or inference"
                    if scenario == "SEC-07"
                    else "generated app JWTs; synthetic analysis/fault injection; no external delivery or inference",
                    "api_lifespan_started": False,
                    "physical_device": False,
                    "evaluated_sources_unchanged": evaluated == source_hashes(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps({"exit_code": result.returncode, "output": str(output)}),
            flush=True,
        )
        return result.returncode if evaluated == source_hashes() else 2
    finally:
        if created:
            subprocess.run(
                ["docker", "rm", "--force", "--volumes", name],
                check=True,
                stdout=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--scenario",
        choices=[
            "SEC-01",
            "SEC-02",
            "SEC-03",
            "SEC-05",
            "SEC-07",
            "DATA-02",
            "DATA-05",
            "DATA-06",
            "DATA-07",
        ],
        default="SEC-01",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.scenario))
