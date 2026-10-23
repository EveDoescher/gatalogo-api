"""Freeze approved versions and run a bounded pilot; never changes product sources.

All generated copies/evidence live under .tmp/quality-campaign/<unique run>.
The measured sources are Git archives; executors are copied separately and hashed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def freeze(flutter):
    flutter = Path(flutter).resolve()
    for root in (ROOT, flutter):
        if git(root, "branch", "--show-current") != "code-quality-cleanup":
            raise ValueError("Run from code-quality-cleanup in both projects")
    run = (
        ROOT
        / ".tmp"
        / "quality-campaign"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    run.mkdir(parents=True, exist_ok=False)
    manifest = {
        "campaign_id": run.name,
        "created_at": datetime.now(UTC).isoformat(),
        "state": "PREPARED",
        "python": sys.version,
        "host": {"os": platform.platform(), "cpu_count": os.cpu_count()},
        "sources": {},
        "local_work_preserved": {},
        "dependencies": sorted(
            (d.metadata["Name"], d.version)
            for d in importlib.metadata.distributions()
            if d.metadata.get("Name")
        ),
        "executors": {},
        "limitations": [
            "Shared installed dependencies: compare source effects, not historical deployment locks.",
            "No physical device, paid inference or external delivery authorized.",
        ],
    }
    for project, repo in (("api", ROOT), ("flutter", flutter)):
        manifest["local_work_preserved"][project] = git(repo, "status", "--porcelain")
        for arm, ref in (("A", "main"), ("B", "code-quality-cleanup")):
            sha = git(repo, "rev-parse", ref)
            target = run / "sources" / arm / project
            target.mkdir(parents=True)
            archive = run / f"{arm}-{project}.tar"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "archive",
                    "--format=tar",
                    "-o",
                    str(archive),
                    sha,
                ],
                check=True,
            )
            excluded = []
            with tarfile.open(archive) as source:
                members = []
                for member in source.getmembers():
                    name = Path(member.name).name.lower()
                    # Never copy real credentials, env files, links or live storage.
                    if (
                        member.issym()
                        or member.islnk()
                        or (name.startswith(".env") and name != ".env.example")
                        or "service-account" in name
                        or "firebase-adminsdk" in name
                        or name.endswith((".pem", ".key", ".p12", ".pfx"))
                        or member.name.startswith(("storage/", ".aws/"))
                    ):
                        excluded.append(member.name)
                        continue
                    members.append(member)
                source.extractall(target, members=members, filter="data")
            archive.unlink()
            manifest["sources"][f"{arm}-{project}"] = {
                "repo": str(repo),
                "ref": ref,
                "sha": sha,
                "path": str(target),
                "excluded_sensitive_paths": excluded,
                "history": git(repo, "log", "-3", "--format=%h %s", sha).splitlines(),
            }
    executor_root = run / "executors"
    executor_root.mkdir()
    for origin in [
        Path(__file__),
        *sorted((ROOT / "scripts" / "campaign").glob("*.py")),
    ]:
        target = executor_root / origin.name
        shutil.copy2(origin, target)
        manifest["executors"][origin.name] = digest(target)
    for name, repo in (("api", ROOT), ("flutter", flutter)):
        for filename in ("test_plan_efficiency.md", "test_results_template.md"):
            path = repo / "docs" / filename
            if path.exists():
                target = run / "approved_plans" / name / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    write_json(run / "manifest.json", manifest)
    print(run, flush=True)
    return run


def attempt_path(run, name):
    if not name.replace("-", "").isalnum():
        raise ValueError("Invalid attempt name")
    return Path(run).resolve() / "attempts" / name


def prepare(run, name):
    run = Path(run).resolve()
    if not run.is_relative_to(ROOT / ".tmp" / "quality-campaign"):
        raise ValueError("Not a campaign directory")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    attempt = attempt_path(run, name)
    if attempt.exists():
        raise ValueError("Executors already frozen; use a new campaign for changes")
    (attempt / "executors").mkdir(parents=True)
    origin = ROOT / "scripts" / "campaign"
    for path in sorted(origin.glob("*.py")):
        shutil.copy2(path, attempt / "executors" / path.name)
    shutil.copy2(Path(__file__), attempt / "executors" / "quality_campaign.py")
    shutil.copy2(
        ROOT / "scripts" / "run_integration_checks.py",
        attempt / "executors" / "integration_runner.py",
    )
    flutter_tools = Path(manifest["sources"]["B-flutter"]["repo"]) / "tool"
    for flutter_audit in sorted(flutter_tools.glob("campaign_*test.dart")):
        shutil.copy2(flutter_audit, attempt / "executors" / flutter_audit.name)
    spec = importlib.util.spec_from_file_location(
        "campaign_fixtures", origin / "fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not (run / "fixtures" / "manifest.json").exists():
        write_json(
            run / "fixtures" / "manifest.json", module.materialize(run / "fixtures")
        )
        write_json(run / "fixtures" / "social_sample.json", module.social_rows())
        write_json(
            run / "fixtures" / "notifications_sample.json", module.notification_rows()
        )
    write_json(
        attempt / "executor_manifest.json",
        {
            "source_base": manifest["sources"],
            "prepared_at": datetime.now(UTC).isoformat(),
            "hashes": {
                p.name: digest(p)
                for p in (attempt / "executors").iterdir()
                if p.is_file()
            },
        },
    )
    return run


API_SUITES = {
    "pilot": "pilot_cases.py",
    "critical": "critical_suite.py",
    "extended": "extended_suite.py",
    "targeted": "targeted_suite.py",
    "live": "live_suite.py",
    "full": "full_suite.py",
    "queue": "queue_suite.py",
    "privacy": "privacy_suite.py",
    "diagnostic": "diagnostic_suite.py",
    "network": "network_suite.py",
    "delivery": "delivery_suite.py",
    "crossed": "crossed_suite.py",
    "social-volume": "social_volume_suite.py",
    "image": "image_suite.py",
    "storage-resilience": "storage_resilience_suite.py",
    "mechanisms": "remaining_mechanism_suite.py",
    "pipeline": "pipeline_suite.py",
    "worker-race": "worker_race_suite.py",
    "query-plan": "query_plan_suite.py",
    "socket-fanout": "socket_fanout_suite.py",
    "wire-contract": "wire_contract_suite.py",
    "identity": "identity_suite.py",
    "identity-controls": "identity_controls_suite.py",
    "offline-flow": "offline_flow_suite.py",
    "upload-transport": "upload_transport_suite.py",
    "social-history": "social_history_suite.py",
}


def execute_api(run, name, mutations=True, suite="pilot"):
    if suite not in API_SUITES:
        raise ValueError("Unknown frozen API suite")
    api_arm = os.environ.get("QUALITY_API_ARM", "B") if suite == "crossed" else "B"
    if api_arm not in {"A", "B"}:
        raise ValueError("Invalid API arm")
    run = Path(run).resolve()
    attempt = attempt_path(run, name)
    if (attempt / "api_attempt.json").exists():
        raise ValueError("Never overwrite a previous attempt; freeze a new campaign")
    write_json(
        attempt / "api_attempt.json",
        {
            "started_at": datetime.now(UTC).isoformat(),
            "suite": suite,
            "api_arm": api_arm,
            "experiment_environment": {
                key: os.environ.get(key)
                for key in (
                    "PROGRESSION_REWARDS_ENABLED",
                    "PROGRESSION_ROLLOUT_PERCENT",
                    "QUALITY_LIVE_REWARDS_VARIANT",
                    "HABITAT_FREE_SHOP",
                    "HABITAT_INSTANT_BUILD",
                )
            },
            "baseline_filter": os.getenv("QUALITY_BASELINE_FILTER"),
            "mutant_filter": os.getenv("QUALITY_MUTANT_FILTER"),
        },
    )
    os.environ.update(
        QUALITY_CAMPAIGN_RUN=str(run),
        QUALITY_CAMPAIGN_ATTEMPT=str(attempt),
        FLUTTER_PROJECT_ROOT=str(run / "sources" / "B" / "flutter"),
        GEMINI_API_KEY="",
        FCM_SERVICE_ACCOUNT_FILE="",
        SMTP_ENABLED="false",
        PHOTO_STORAGE_DIR=str(run / "isolated_api_storage"),
        ANALYSIS_CACHE_DIR=str(run / "isolated_api_cache"),
    )
    executor = attempt / "executors" / "integration_runner.py"
    if not executor.exists():
        raise RuntimeError("Prepare this attempt before execution")
    spec = importlib.util.spec_from_file_location("isolated_integration", executor)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    # Reuse existing DB/image/migrations/readiness/cleanup rather than a second Docker harness.
    runner.ROOT = run / "sources" / api_arm / "api"
    selected = [str(attempt / "executors" / API_SUITES[suite])]
    if mutations and suite == "pilot":
        selected.append(str(attempt / "executors" / "mutation_cases.py"))
    code = runner.main([*selected, "--confcutdir", str(attempt / "executors")])
    write_json(
        attempt / "api_attempt_result.json",
        {"exit_code": code, "finished_at": datetime.now(UTC).isoformat()},
    )
    return code


def execute_flutter(run, name):
    run = Path(run).resolve()
    attempt = attempt_path(run, name)
    if (attempt / "flutter_attempt.json").exists():
        raise ValueError("Never overwrite a previous Flutter attempt")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    binary = shutil.which("flutter")
    if not binary:
        raise RuntimeError("Flutter unavailable")
    write_json(
        attempt / "flutter_attempt.json",
        {"started_at": datetime.now(UTC).isoformat(), "binary": binary},
    )
    results = []
    for label, arm in (("01-A", "A"), ("02-B", "B"), ("03-B", "B"), ("04-A", "A")):
        target = run / "sources" / arm / "flutter"
        config = target / ".dart_tool"
        config.mkdir(exist_ok=True)
        for filename in ("package_config.json", "package_graph.json"):
            origin = repo / ".dart_tool" / filename
            if origin.exists():
                shutil.copy2(origin, config / filename)
        test = target / "test" / "campaign_probe_test.dart"
        shutil.copy2(attempt / "executors" / "campaign_probe_test.dart", test)
        output = attempt / "flutter_results" / label
        output.mkdir(parents=True, exist_ok=False)
        env = dict(os.environ, QUALITY_FLUTTER_OUTPUT=str(output / "samples.json"))
        with (output / "test.jsonl").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [binary, "test", "--no-pub", "--reporter", "json", str(test)],
                cwd=target,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        results.append({"label": label, "exit_code": result.returncode})
        write_json(attempt / "flutter_attempt_result.json", results)
        print(f"Flutter {label}: exit={result.returncode}", flush=True)
    # Existing B account lifecycle tests remain separate from common-contract comparison.
    target = run / "sources" / "B" / "flutter"
    output = attempt / "flutter_results" / "RES-03"
    output.mkdir(exist_ok=False)
    with (output / "test.jsonl").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [
                binary,
                "test",
                "--no-pub",
                "--reporter",
                "json",
                "test/conversation_controller_test.dart",
                "test/notification_store_test.dart",
            ],
            cwd=target,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    results.append({"label": "RES-03-B-only", "exit_code": result.returncode})
    write_json(attempt / "flutter_attempt_result.json", results)
    return int(any(r["exit_code"] for r in results))


def execute_accessibility(run, name):
    run = Path(run).resolve()
    attempt = attempt_path(run, name)
    output = attempt / "accessibility"
    output.mkdir(exist_ok=False)
    source = run / "sources" / "B" / "flutter"
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    config = source / ".dart_tool"
    config.mkdir(exist_ok=True)
    for filename in ("package_config.json", "package_graph.json"):
        if (repo / ".dart_tool" / filename).exists():
            shutil.copy2(repo / ".dart_tool" / filename, config / filename)
    test = source / "test" / "campaign_accessibility_test.dart"
    shutil.copy2(attempt / "executors" / "campaign_accessibility_test.dart", test)
    env = dict(os.environ, QUALITY_ACC_OUTPUT=str(output / "observations"))
    binary = shutil.which("flutter")
    if not binary:
        raise RuntimeError("Flutter unavailable")
    with (output / "tests.jsonl").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [
                binary,
                "test",
                "--no-pub",
                "--reporter",
                "json",
                str(test),
                "test/habitat_service_test.dart",
            ],
            cwd=source,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    write_json(
        output / "result.json",
        {
            "exit_code": result.returncode,
            "source_sha": manifest["sources"]["B-flutter"]["sha"],
            "scope": "desktop accessibility and HabitatService unit tests; no device",
        },
    )
    return result.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flutter", default=str(ROOT.parent / "catlogue"))
    parser.add_argument("--run", type=Path)
    parser.add_argument("--attempt", default="pilot-01")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument(
        "--prepare", dest="operation", action="store_const", const="prepare"
    )
    for suite in API_SUITES:
        flag = "api" if suite == "pilot" else suite
        operations.add_argument(
            "--execute-" + flag, dest="operation", action="store_const", const=suite
        )
    for target in ("accessibility", "flutter"):
        operations.add_argument(
            "--execute-" + target, dest="operation", action="store_const", const=target
        )
    parser.add_argument("--no-mutations", action="store_true")
    args = parser.parse_args()
    if args.operation is None:
        freeze(args.flutter)
    elif not args.run:
        parser.error("Preparation/execution requires --run")
    elif args.operation == "prepare":
        prepare(args.run, args.attempt)
    elif args.operation in API_SUITES:
        raise SystemExit(
            execute_api(args.run, args.attempt, not args.no_mutations, args.operation)
        )
    elif args.operation == "accessibility":
        raise SystemExit(execute_accessibility(args.run, args.attempt))
    else:
        raise SystemExit(execute_flutter(args.run, args.attempt))
