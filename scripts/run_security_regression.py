"""Run bounded security/integrity cases on disposable PostgreSQL and frozen sources."""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from uuid import uuid4

from campaign.subprocesses import run_logged

ROOT = Path(__file__).resolve().parents[1]
LONG_SCENARIOS = {"LOAD-01", "LOAD-02", "LOAD-06", "LOAD-PILOT", "BR-02-TCP"}
MUTATIONS = {
    "MUT-13": (
        "DLV-02",
        "app/services/auth_service.py",
        '    await session.commit()\n    await _send_otp(\n        email=delivery.email,\n        code=delivery.code,\n        purpose=delivery.purpose,\n        settings=delivery.settings,\n    )',
        '    await _send_otp(\n        email=delivery.email,\n        code=delivery.code,\n        purpose=delivery.purpose,\n        settings=delivery.settings,\n    )\n    await session.commit()',
    ),
    "MUT-14": (
        "RES-05",
        "app/services/photo_cleanup.py",
        "retained = await _referenced(check, key, cat)",
        "retained = False",
    ),
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
        return run_logged(
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
            log=log,
            timeout=4600 if env.get("QUALITY_LONG_SCENARIO") == "LOAD-06" else 1500 if env.get("QUALITY_LONG_SCENARIO") in ("LOAD-01", "LOAD-02") else (1200 if env.get("QUALITY_SYNC_CONFIRMATION") == "true" or env.get("QUALITY_LOCAL_MODEL_RESOURCE") == "true" or env.get("QUALITY_REAL_REFERENCE_BATCH") == "true" else 480 if env.get("QUALITY_LARGE_LOCAL_SYNC") == "true" else 360),
        )


def junit_cases(path):
    return {
        (c.get("classname"), c.get("name")): c
        for c in ET.parse(path).getroot().iter("testcase")
    }


def frozen_execution_hashes(source, output):
    """Hash the actual immutable copies used by detached long experiments."""
    files = [*source.glob("app/**/*.py"), *source.glob("alembic/**/*.py"), source / "alembic.ini"]
    files.extend((output / "executors").glob("*.py"))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files) if p.is_file()}


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
            "test_otp_delivery.py",
            "test_account_settings.py",
            "test_segmentation_service.py",
            "test_storage_service.py",
            "test_recognition.py",
            "test_dense_features.py",
            "test_conversation_events.py",
            "test_websocket_manager.py",
            "test_sync_query_performance.py",
            "test_logging_security.py",
            "test_reference_analysis.py",
            "test_analysis_cache_resilience.py",
        )
    )
    paths.extend(ROOT.glob("scripts/campaign/*.py"))
    paths.extend(ROOT.glob("alembic/**/*.py"))
    paths.extend(ROOT.glob("tests/fixtures/local_models/*"))
    paths.append(ROOT / "alembic.ini")
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
                "tests/test_otp_delivery.py",
                "tests/test_account_settings.py",
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
    requested_ref = os.environ.get("QUALITY_API_SOURCE_REF", "HEAD")
    if requested_ref != "HEAD" and not re.fullmatch(r"[0-9a-f]{40}", requested_ref):
        raise ValueError("A frozen API source ref must be an explicit full commit SHA")
    sha = subprocess.check_output(
        ["git", "rev-parse", "--verify", requested_ref + "^{commit}"], cwd=ROOT, text=True
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
        "SEC-09": "remaining_mechanism_cases.py",
        "DATA-02": "rollback_cases.py",
        "DATA-05": None,
        "DATA-06": "fencing_cases.py",
        "DATA-07": "production_boundary_cases.py",
        "DATA-08": "social_order_cases.py",
        "DATA-03": "idempotency_cases.py",
        "DATA-04": "idempotency_cases.py",
        "DB-02": "history_cases.py",
        "DLV-01": "delivery_cases.py",
        "DLV-02": "delivery_cases.py",
        "DLV-04": "fcm_delivery_cases.py",
        "LOAD-03": "remaining_mechanism_cases.py",
        "LOAD-04-REAL": "real_reference_cases.py",
        "BR-02-TCP": "paired_tcp_cases.py",
        "LOAD-01": "long_load_cases.py",
        "LOAD-02": "long_load_cases.py",
        "LOAD-06": "long_load_cases.py",
        "LOAD-PILOT": "long_load_cases.py",
        "RES-04": "worker_race_cases.py",
        "RES-08": "test_analysis_cache_resilience.py",
        "IMG-07": "storage_worker_cases.py",
        "IMG-05": "image_rejection_cases.py",
        "DATA-01": "data_snapshot_cases.py",
        "DATA-09": "report_lifecycle_cases.py",
        "DB-06": "concurrent_catalog_cases.py",
        "DB-03": "social_volume_cases.py",
        "DB-05": "pool_release_cases.py",
        "DB-03-HISTORY": "social_history_cases.py",
        "DB-04": "migrated_plan_cases.py",
        "RES-01": "current_offline_cases.py",
        "LAT-04": "sync_batch_cases.py",
        "LAT-03": "analysis_error_cases.py",
        "LAT-08": "latency_pressure_cases.py",
        "RES-02": "current_upload_transport_cases.py",
        "RES-05": "service_recovery_cases.py",
        "LOAD-05": "current_socket_fanout_cases.py",
        "RSC-02": "resource_lifecycle_cases.py",
        "RSC-06": "resource_caps_cases.py",
        "RSC-01": "local_model_resource_cases.py",
        "RSC-01-BOOT": "local_model_resource_cases.py",
        "RSC-02-DIAG": "resource_lifecycle_cases.py",
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
    if scenario == "DATA-01":
        names.extend(["image_cases.py", "pipeline_cases.py"])
    if scenario == "DATA-09":
        names.extend(["remaining_mechanism_cases.py", "measurements.py"])
    if scenario == "DB-06":
        names.extend(["remaining_mechanism_cases.py", "production_boundary_cases.py", "measurements.py"])
    if scenario == "DB-05":
        names.extend(["image_cases.py", "pipeline_cases.py", "test_reference_analysis.py"])
    if scenario == "RES-01":
        names.extend(["image_cases.py", "critical_report.py", "subprocesses.py"])
    if scenario == "LAT-04":
        names.extend(["current_offline_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py"])
    if scenario == "LAT-03":
        names.extend(["current_offline_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py"])
    if scenario in LONG_SCENARIOS:
        names.extend(["current_offline_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py", "measurements.py", "long_load_child.py"])
        shutil.copytree(ROOT / "alembic", source / "alembic", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "alembic.ini", source / "alembic.ini")
    if scenario == "LAT-08":
        names.extend(["current_offline_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py", "measurements.py", "latency_probe_child.py"])
    if scenario == "RES-02":
        names.extend(["current_offline_cases.py", "upload_transport_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py"])
    if scenario == "RES-05":
        names.extend([
            "storage_resilience_cases.py", "image_cases.py", "pipeline_cases.py",
            "delivery_cases.py", "fcm_delivery_cases.py", "current_upload_transport_cases.py",
            "current_offline_cases.py", "upload_transport_cases.py", "critical_report.py",
            "subprocesses.py", "test_quintal_live_api.py",
        ])
    if scenario == "LOAD-05":
        names.extend(["current_offline_cases.py", "current_upload_transport_cases.py", "upload_transport_cases.py", "socket_fanout_cases.py", "network_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py", "measurements.py"])
    if scenario in ("RSC-02", "RSC-02-DIAG"):
        names.extend(["current_offline_cases.py", "current_upload_transport_cases.py", "upload_transport_cases.py", "network_cases.py", "image_cases.py", "critical_report.py", "subprocesses.py", "measurements.py"])
    if scenario == "RSC-06":
        names.extend([
            "test_reference_analysis.py", "current_offline_cases.py", "image_cases.py",
            "critical_report.py", "subprocesses.py", "measurements.py",
            "latency_pressure_cases.py", "latency_probe_child.py", "windows_limits.py",
        ])
    if scenario == "LOAD-04-REAL":
        names.extend(["current_offline_cases.py", "image_cases.py", "critical_report.py",
            "subprocesses.py", "measurements.py", "paid_provider.py"])
    if scenario == "BR-02-TCP":
        names.extend(["current_offline_cases.py", "image_cases.py", "critical_report.py",
            "subprocesses.py", "measurements.py", "latency_pressure_cases.py", "latency_probe_child.py"])
    if scenario in ("RSC-01", "RSC-01-BOOT"):
        names.extend([
            "local_model_probe_cases.py", "current_offline_cases.py", "image_cases.py",
            "critical_report.py", "subprocesses.py", "measurements.py", "windows_limits.py", "local_model_controls.py",
        ])
    if scenario == "IMG-05":
        names.extend([
            "image_cases.py", "pipeline_cases.py", "input_bounds_cases.py",
            "local_model_controls.py", "measurements.py", "windows_limits.py",
        ])
    if scenario == "SEC-04":
        names.extend(["remaining_mechanism_cases.py", "social_volume_cases.py", "measurements.py"])
    if scenario in ("LOAD-03", "SEC-09"):
        names.append("measurements.py")
    if scenario == "DB-03":
        names.append("measurements.py")
    if scenario == "DB-03-HISTORY":
        names.extend(["social_volume_cases.py", "measurements.py"])
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
    if scenario in ("RSC-01", "RSC-01-BOOT", "IMG-05", "LOAD-04-REAL"):
        for name in ("chelsea.png", "README.md"):
            destination = executors / name
            shutil.copy2(ROOT / "tests/fixtures/local_models" / name, destination)
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
    if scenario == "DATA-01":
        selected.append("pipeline_cases.py")
    if scenario == "DATA-09":
        selected.append("remaining_mechanism_cases.py")
    selectors = [str(executors / name) for name in selected]
    expression = "DB03" if scenario == "DB-03-HISTORY" else scenario.replace("-", "")
    selectors += ["-k", expression, "--confcutdir", str(executors)]
    if scenario == "RSC-02-DIAG":
        selectors = [str(executors / "resource_lifecycle_cases.py") + "::test_RSC02_diagnostic_denied_multipart_keepalive", "--confcutdir", str(executors)]
    if scenario == "RSC-06":
        selectors = [str(executors / case_file), "-k", "postgres", "--confcutdir", str(executors)]
    if scenario == "RSC-01-BOOT":
        selectors = [str(executors / case_file) + "::test_RSC01_actual_lifespan_boot_and_shutdown", "--confcutdir", str(executors)]
    if scenario in LONG_SCENARIOS:
        selectors = [str(executors / case_file), "--confcutdir", str(executors)]
    if scenario == "LOAD-04-REAL":
        selectors = [str(executors / case_file), "--confcutdir", str(executors)]
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
            QUALITY_LARGE_LOCAL_SYNC="true" if scenario == "LAT-04" else "false",
            QUALITY_API_KEEPALIVE_SECONDS="120" if scenario == "BR-02-TCP" else "5" if scenario.startswith("RSC-02") else "1",
            QUALITY_LATENCY_HTTP_TIMEOUT_SECONDS="30" if scenario == "BR-02-TCP" else "10",
            QUALITY_LATENCY_CLIENT_KEEPALIVE_SECONDS="15" if scenario == "BR-02-TCP" else "5",
            QUALITY_LATENCY_EXPECT_RELEASED_POOL="false" if scenario == "BR-02-TCP" else "true",
            QUALITY_CAMPAIGN_RUN=str(campaign),
            QUALITY_CAMPAIGN_ATTEMPT=str(output),
            QUALITY_TARGET_SOURCE=str(source),
            QUALITY_CASE_EVIDENCE=str(output / "observations"),
            QUALITY_FLUTTER_REPO=str(ROOT.parent / "catlogue"),
            QUALITY_LOCAL_MODEL_RESOURCE="true" if scenario == "RSC-01" else "false",
            QUALITY_REAL_REFERENCE_BATCH="true" if scenario == "LOAD-04-REAL" else "false",
            QUALITY_MODEL_PHOTO=str(executors / "chelsea.png"),
            QUALITY_MODEL_CACHE=str(ROOT / ".model-cache"),
            QUALITY_LONG_SCENARIO=scenario if scenario in LONG_SCENARIOS else "",
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
    frozen_before = None
    if scenario in LONG_SCENARIOS:
        shutil.copy2(Path(__file__), output / "executors/run_security_regression.snapshot.py")
        frozen_before = frozen_execution_hashes(source, output)
        (output / "frozen_execution_manifest.json").write_text(json.dumps(frozen_before, indent=2)+"\n", encoding="utf-8")
    # Recheck a green baseline for each mutant, focusing on its domain oracles.
    mutation_oracles = {
        "MUT-13": [
            "extended_cases.py::test_DLV02_commit_failure_prevents_smtp_acceptance",
            "delivery_cases.py::test_DLV02_SMTP_ambiguous_acceptance_retry_and_cooldown",
        ],
        "MUT-14": [
            "service_recovery_cases.py::test_RES05_cleanup_never_deletes_referenced_file",
            "service_recovery_cases.py::test_RES05_upload_commit_failure_preserves_old_and_recovers_new_orphan",
            "service_recovery_cases.py::test_RES05_cleanup_defers_while_writer_holds_cat_lock",
        ],
    }
    if mutation in mutation_oracles:
        executors = output / "executors"
        selectors = [str(executors / oracle) for oracle in mutation_oracles[mutation]]
        selectors.extend(["--confcutdir", str(executors)])
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
                cwd=source if scenario in LONG_SCENARIOS else ROOT,
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
                    else "real cached detector/SAM and authorized bounded Google Gemini; public CC0 fixture; no external notifications"
                    if scenario == "LOAD-04-REAL"
                    else "generated app JWTs; synthetic analysis/fault injection; no external delivery or inference",
                    "api_lifespan_started": False,
                    "physical_device": False,
                    "evaluated_sources_unchanged": frozen_before == frozen_execution_hashes(source, output) if frozen_before is not None else evaluated == source_hashes(),
                    "workspace_sources_unchanged": evaluated == source_hashes(),
                    "isolation": "Frozen app, executors and migrations; original-workspace edits cannot affect this long run" if frozen_before is not None else "Original strict workspace guard",
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
        if (frozen_before is not None and frozen_before != frozen_execution_hashes(source, output)) or (frozen_before is None and evaluated != source_hashes()):
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
            "SEC-09",
            "DATA-02",
            "DATA-05",
            "DATA-06",
            "DATA-07",
            "DATA-08",
            "DATA-03",
            "DATA-04",
            "DB-02",
            "DLV-01",
            "DLV-02",
            "DLV-04",
            "LOAD-03",
            "LOAD-04-REAL",
            "BR-02-TCP",
            "LOAD-01",
            "LOAD-02",
            "LOAD-06",
            "LOAD-PILOT",
            "RES-04",
            "RES-08",
            "IMG-07",
            "IMG-05",
            "DATA-01",
            "DATA-09",
            "DB-06",
            "DB-03",
            "DB-05",
            "DB-03-HISTORY",
            "DB-04",
            "RES-01",
            "LAT-04",
            "LAT-03",
            "LAT-08",
            "RES-02",
            "RES-05",
            "LOAD-05",
            "RSC-02",
            "RSC-06",
            "RSC-01",
            "RSC-01-BOOT",
            "RSC-02-DIAG",
        ],
        default="SEC-01",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.scenario, args.mutation))
