"""Run bounded security/integrity cases on disposable PostgreSQL and frozen sources."""

import argparse
import difflib
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
import xml.etree.ElementTree as ET
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "MUT-03": (
        "DATA-05",
        "app/routers/sync.py",
        "return operation.device_id > existing.updated_by_device",
        "return operation.device_id >= existing.updated_by_device",
    ),
    "MUT-07": (
        "DATA-06",
        "app/services/reference_analysis.py",
        "job.attempts != claim.attempt",
        "False",
    ),
    "MUT-12": (
        "DATA-04",
        "app/services/quintal_production.py",
        'if old.details.get("request") != fingerprint:',
        "if False:",
    ),
}


def execute_pytest(source, selectors, env, output, prefix=""):
    with (output / (prefix + "pytest.log")).open("w", encoding="utf-8") as log:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-ra",
                *selectors,
                "--junitxml",
                str(output / (prefix + "junit.xml")),
            ],
            cwd=source,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=360,
        )


def junit_cases(path):
    return {
        (c.get("classname"), c.get("name")): c
        for c in ET.parse(path).getroot().iter("testcase")
    }


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
            "test_sync_query_performance.py",
            "test_logging_security.py",
            "test_reference_analysis.py",
            "test_analysis_cache_resilience.py",
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
        "SEC-04": "audience_cases.py",
        "SEC-05": "storage_cases.py",
        "SEC-06": "input_bounds_cases.py",
        "SEC-07": "auth_flow_cases.py",
        "SEC-10": "revocation_cases.py",
        "SEC-08": "privacy_cases.py",
        "DATA-02": "rollback_cases.py",
        "DATA-05": None,
        "DATA-06": "fencing_cases.py",
        "DATA-07": "production_boundary_cases.py",
        "DATA-08": "social_order_cases.py",
        "DATA-03": "idempotency_cases.py",
        "DATA-04": "idempotency_cases.py",
        "DB-02": "history_cases.py",
        "DLV-01": "delivery_cases.py",
        "DLV-04": "fcm_delivery_cases.py",
        "LOAD-03": "remaining_mechanism_cases.py",
        "RES-04": "worker_race_cases.py",
        "RES-08": "test_analysis_cache_resilience.py",
        "IMG-07": "storage_worker_cases.py",
    }[scenario]
    if case_file:
        names.append(case_file)
    if scenario in ("SEC-03", "SEC-10", "SEC-08"):
        names.append("test_auth_security.py")
    if scenario == "SEC-07":
        names.append("delivery_cases.py")
    if scenario == "SEC-08":
        names.extend(
            ["delivery_cases.py", "image_cases.py", "test_logging_security.py"]
        )
    if scenario in ("SEC-06", "RES-08"):
        names.append("image_cases.py")
    if scenario == "RES-08":
        names.append("storage_resilience_cases.py")
    if scenario == "IMG-07":
        names.extend(["storage_resilience_cases.py", "worker_race_cases.py", "test_reference_analysis.py", "subprocesses.py"])
    if scenario == "SEC-04":
        names.extend(["remaining_mechanism_cases.py", "social_volume_cases.py", "measurements.py"])
    if scenario == "LOAD-03":
        names.append("measurements.py")
    if scenario == "RES-04":
        names.extend(["test_reference_analysis.py", "worker_child.py", "subprocesses.py"])
    if scenario in ("DATA-03", "DATA-04"):
        names.append("idempotency_child.py")
    if scenario == "DB-02":
        names.append("test_sync_query_performance.py")
    for name in names:
        destination = executors / name
        origin = (
            ROOT / ("tests" if name.startswith("test_") else "scripts/campaign") / name
        )
        shutil.copy2(origin, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output / "candidate_manifest.json").write_text(
        json.dumps({"source_sha": sha, "executor_sha256": hashes}, indent=2) + "\n",
        encoding="utf-8",
    )
    selected = (
        ["security_cases.py"]
        if scenario == "SEC-10"
        else ["security_cases.py", "extended_cases.py"]
    )
    if scenario == "DB-02":
        selected = []
    if case_file:
        selected.append(case_file)
    if scenario == "SEC-07":
        selected.append("delivery_cases.py")
    if scenario == "SEC-08":
        selected.extend(
            ["delivery_cases.py", "image_cases.py", "test_logging_security.py"]
        )
    if scenario in ("SEC-06", "RES-08"):
        selected.append("image_cases.py")
    if scenario == "SEC-04":
        selected.extend(["remaining_mechanism_cases.py", "social_volume_cases.py"])
    selectors = [str(executors / name) for name in selected]
    selectors += ["-k", scenario.replace("-", ""), "--confcutdir", str(executors)]
    if scenario == "RES-04":
        selectors = [str(executors / "worker_race_cases.py") + "::test_RES04_actual_worker_process_killed_after_claim_then_restarted[postgres]"]
        selectors.extend(str(executors / "test_reference_analysis.py") + "::" + name + "[postgres]" for name in (
            "test_expired_claim_is_recovered_and_late_result_cannot_overwrite",
            "test_cancellation_releases_persisted_claim_and_next_process_resumes",
            "test_failures_retry_then_mark_only_current_photo_failed",
            "test_expired_final_attempt_fails_without_another_inference",
            "test_inference_releases_database_connection",
        ))
        selectors.extend(["--confcutdir", str(executors)])
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


def run(output, scenario="SEC-01", mutation=None):
    if mutation and MUTATIONS[mutation][0] != scenario:
        raise ValueError("Mutation must use its declared scenario oracle")
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
        baseline = None
        recipe = None
        if mutation:
            baseline_result = execute_pytest(
                source, selectors, env, output, "baseline-"
            )
            baseline = junit_cases(output / "baseline-junit.xml")
            if (
                baseline_result.returncode
                or not baseline
                or any(list(c) for c in baseline.values())
            ):
                (output / "mutation.json").write_text(
                    json.dumps(
                        dict(mutation=mutation, classification="INCONCLUSIVE_BASELINE"),
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 1
            _, relative, old, new = MUTATIONS[mutation]
            target = source / relative
            original = target.read_text(encoding="utf-8")
            if original.count(old) != 1:
                raise ValueError("Mutation recipe must match exactly once")
            changed = original.replace(old, new, 1)
            target.write_text(changed, encoding="utf-8")
            (output / "mutation.diff").write_text(
                "".join(
                    difflib.unified_diff(
                        original.splitlines(True),
                        changed.splitlines(True),
                        fromfile=relative,
                        tofile=relative,
                    )
                ),
                encoding="utf-8",
            )
            recipe = dict(
                path=relative,
                before_sha256=hashlib.sha256(original.encode()).hexdigest(),
                after_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            )
            env["QUALITY_CASE_EVIDENCE"] = str(output / "mutant-observations")
        result = execute_pytest(source, selectors, env, output)
        classification = None
        if mutation:
            cases = junit_cases(output / "junit.xml")
            killed = [
                name
                for name, c in cases.items()
                if name in baseline
                and c.find("failure") is not None
                and any(
                    marker in (c.find("failure").text or "")
                    for marker in (
                        "AssertionError",
                        "DID NOT RAISE",
                        "Regex pattern did not match",
                    )
                )
            ]
            infrastructure = (
                set(cases) != set(baseline)
                or result.returncode not in (0, 1)
                or any(
                    c.find("error") is not None or c.find("skipped") is not None
                    for c in cases.values()
                )
            )
            classification = (
                "INCONCLUSIVE_INFRASTRUCTURE"
                if infrastructure
                else "KILLED"
                if killed
                else "SURVIVED"
                if result.returncode == 0
                else "INCONCLUSIVE_ORACLE"
            )
            assert (
                recipe["after_sha256"]
                == hashlib.sha256((source / recipe["path"]).read_bytes()).hexdigest()
            )
            (output / "mutation.json").write_text(
                json.dumps(
                    dict(
                        mutation=mutation,
                        classification=classification,
                        recipe=recipe,
                        failed_baseline_selectors=killed,
                        baseline_passed=len(baseline),
                        isolated_candidate=str(source.relative_to(source.parents[2])),
                        product_checkout_unchanged=evaluated == source_hashes(),
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "mutation": mutation,
                    "classification": classification,
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
        if evaluated != source_hashes():
            return 2
        return (
            (0 if classification == "KILLED" else 1) if mutation else result.returncode
        )
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
    parser.add_argument("--mutation", choices=sorted(MUTATIONS))
    parser.add_argument(
        "--scenario",
        choices=[
            "SEC-01",
            "SEC-02",
            "SEC-03",
            "SEC-04",
            "SEC-05",
            "SEC-06",
            "SEC-07",
            "SEC-10",
            "SEC-08",
            "DATA-02",
            "DATA-05",
            "DATA-06",
            "DATA-07",
            "DATA-08",
            "DATA-03",
            "DATA-04",
            "DB-02",
            "DLV-01",
            "DLV-04",
            "LOAD-03",
            "RES-04",
            "RES-08",
            "IMG-07",
        ],
        default="SEC-01",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.scenario, args.mutation))
