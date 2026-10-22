"""Bounded external executor for the next API battery and new defect variants."""

import json
import shutil
import difflib
import pytest
from critical_suite import BASE, RUN, ATTEMPT, EXECUTORS, execute, write, require_green

FILES = [
    "test_storage_service.py",
    "test_sync_query_performance.py",
    "test_conversation_events.py",
    "test_realtime_events.py",
    "test_quintal_push.py",
    "test_account_settings.py",
    "test_message_reactions.py",
    "test_quintal_production.py",
    "test_quintal_rewards.py",
    "test_quintal_routines.py",
    "test_preprocessing_quality.py",
]


def test_extended_baseline():
    selectors = [str(EXECUTORS / "extended_cases.py")]
    selectors += [str(BASE / "tests" / f) for f in FILES]
    # Explicit conftest fixtures; all delivery is stubbed, no real credentials.
    result = execute(BASE, "extended-baseline", selectors)
    require_green(result)


MUTANTS = [
    (
        "MUT-06",
        "app/main.py",
        "        await session.rollback()\n        service = AnalysisService(settings)",
        "        pass\n        service = AnalysisService(settings)",
        "test_DB05_primary_inference_releases_read_connection",
        "external",
    ),
    (
        "MUT-27",
        "app/services/chat_commands.py",
        "    await session.commit()\n    await publish_committed_events(session)\n    await session.refresh(message)",
        "    await publish_committed_events(session)\n    await session.commit()\n    await session.refresh(message)",
        "test_DATA02_events_wait_for_successful_commit",
        "external",
    ),
    (
        "MUT-04",
        "app/services/catalog_sync_queries.py",
        'CatPhoto.kind.in_(("front", "left", "right", "back")),',
        "True,",
        "test_DB02_IMG03_reference_filtering",
        "external",
    ),
    (
        "MUT-26",
        "app/services/fcm_service.py",
        "and str(device.id) not in delivered",
        "and True",
        "test_real_transport_retries_only_failed_device_and_keeps_bad_request_token",
        "test_quintal_push.py",
    ),
]


@pytest.mark.parametrize("mid,path,old,new,node,where", MUTANTS)
def test_extended_mutant(mid, path, old, new, node, where):
    baseline = json.loads(
        (ATTEMPT / "critical_runs/extended-baseline/summary.json").read_text(
            encoding="utf-8"
        )
    )
    if baseline["exit_code"] or not any(
        c["name"].startswith(node) and c["status"] == "PASS" for c in baseline["cases"]
    ):
        pytest.skip("BLOCKED: expected oracle not green in baseline")
    target = RUN / "sources" / (ATTEMPT.name + "-" + mid) / "api"
    shutil.copytree(
        BASE,
        target,
        ignore=shutil.ignore_patterns(".tmp", "__pycache__", ".pytest_cache"),
    )
    file = target / path
    original = file.read_text(encoding="utf-8")
    assert original.count(old) == 1
    modified = original.replace(old, new, 1)
    file.write_text(modified, encoding="utf-8")
    selected = (
        str(EXECUTORS / "extended_cases.py")
        if where == "external"
        else str(target / "tests" / where)
    )
    result = execute(target, mid, [selected + "::" + node])
    errors = any(c["status"] in ("ERROR", "SKIPPED") for c in result["cases"])
    killed = any(
        c["status"] == "FAIL" and c["name"].startswith(node) for c in result["cases"]
    )
    classification = (
        "INCONCLUSIVE_INFRASTRUCTURE"
        if errors or not result["cases"] or result["exit_code"] not in (0, 1)
        else "KILLED"
        if killed
        else "SURVIVED"
    )
    folder = ATTEMPT / "critical_runs" / mid
    (folder / "mutation.diff").write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(True),
                modified.splitlines(True),
                fromfile=path,
                tofile=path,
            )
        ),
        encoding="utf-8",
    )
    write(
        folder / "classification.json",
        {
            "mutant": mid,
            "classification": classification,
            "oracle": node,
            "source": path,
        },
    )
    assert classification == "KILLED"
