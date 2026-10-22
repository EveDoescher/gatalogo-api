"""Append-only continuation report. Raw logs and ephemeral credentials stay local."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from critical_report import flutter_tests

ATTEMPTS = [
    "extended-01",
    "extended-02",
    "extended-03",
    "targeted-01",
    "targeted-02",
    "queue-01",
    "privacy-01",
    "privacy-02",
    "full-regression-01",
    "live-diagnostic-01",
    "live-diagnostic-02-enabled",
    "live-diagnostic-03-disabled",
    "flutter-resilience-01",
    "flutter-resilience-02",
    "flutter-resilience-03",
    "flutter-resilience-04",
    "accessibility-04",
    "flutter-full-01",
    "network-01",
    "probe-layout-01",
]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping(name, flutter=False):
    scenarios = [
        s[:-2] + "-" + s[-2:]
        for s in re.findall(r"(?:SEC|DATA|DB|IMG|LOAD|DLV|RES|ACC)\d{2}", name)
    ]
    code = []
    if "SEC10_tcp" in name:
        scenarios.extend(["SEC-03", "SEC-10"])
        code.extend(["app/routers/websocket.py", "app/services/auth_service.py"])
    rules = [
        (
            ("conflict", "tombstone", "reference_filter", "DB02"),
            ["DATA-05", "DB-02"],
            ["app/routers/sync.py", "app/services/catalog_sync_queries.py"],
        ),
        (
            ("upload_failure",),
            ["DATA-02"],
            ["app/routers/sync.py", "app/services/storage_service.py"],
        ),
        (("events_wait",), ["DATA-02"], ["app/services/chat_commands.py"]),
        (
            ("resource_action", "private_photo"),
            ["SEC-02"],
            ["app/routers/sync.py", "app/services/storage_service.py"],
        ),
        (
            ("attachment_member", "websocket_loop", "revocation", "tcp_preexisting"),
            ["SEC-02", "SEC-03"],
            ["app/routers/websocket.py", "app/services/conversations.py"],
        ),
        (
            ("credential", "smtp", "registration_and"),
            [],
            ["app/routers/auth.py", "app/services/auth_service.py"],
        ),
        (
            ("export_", "favorites_owner"),
            ["SEC-08"],
            ["app/routers/product.py"],
        ),
        (
            ("clue_location",),
            ["SEC-04"],
            ["app/services/report_media.py", "app/routers/product.py"],
        ),
        (
            ("releases_database", "releases_read", "DB05"),
            ["DB-05"],
            [
                "app/main.py",
                "app/services/reference_analysis.py",
                "app/services/vision_worker.py",
            ],
        ),
        (
            ("claim", "lease", "replacement", "cancellation", "expired_final"),
            ["DATA-06"],
            ["app/services/reference_analysis.py", "app/services/vision_worker.py"],
        ),
        (
            ("same_intent", "concurrent_collection"),
            ["DATA-03", "DATA-07"],
            ["app/services/quintal_production.py"],
        ),
        (
            ("controlled_reference_queue",),
            ["LOAD-04"],
            ["app/services/reference_analysis.py"],
        ),
        (("fanout",), ["LOAD-05"], ["app/services/websocket_manager.py"]),
        (
            ("live_build_plant", "rollout_disabled"),
            ["DATA-07"],
            ["app/services/quintal_production.py", "app/services/milestones.py"],
        ),
    ]
    for keys, ids, paths in rules:
        if any(k in name for k in keys):
            scenarios.extend(ids)
            code.extend(paths)
    if flutter:
        code = []
        frules = [
            (
                ("DB01", "batch"),
                ["DB-01"],
                [
                    "lib/data/sqlite_id_batches.dart",
                    "lib/data/repositories/catalog_repository.dart",
                ],
            ),
            (
                ("DB07", "cursor", "partial page", "SQLite concurrent"),
                ["DB-07", "RES-08"],
                [
                    "lib/services/catalog_sync_service.dart",
                    "lib/data/cat_database.dart",
                ],
            ),
            (
                ("RES03", "owner", "stale", "account"),
                ["RES-03"],
                [
                    "lib/services/catalog_sync_coordinator.dart",
                    "lib/stores/notification_store.dart",
                ],
            ),
            (
                ("single-flight", "single flight", "sync triggers", "refresh"),
                ["RES-07"],
                [
                    "lib/services/catalog_sync_service.dart",
                    "lib/stores/session_store.dart",
                ],
            ),
            (("401",), ["LAT-03"], ["lib/services/authenticated_api_client.dart"]),
            (
                ("ACC login",),
                ["ACC-01", "ACC-02", "ACC-03", "ACC-05", "ACC-07"],
                ["lib/pages/auth_page.dart", "lib/theme/app_theme.dart"],
            ),
            (
                ("ACC hold",),
                ["ACC-03", "ACC-05", "ACC-06"],
                ["lib/widgets/quintal_hold_control.dart"],
            ),
            (("ACC08",), ["ACC-08"], ["lib/pages/auth_page.dart"]),
        ]
        for keys, ids, paths in frules:
            if any(k in name for k in keys):
                scenarios.extend(ids)
                code.extend(paths)
    return sorted(set(scenarios)), sorted(set(code))


def build(run, destination):
    run, dest = Path(run).resolve(), Path(destination).resolve()
    if dest.exists():
        raise ValueError("Never overwrite a consolidated report")
    dest.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[2]
    old = repo / "docs/campaigns/2026-10-21-critical"
    manifest = read(run / "manifest.json")
    commits = {
        k: subprocess.check_output(
            ["git", "-C", str(v), "rev-parse", "HEAD"], text=True
        ).strip()
        for k, v in {
            "api": repo,
            "flutter": manifest["sources"]["B-flutter"]["repo"],
        }.items()
    }
    evidence, summaries, traces, observations = {}, {}, [], {}
    history = read(old / "mutation_history.json")

    def index(p):
        key = p.relative_to(run).as_posix()
        evidence[key] = {
            "sha256": digest(p),
            "bytes": p.stat().st_size,
            "local_only": p.suffix in (".log", ".xml", ".jsonl", ".db", ".sqlite"),
        }
        return key

    for a in ATTEMPTS:
        root = run / "attempts" / a
        assert (root / "executor_manifest.json").is_file(), a
        for p in sorted(root.rglob("summary.json")):
            if "executors" in p.parts:
                continue
            data = read(p)
            key = index(p)
            summaries[key] = data
            flutter = "flutter_resilience" in p.parts or "flutter_full" in p.parts
            is_a = p.parent.name == "official-export"
            source = manifest["sources"][
                ("A" if is_a else "B") + ("-flutter" if flutter else "-api")
            ]["sha"]
            for c in data.get("cases", []):
                ids, code = mapping(c["name"], flutter)
                traces.append(
                    {
                        "attempt": a,
                        "variant": p.parent.name,
                        "test": c["name"],
                        "scenarios": ids,
                        "result": "SKIPPED"
                        if c.get("skipped")
                        else c.get("status", c.get("result")),
                        "code": code,
                        "code_commit": source,
                        "executor_commit": commits["flutter" if flutter else "api"],
                        "executor_snapshot": f"attempts/{a}/executor_manifest.json",
                        "evidence": key,
                        "selectors": data.get("selectors", data.get("files", [])),
                        "mapping_limit": "Paths are reviewed entry points; empty list means regression-only, not scenario completion.",
                    }
                )
        if a == "accessibility-04":
            key = index(root / "accessibility/tests.jsonl")
            summaries[key] = {
                "cases": flutter_tests(root / "accessibility/tests.jsonl"),
                **read(root / "accessibility/result.json"),
            }
            for c in summaries[key]["cases"]:
                ids, code = mapping(c["name"], True)
                traces.append(
                    {
                        "attempt": a,
                        "variant": "headless",
                        "test": c["name"],
                        "scenarios": ids,
                        "result": c["result"],
                        "code": code,
                        "code_commit": manifest["sources"]["B-flutter"]["sha"],
                        "executor_commit": commits["flutter"],
                        "executor_snapshot": f"attempts/{a}/executor_manifest.json",
                        "evidence": key,
                        "selectors": [
                            "tool/campaign_accessibility_test.dart",
                            "test/habitat_service_test.dart",
                        ],
                    }
                )
        if a == "probe-layout-01":
            for p in sorted((root / "flutter_results").glob("*/test.jsonl")):
                key = index(p)
                cases = flutter_tests(p)
                summaries[key] = {
                    "cases": cases,
                    "scope": "Probe relocation revalidation; timings remain exploratory",
                }
                arm = "A" if p.parent.name.endswith("A") else "B"
                for c in cases:
                    ids, code = mapping(c["name"], True)
                    if "DB-01" in c["name"]:
                        ids = ["DB-01"]
                        code = [
                            "lib/stores/cat_store.dart",
                            "lib/data/cat_database.dart",
                        ]
                    traces.append(
                        {
                            "attempt": a,
                            "variant": p.parent.name,
                            "test": c["name"],
                            "scenarios": ids,
                            "result": "SKIPPED" if c["skipped"] else c["result"],
                            "code": code,
                            "code_commit": manifest["sources"][arm + "-flutter"]["sha"],
                            "executor_commit": commits["api"],
                            "executor_snapshot": f"attempts/{a}/executor_manifest.json",
                            "evidence": key,
                            "selectors": ["tool/campaign_probe_test.dart"]
                            if p.parent.name != "RES-03"
                            else [
                                "test/conversation_controller_test.dart",
                                "test/notification_store_test.dart",
                            ],
                        }
                    )
        for p in sorted(root.rglob("classification.json")):
            original = read(p)
            reviewed = p.with_name("reviewed_classification.json")
            data = read(reviewed) if reviewed.exists() else original.copy()
            data.update(
                mutant=original.get("mutant", p.parent.name),
                attempt=a,
                evidence=index(p),
                original_classification=original["classification"],
                executor_manifest=f"attempts/{a}/executor_manifest.json",
                executor_commit=commits["api"],
                source_commit=manifest["sources"][
                    "B-flutter" if "flutter_resilience" in p.parts else "B-api"
                ]["sha"],
            )
            data["family"] = data["mutant"].split("/")[0]
            if reviewed.exists():
                data["review_evidence"] = index(reviewed)
            if p.with_name("mutation.diff").exists():
                data["patch"] = index(p.with_name("mutation.diff"))
            history.append(data)
        for p in root.rglob("observations/*.json"):
            observations[index(p)] = read(p)
        for p in root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                index(p)

    rows = read(old / "matrix.json")
    # Only explicit directed tests update coverage. Broad regressions are not automatically full variants.
    for row in rows:
        sid = row["scenario"]
        row["previous_report"] = "../2026-10-21-critical/matrix.json"
        row["previous_runs"] = row.get("runs", []) + row.get("previous_runs", [])
        matched = [
            t["evidence"]
            for t in traces
            if sid in t["scenarios"]
            and t["attempt"]
            not in (
                "extended-01",
                "targeted-01",
                "flutter-resilience-01",
                "full-regression-01",
                "flutter-full-01",
            )
            and not t["variant"].startswith("MUT-")
        ]
        row["runs"] = sorted(set(matched))
        if matched:
            row.update(
                state="PARTIAL_EXECUTED",
                limitation="Somente variantes dirigidas registradas na rastreabilidade; falha observada também conta como execução, não como aprovação. Variantes restantes do plano continuam pendentes.",
            )
        if sid == "BR-04":
            row.update(
                state="PENDING",
                runs=[],
                limitation="Cliente/API cruzados não executados; erro de classificação anterior permanece corrigido.",
            )
        if sid == "LOAD-05":
            row["limitation"] = (
                "Fanout sintético 1/10/50 e cleanup, sem TCP real, consumidor lento ou reconexão parcial."
            )
        if sid == "DLV-04":
            row.update(
                state="PARTIAL_EXECUTED",
                limitation="Transporte FCM mockado em testes existentes e MUT-26; entrega real em aparelho e sucesso parcial/retry completos pendentes.",
            )
        if sid == "ACC-08":
            row["limitation"] = (
                "Erro de e-mail vazio, semântica exposta e teclado; demais erros, anúncio assistivo e gestão de foco pendentes."
            )
        if sid == "DLV-01":
            row["limitation"] = (
                "Registro/OTP/reuso com SMTP real em Mailpit próprio; recuperação de senha com SMTP real ainda pendente."
            )
        if sid == "SEC-09":
            row.update(
                state="PARTIAL_EXECUTED",
                runs=["dependency_advisories.json", "dependency_advisory_review.json"],
                limitation="254 nomes/versões aprovados consultados no OSV; aplicabilidade e configuração release/CORS/docs ainda pendentes.",
            )
        assert row["state"] != "COMPLETED"
    assert len(rows) == 95 and len({r["scenario"] for r in rows}) == 95
    registry = read(old / "mutation_registry.json")
    for family in registry:
        items = [m for m in history if m.get("family") == family["family"]]
        latest = {}
        for m in items:
            latest[m["mutant"]] = m
        family.update(
            history=items,
            state="PARTIAL_EXECUTED",
            classification="KILLED"
            if latest and all(m["classification"] == "KILLED" for m in latest.values())
            else "REVIEW",
            limitation="Apenas defeitos mínimos registrados; não é cobertura integral nem escore de mutação aleatória.",
        )
    latest = {}
    for m in history:
        if m["classification"] not in ("BLOCKED_BASELINE", "BLOCKED"):
            latest[m["mutant"]] = m
    for name in (
        "dependency_advisories.json",
        "dependency_advisory_review.json",
        "osv_approval_payload.json",
        "concurrent_host_observation.json",
        "continuation_inventory.json",
        "continuation_source_verification.json",
        "continuation_environment_check.json",
        "manifest.json",
    ):
        p = run / name
        if p.exists():
            index(p)
            if name != "osv_approval_payload.json":
                shutil.copy2(p, dest / name)
    save(
        dest / "source_manifest.json",
        {
            "sources": manifest["sources"],
            "executor_commits": commits,
            "runtime": "Same installed dependencies as pilot, not a reconstruction of historical deployments",
        },
    )
    save(dest / "results.json", summaries)
    save(dest / "observations.json", observations)
    save(dest / "traceability.json", traces)
    save(dest / "mutation_history.json", history)
    save(dest / "mutation_registry.json", registry)
    save(dest / "matrix.json", rows)
    save(
        dest / "evidence_index.json",
        {
            "local_campaign": str(run),
            "files": evidence,
            "privacy": "Raw logs/JUnit/SQLite local only; no tar, OTP or JWT published",
        },
    )
    receipts = []
    for p in sorted(
        (run.parent.parent / "quality-archives").glob(run.name + "-*.receipt.json")
    ):
        d = read(p)
        receipts.append(
            {
                k: d[k]
                for k in (
                    "archive",
                    "sha256",
                    "created_at",
                    "verified_members",
                    "originals_removed",
                )
            }
        )
    save(dest / "archive_receipts.json", receipts)
    shutil.copy2(
        repo / "scripts/campaign/confirmatory_protocol.json",
        dest / "confirmatory_protocol.json",
    )
    save(
        dest / "counts.json",
        {
            "states": dict(Counter(r["state"] for r in rows)),
            "historical_classifications": dict(
                Counter(m["classification"] for m in history)
            ),
            "latest_classifications": dict(
                Counter(m["classification"] for m in latest.values())
            ),
            "latest_variants": len(latest),
            "families": len(registry),
            "note": "Execution counts overlap; do not add regression and targeted cases as unique tests.",
        },
    )
    lines = [
        "# Matriz cumulativa — 22/10/2026",
        "",
        "95 cenários preservados. Nenhum cenário integralmente concluído. Cada subconjunto executado está na rastreabilidade; estados parciais não são aprovação.",
        "",
        "| ID | Estado | Evidências novas | Limitação |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r['scenario']} | {r['state']} | {len(r['runs'])} | {r.get('limitation', '')} |"
        for r in rows
    ]
    (dest / "matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(read(dest / "counts.json")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    build(args.run, args.destination)
