"""Baseline and directed mutations for the second, bounded critical battery."""

import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
ATTEMPT = Path(os.environ["QUALITY_CAMPAIGN_ATTEMPT"])
EXECUTORS = ATTEMPT / "executors"
BASE = RUN / "sources" / "B" / "api"

WORKER_CASES = [
    "test_progression_failure_rolls_back_photo_and_staged_events",
    "test_expired_claim_is_recovered_and_late_result_cannot_overwrite",
    "test_replacement_before_processing_never_invokes_analysis",
    "test_cancellation_releases_persisted_claim_and_next_process_resumes",
    "test_postgres_claim_skips_busy_job_and_rereads_replaced_photo",
    "test_failures_retry_then_mark_only_current_photo_failed",
    "test_terminal_progression_failure_marks_photo_after_savepoint_rollback",
    "test_expired_final_attempt_fails_without_another_inference",
]


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def execute(source, name, selectors):
    folder = ATTEMPT / "critical_runs" / name
    folder.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, QUALITY_TARGET_SOURCE=str(source), QUALITY_CASE_EVIDENCE=str(folder / "observations"),
               GEMINI_API_KEY="", SMTP_ENABLED="false", FCM_SERVICE_ACCOUNT_FILE="",
               PHOTO_STORAGE_DIR=str(folder / "storage"), ANALYSIS_CACHE_DIR=str(folder / "cache"))
    started = time.perf_counter()
    xml = folder / "junit.xml"
    with (folder / "pytest.log").open("w", encoding="utf-8") as output:
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-ra", *selectors,
                                 "--confcutdir", str(EXECUTORS), "--junitxml", str(xml)],
                                cwd=source, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=360)
    cases = []
    if xml.exists():
        for case in ET.parse(xml).getroot().iter("testcase"):
            cases.append({"name": case.attrib["name"], "seconds": float(case.attrib.get("time", 0)),
                          "status": "ERROR" if case.find("error") is not None else
                          "FAIL" if case.find("failure") is not None else
                          "SKIPPED" if case.find("skipped") is not None else "PASS"})
    summary = {"run": name, "exit_code": result.returncode, "elapsed_seconds": time.perf_counter()-started,
               "cases": cases, "selectors": [str(s).replace(str(source), "<target>").replace(str(EXECUTORS), "<executors>") for s in selectors]}
    write(folder / "summary.json", summary)
    return summary


def test_critical_baseline():
    selected = [str(EXECUTORS / "security_cases.py")]
    selected.extend(str(BASE / "tests" / "test_reference_analysis.py") + f"::{name}[postgres]" for name in WORKER_CASES)
    selected.append(str(BASE / "tests" / "test_quintal_postgres.py") + "::test_concurrent_collection_and_cross_account_access")
    if os.environ.get("QUALITY_BASELINE_FILTER") == "DB-02":
        selected = [str(EXECUTORS / "security_cases.py") + "::test_DB02_history_pagination_and_latest_revision"]
    summary = execute(BASE, "baseline", selected)
    assert summary["exit_code"] == 0 and all(c["status"] == "PASS" for c in summary["cases"]), "Inspect private baseline evidence"


# Each entry has one executable defect and a behavioral oracle. Never run on working code.
MUTANTS = [
    ("MUT-01", "app/routers/sync.py", ".where(SyncChange.user_id == user.id, SyncChange.revision > cursor)",
     ".where(SyncChange.revision > cursor)", "test_SEC02_pull_filters_both_owners", None),
    ("MUT-02", "app/routers/sync.py", "if existing.deleted_at is not None and operation.deleted_at is None:",
     "if False and existing.deleted_at is not None and operation.deleted_at is None:", "test_DATA05_conflict_and_tombstone", None),
    ("MUT-03", "app/routers/sync.py", "return operation.device_id > existing.updated_by_device",
     "return operation.device_id < existing.updated_by_device", "test_DATA05_conflict_and_tombstone", None),
    ("MUT-05", "app/routers/sync.py", "next_cursor = changes[-1].revision if changes else cursor",
     "next_cursor = max((r.revision for r in records), default=cursor)", "test_DB02_history_pagination_and_latest_revision", None),
    ("MUT-07", "app/services/reference_analysis.py", 'or job.attempts != claim.attempt',
     "or False", "test_DATA06_old_finish_while_new_attempt_is_running", None),
    ("MUT-08", "app/services/reference_analysis.py", "and photo.content_hash == expected_hash",
     "and True", "test_DATA06_delete_or_replace_between_claim_and_finish", None),
    ("MUT-09", "app/services/reference_analysis.py", "and cat.deleted_at is None",
     "and True", "test_DATA06_delete_or_replace_between_claim_and_finish", None),
    ("MUT-10-savepoint", "app/services/reference_analysis.py", "async with session.begin_nested():",
     "if True:", "test_progression_failure_rolls_back_photo_and_staged_events", "worker"),
    ("MUT-10-events", "app/services/reference_analysis.py", "                    discard_pending_events(session)",
     "                    pass  # deliberate mutant: retain events", "test_progression_failure_rolls_back_photo_and_staged_events", "worker"),
    ("MUT-11", "app/services/quintal_production.py", "    if old:",
     "    if False and old:", "test_DATA03_DATA04_concurrent_intent_and_cross_account", None),
    ("MUT-12", "app/services/quintal_production.py", 'if old.details.get("request") != fingerprint:',
     'if False and old.details.get("request") != fingerprint:', "test_DATA03_DATA04_concurrent_intent_and_cross_account", None),
    ("MUT-13", "app/services/quintal_production.py", "            HabitatTransaction.user_id == user_id,",
     "            True,", "test_DATA03_DATA04_concurrent_intent_and_cross_account", None),
    ("MUT-14", "app/services/quintal_simulation.py", 'if task.status == "active" and utc(task.ready_at) <= now:',
     'if task.status == "active":', "test_DATA07_collect_boundary_and_replay", None),
    ("MUT-15", "app/services/auth_service.py", '        await session.commit()\n        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")',
     '        await session.flush()\n        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")',
     "test_SEC07_failed_attempts_survive_request_rollback", None),
    ("MUT-16", "app/services/auth_service.py", "or active_session.revoked_at is not None",
     "or False", "test_SEC07_refresh_and_logout_revoke_previous_credentials", None),
    ("MUT-17", "app/services/conversations.py", "    if member is None:",
     "    if False and member is None:", "test_SEC03_stranger_friend_cannot_emit_or_mark_read", None),
    ("MUT-18", "app/services/storage_service.py", "if root != resolved and root not in resolved.parents:",
     "if False and root != resolved and root not in resolved.parents:", "test_SEC05_storage_escape_never_reads_or_deletes", None),
]


def pytest_generate_tests(metafunc):
    if "mid" in metafunc.fixturenames:
        selected = os.environ.get("QUALITY_MUTANT_FILTER", "")
        allowed = set(selected.split(",")) if selected else {m[0] for m in MUTANTS}
        assert allowed <= {m[0] for m in MUTANTS}, "Unknown mutant filter"
        metafunc.parametrize("mid,path,old,new,node,kind", [m for m in MUTANTS if m[0] in allowed])


def test_critical_mutation(mid, path, old, new, node, kind):
    baseline = json.loads((ATTEMPT / "critical_runs" / "baseline" / "summary.json").read_text(encoding="utf-8"))
    if baseline["exit_code"] or any(c["status"] != "PASS" for c in baseline["cases"]):
        pytest.skip("BLOCKED: no clean baseline; never classify as killed")
    if not any(c["name"].startswith(node) for c in baseline["cases"]):
        pytest.skip("BLOCKED: selected behavioral oracle did not run in baseline")
    target = RUN / "sources" / f"{ATTEMPT.name}-{mid}" / "api"
    shutil.copytree(BASE, target, ignore=shutil.ignore_patterns(".tmp", "__pycache__", ".pytest_cache"))
    file = target / path
    original = file.read_text(encoding="utf-8")
    assert original.count(old) == 1, "Mutation recipe must match exactly once"
    modified = original.replace(old, new, 1)
    file.write_text(modified, encoding="utf-8")
    if kind == "worker":
        selectors = [str(target / "tests" / "test_reference_analysis.py") + f"::{node}[postgres]"]
    else:
        selectors = [str(EXECUTORS / "security_cases.py") + "::" + node]
    summary = execute(target, mid, selectors)
    errors = [c for c in summary["cases"] if c["status"] in ("ERROR", "SKIPPED")]
    failed = [c for c in summary["cases"] if c["status"] == "FAIL" and c["name"].startswith(node)]
    classification = "INCONCLUSIVE_INFRASTRUCTURE" if errors or not summary["cases"] or summary["exit_code"] not in (0, 1) else (
        "KILLED" if failed else "SURVIVED")
    folder = ATTEMPT / "critical_runs" / mid
    (folder / "mutation.diff").write_text("".join(difflib.unified_diff(
        original.splitlines(True), modified.splitlines(True), fromfile=path, tofile=path)), encoding="utf-8")
    write(folder / "classification.json", {"mutant": mid, "family": "-".join(mid.split("-")[:2]),
          "classification": classification, "elapsed_seconds": summary["elapsed_seconds"],
          "expected_node": node, "failed_cases": failed,
          "baseline": "critical_runs/baseline/summary.json", "source": path,
          "note": "Failure during call of expected oracle; setup/import/compile errors do not count"})
    assert classification == "KILLED", classification
