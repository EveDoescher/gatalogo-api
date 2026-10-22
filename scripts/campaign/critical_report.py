"""Append-only consolidated critical campaign report; publishes no raw JWT/log traces."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ATTEMPTS = ["security-01", "security-02", "security-03", "database-01"]
AUDITS = ["accessibility-01", "accessibility-02", "accessibility-03"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flutter_tests(path):
    names, cases = {}, []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("type") == "testStart":
            names[item["test"]["id"]] = item["test"]["name"]
        if item.get("type") == "testDone" and not item.get("hidden"):
            cases.append({"name": names.get(item["testID"], "unknown"), "result": item["result"], "skipped": item["skipped"]})
    return cases


def map_case(name):
    scenarios = re.findall(r"(?:SEC|DATA|DB)\d{2}", name)
    scenarios = [s[:-2]+"-"+s[-2:] for s in scenarios]
    if "progression_failure" in name:
        scenarios += ["DATA-02", "DATA-06"]
    if any(s in name for s in ("claim", "replacement", "cancellation", "failures_retry", "expired_final")):
        scenarios += ["DATA-06"]
    if "concurrent_collection" in name:
        scenarios += ["DATA-03", "DATA-04", "DATA-07", "SEC-02"]
    if "SEC02" in name:
        code = ["app/routers/sync.py", "app/services/storage_service.py"]
    elif "SEC03" in name:
        code = ["app/services/conversations.py"]
    elif "SEC05" in name:
        code = ["app/services/storage_service.py"]
    elif "SEC07" in name:
        code = ["app/services/auth_service.py", "app/routers/auth.py"]
    elif "DATA05" in name or "DB02" in name:
        code = ["app/routers/sync.py", "app/services/catalog_sync_queries.py"]
    elif "DATA03" in name or "DATA07" in name or "concurrent_collection" in name:
        code = ["app/services/quintal_production.py", "app/services/quintal_simulation.py"]
    else:
        code = ["app/services/reference_analysis.py"]
    return sorted(set(scenarios)), code


def build(run, destination):
    run, dest = Path(run).resolve(), Path(destination).resolve()
    if dest.exists():
        raise ValueError("Never overwrite a consolidated report")
    dest.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[2]
    old = repo / "docs/campaigns/2026-10-21-pilot"
    manifest = read(run / "manifest.json")
    executor_commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    flutter_repo = manifest["sources"]["B-flutter"]["repo"]
    flutter_commit = subprocess.check_output(["git", "-C", flutter_repo, "rev-parse", "HEAD"], text=True).strip()
    evidence = {}

    def index(p):
        key = p.relative_to(run).as_posix()
        evidence[key] = {"sha256": sha(p), "bytes": p.stat().st_size, "local_only": p.suffix in (".log", ".xml", ".jsonl")}
        return key

    summaries, history, traces, observations = {}, [], [], {}
    for a in ATTEMPTS:
        root = run / "attempts" / a
        for p in sorted((root / "critical_runs").glob("*/summary.json")):
            key = index(p)
            data = read(p)
            summaries[key] = data
            for c in data["cases"]:
                scenarios, code = map_case(c["name"])
                traces.append({"attempt": a, "variant": data["run"], "test": c["name"], "scenarios": scenarios,
                               "result": c["status"], "code": code, "code_commit": manifest["sources"]["B-api"]["sha"],
                               "executor_commit": executor_commit, "executor_snapshot": f"attempts/{a}/executor_manifest.json",
                               "selectors": data["selectors"], "evidence": key})
        for p in sorted((root / "critical_runs").glob("*/classification.json")):
            d = read(p)
            d.update(attempt=a, evidence=index(p), patch=index(p.parent / "mutation.diff"),
                     executor_manifest=f"attempts/{a}/executor_manifest.json", executor_commit=executor_commit,
                     source_commit=manifest["sources"]["B-api"]["sha"])
            history.append(d)
        for p in sorted((root / "critical_runs/baseline/observations").glob("*.json")):
            observations[index(p)] = read(p)
        for p in root.rglob("*"):
            if p.is_file() and (p.suffix in (".py", ".json", ".xml", ".log", ".diff")):
                index(p)
    audits = {}
    for a in AUDITS:
        root = run / "attempts" / a
        audits[a] = {"cases": flutter_tests(root / "accessibility/tests.jsonl"), "result": read(root / "accessibility/result.json")}
        for c in audits[a]["cases"]:
            audit = c["name"].startswith("ACC")
            traces.append({"attempt": a, "variant": "Flutter-desktop", "test": c["name"],
                           "scenarios": (["ACC-01", "ACC-02", "ACC-03", "ACC-05", "ACC-06", "ACC-07"] if "ACC hold" in c["name"] else
                                         ["ACC-01", "ACC-02", "ACC-03", "ACC-05", "ACC-07"] if audit else ["RES-03"] if any(k in c["name"] for k in ("care response", "retries", "retry", "later placement")) else []),
                           "result": c["result"], "code": (["lib/pages/auth_page.dart", "lib/widgets/quintal_hold_control.dart", "lib/theme/app_theme.dart"] if audit else ["lib/services/habitat_service.dart"]),
                           "code_commit": manifest["sources"]["B-flutter"]["sha"], "executor_commit": flutter_commit if audit else manifest["sources"]["B-flutter"]["sha"],
                           "executor_snapshot": f"attempts/{a}/executor_manifest.json",
                           "selectors": ["tool/campaign_accessibility_test.dart" if audit else "test/habitat_service_test.dart"],
                           "evidence": f"attempts/{a}/accessibility/tests.jsonl"})
        for p in (root / "accessibility/observations").glob("*.json"):
            observations[index(p)] = read(p)
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in (".json", ".jsonl", ".dart", ".py"):
                index(p)
    for p in (run / "manifest.json", run / "critical_source_verification.json", run / "critical_environment_check.json"):
        index(p)
    rows = read(old / "matrix.json")
    for row in rows:
        row["previous_report"] = "../2026-10-21-pilot/matrix.json"
        row["previous_runs"] = row.pop("runs")
        row["runs"] = []
        sid = row["scenario"]
        matches = [t["evidence"] for t in traces if sid in t["scenarios"] and t["attempt"] in ("security-03", "database-01") and t["variant"] == "baseline"]
        if matches:
            row.update(state="PARTIAL_EXECUTED", runs=sorted(set(matches)), limitation="Selected variants only; see variant ledger and approved plan. No whole-scenario completion.")
        if sid == "RES-03":
            row.update(state="PARTIAL_EXECUTED", runs=["attempts/accessibility-03/accessibility/tests.jsonl"],
                       limitation="HabitatService: late care after account change/disposal, persisted retry keys and older placement response; previous chat/inbox retained. Catalog/analysis and remaining lifecycle variants pending.")
        if sid in ("ACC-01", "ACC-02", "ACC-03", "ACC-05", "ACC-06", "ACC-07"):
            row.update(state="PARTIAL_EXECUTED", runs=["attempts/accessibility-03/accessibility/tests.jsonl"],
                       findings=sid in ("ACC-01", "ACC-02", "ACC-03", "ACC-07"),
                       limitation="Initial AuthPage viewport (320/412 x 800, scale 1/2) and isolated hold control only. No readers/device/scroll/error-state certification.")
        if sid == "ACC-08":
            row.update(state="PENDING", limitation="Error announcements/focus desktop variants not executed; device portions also pending")
        if sid == "BR-04":
            row.update(state="PENDING", runs=[], limitation="ERRATUM: previous partial label incorrect. Crossed API/client versions were not executed.")
    registry = read(old / "mutation_registry.json")
    for family in registry:
        runs = [m for m in history if m["family"] == family["family"]]
        family["previous_pilot_classification"] = family["classification"]
        family["history"] = runs
        if runs:
            latest = {}
            for m in runs:
                latest[m["mutant"]] = m
            family.update(state="PARTIAL_EXECUTED", classification="KILLED" if all(m["classification"] == "KILLED" for m in latest.values()) else "REVIEW",
                          executor="scripts/campaign/critical_suite.py", limitation="Only registered minimal defects, not all possible mutations or approved variants")
    save(dest / "source_manifest.json", {"sources": manifest["sources"], "api_executor_commit": executor_commit, "flutter_audit_commit": flutter_commit,
                                           "runtime": "Same installed Python/Flutter dependencies as pilot; not historical deployment recreation"})
    save(dest / "api_results.json", summaries)
    save(dest / "flutter_results.json", audits)
    save(dest / "observations.json", observations)
    save(dest / "traceability.json", traces)
    save(dest / "mutation_history.json", history)
    save(dest / "mutation_registry.json", registry)
    save(dest / "matrix.json", rows)
    save(dest / "evidence_index.json", {"local_campaign": str(run), "files": evidence, "privacy": "Raw logs/JUnit remain local; public summaries contain synthetic data only"})
    receipts = []
    for p in sorted((run.parent.parent / "quality-archives").glob(run.name+"-*.receipt.json")):
        d = read(p)
        receipts.append({k: d[k] for k in ("archive", "sha256", "created_at", "verified_members", "originals_removed")})
    save(dest / "archive_receipts.json", receipts)
    shutil.copy2(run / "critical_source_verification.json", dest / "product_source_verification.json")
    shutil.copy2(repo / "scripts/campaign/confirmatory_protocol.json", dest / "confirmatory_protocol.json")
    lines = ["# Matriz cumulativa — continuação", "", "Nenhum cenário inteiro foi promovido a concluído. Histórico anterior preservado no piloto.", "", "| ID | Estado | Novas evidências | Limite |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scenario']} | {r['state']} | {len(r['runs'])} | {r.get('limitation', 'Ver plano e histórico anterior')} |")
    (dest / "matrix.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    save(dest / "counts.json", {"states": dict(Counter(r["state"] for r in rows)), "mutation_executions": dict(Counter(m["classification"] for m in history)),
                              "unique_mutant_variants": len({m["mutant"] for m in history}), "mutant_families": len({m["family"] for m in history})})
    print(json.dumps(read(dest / "counts.json")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    build(args.run, args.destination)
