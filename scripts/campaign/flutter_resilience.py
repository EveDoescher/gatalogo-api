"""Isolated Dart mutations and bounded desktop resilience regression battery."""

import argparse
import difflib
import json
import os
from pathlib import Path
import shutil
from subprocesses import run_logged
import time

FILES = [
    "campaign_resilience_test.dart",
    "campaign_social_resilience_test.dart",
    "catalog_read_performance_test.dart",
    "local_database_test.dart",
    "session_store_test.dart",
    "authenticated_api_client_test.dart",
    "notification_store_test.dart",
    "conversation_controller_test.dart",
    "habitat_service_test.dart",
    "cat_preflight_service_test.dart",
    "cat_sharing_service_test.dart",
    "gamification_service_test.dart",
    "product_flows_test.dart",
    "quintal_production_service_test.dart",
]
MUTANTS = [
    (
        "MUT-19",
        "lib/services/catalog_sync_coordinator.dart",
        "return owner == session.session?.userId ? snapshot : null;",
        "return snapshot;",
        ["campaign_resilience_test.dart"],
    ),
    (
        "MUT-20",
        "lib/data/sqlite_id_batches.dart",
        "offset += size",
        "offset += size + 1",
        ["campaign_resilience_test.dart", "catalog_read_performance_test.dart"],
    ),
    (
        "MUT-22",
        "lib/stores/notification_store.dart",
        "if (_disposed || generation != _generation) return;\n    _cursor(result);",
        "if (false) return;\n    _cursor(result);",
        ["notification_store_test.dart", "campaign_resilience_test.dart"],
    ),
    (
        "MUT-23",
        "lib/stores/session_store.dart",
        "return _refreshing ??= _refreshAccessToken()",
        "return _refreshing = _refreshAccessToken()",
        ["session_store_test.dart"],
    ),
    (
        "MUT-24",
        "lib/controllers/conversation_controller.dart",
        "(m.id == tempId ? realMsg.id : m.id):",
        "m.id:",
        ["conversation_controller_test.dart"],
    ),
    (
        "MUT-25",
        "lib/services/habitat_service.dart",
        "final key = _pendingPurchases[pendingId] ?? const Uuid().v4();",
        "final key = const Uuid().v4();",
        ["habitat_service_test.dart"],
    ),
    (
        "MUT-28",
        "lib/services/authenticated_api_client.dart",
        "        !retried &&",
        "        true &&",
        ["campaign_resilience_test.dart"],
    ),
]
CURSOR = """    final body = Map<String, dynamic>.from(jsonDecode(response.body) as Map);
    for (final raw in (body['changes'] as List<dynamic>? ?? const [])) {
      await _applyRemoteChange(
        ownerId,
        deviceId,
        Map<String, dynamic>.from(raw as Map),
        token,
      );
    }
    await _database.setSyncCursor(
      ownerId,
      body['next_cursor'] as int? ?? cursor,
    );"""
MUTANTS.append(
    (
        "MUT-21",
        "lib/services/catalog_sync_service.dart",
        CURSOR,
        CURSOR[: CURSOR.index("    for (final raw")]
        + CURSOR[CURSOR.index("    await _database.setSyncCursor(") :]
        + "\n"
        + CURSOR[
            CURSOR.index("    for (final raw") : CURSOR.index(
                "    await _database.setSyncCursor("
            )
        ],
        ["campaign_resilience_test.dart"],
    )
)


def write(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def execute(target, folder, files):
    folder.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    with (folder / "tests.jsonl").open("w", encoding="utf-8") as log:
        result = run_logged(
            [
                shutil.which("flutter"),
                "test",
                "--no-pub",
                "--reporter",
                "json",
                *[str(target / "test" / f) for f in files],
            ],
            cwd=target,
            log=log,
            timeout=300,
            env=dict(
                os.environ,
                GEMINI_API_KEY="",
                FCM_SERVICE_ACCOUNT_FILE="",
                SMTP_ENABLED="false",
            ),
        )
    names, cases = {}, []
    for line in (
        (folder / "tests.jsonl")
        .read_text(encoding="utf-8", errors="replace")
        .splitlines()
    ):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("type") == "testStart":
            names[item["test"]["id"]] = item["test"]["name"]
        if item.get("type") == "testDone" and not item.get("hidden"):
            cases.append(
                {
                    "name": names.get(item["testID"], "unknown"),
                    "result": item["result"],
                    "skipped": item["skipped"],
                }
            )
    summary = {
        "exit_code": result.returncode,
        "elapsed_seconds": time.perf_counter() - started,
        "files": files,
        "cases": cases,
    }
    write(folder / "summary.json", summary)
    return summary


def main(run, attempt, baseline_only=False, files=None, mutants=None):
    run = Path(run).resolve()
    attempt = run / "attempts" / attempt
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    base = run / "sources/B/flutter"
    for f in ("package_config.json", "package_graph.json"):
        shutil.copy2(repo / ".dart_tool" / f, base / ".dart_tool" / f)
    for tool in (attempt / "executors").glob("campaign_*test.dart"):
        shutil.copy2(tool, base / "test" / tool.name)
    chosen = [m for m in MUTANTS if not mutants or m[0] in mutants.split(",")]
    if mutants and set(mutants.split(",")) - {m[0] for m in MUTANTS}:
        raise ValueError("Unknown mutant filter")
    if files and not baseline_only and not mutants:
        raise ValueError("A subset cannot gate all mutation variants")
    selected = files.split(",") if files else FILES
    if not set(selected) <= set(FILES):
        raise ValueError("Unknown test selector")
    if not baseline_only and any(not set(m[4]) <= set(selected) for m in chosen):
        raise ValueError("All selected mutant oracles must run in baseline")
    result = execute(base, attempt / "flutter_resilience/baseline", selected)
    if (
        result["exit_code"]
        or not result["cases"]
        or any(c["result"] != "success" or c["skipped"] for c in result["cases"])
    ):
        write(
            attempt / "flutter_resilience/gate.json",
            {
                "state": "BLOCKED",
                "reason": "baseline not entirely green; no mutation classification",
            },
        )
        return 1
    if baseline_only:
        return 0
    for mid, path, old, new, files in chosen:
        target = run / "sources" / (attempt.name + "-" + mid) / "flutter"
        shutil.copytree(
            base,
            target,
            ignore=shutil.ignore_patterns(".tmp", "build", ".git", ".dart_tool"),
        )
        (target / ".dart_tool").mkdir()
        for config in ("package_config.json", "package_graph.json"):
            shutil.copy2(base / ".dart_tool" / config, target / ".dart_tool" / config)
        file = target / path
        original = file.read_text(encoding="utf-8")
        # MUT-25 changes only the purchase variant, not the later expansion path.
        if original.count(old) != (2 if mid == "MUT-25" else 1):
            raise AssertionError("Recipe mismatch: " + mid)
        modified = original.replace(old, new, 1)
        file.write_text(modified, encoding="utf-8")
        summary = execute(target, attempt / "flutter_resilience" / mid, files)
        expected = {c["name"] for c in result["cases"]}
        failed = [
            c["name"]
            for c in summary["cases"]
            if c["result"] in ("error", "failure") and c["name"] in expected
        ]
        raw = (attempt / "flutter_resilience" / mid / "tests.jsonl").read_text(
            encoding="utf-8", errors="replace"
        )
        # Compilation, timeout and framework invariant errors are not behavioral kills.
        bad = any(
            t in raw
            for t in (
                "Compilation failed",
                "Failed to load",
                "Unhandled exception",
                "pending timers",
                "debug variable was changed",
                "SemanticsHandle was active",
            )
        )
        classification = (
            "INCONCLUSIVE_INFRASTRUCTURE"
            if bad or not summary["cases"] or summary["exit_code"] not in (0, 1)
            else "KILLED"
            if failed
            else "SURVIVED"
            if summary["exit_code"] == 0
            else "INCONCLUSIVE_ORACLE"
        )
        folder = attempt / "flutter_resilience" / mid
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
                "source": path,
                "source_sha": manifest["sources"]["B-flutter"]["sha"],
                "failed_baseline_test_names": failed,
                "executor_manifest": "../executor_manifest.json",
                "baseline": "baseline/summary.json",
            },
        )
        print(mid + ": " + classification, flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--files")
    parser.add_argument("--mutants")
    args = parser.parse_args()
    raise SystemExit(
        main(args.run, args.attempt, args.baseline_only, args.files, args.mutants)
    )
