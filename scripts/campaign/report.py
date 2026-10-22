"""Build a small, sanitised evidence index/matrix; never promotes partial cases."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(run, destination):
    run, destination = Path(run).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "matrix.json").exists():
        raise ValueError("Never overwrite a previous consolidated report")
    manifest = read(run / "manifest.json")
    rows = []
    for root in (run / "approved_plans" / "api", run / "approved_plans" / "flutter"):
        for line in (root / "test_plan_efficiency.md").read_text(encoding="utf-8").splitlines():
            match = re.match(r"\| ((?:BR|LAT|LOAD|DB|IMG|REID|ACC|DATA|RES|SEC|UX|RSC|DLV)-\d+) ", line)
            if match:
                rows.append({"scenario": match[1], "state": "PENDING", "runs": [], "scope": line.split(" | ")[1]})
    assert len(rows) == len({r["scenario"] for r in rows}) == 95
    probes, flutter, mutants = {}, {}, {}
    evidence = []
    evidence_paths = [run / "manifest.json", run / "executor_manifest.json", run / "fixtures" / "manifest.json"]
    evidence_paths += list(run.glob("attempts/*/executor_manifest.json"))
    evidence_paths += list(run.glob("**/flutter_results/*/test.jsonl"))
    evidence_paths += list(run.glob("sources/B/api/.tmp/integration-*/pytest.log"))
    evidence_paths += list(run.glob("harness-tests*.log"))
    for path in sorted(run.glob("**/results/*/probe.json")):
        # Avoid traversing any future embedded copies of reports in source archives.
        if "sources" in path.relative_to(run).parts:
            continue
        key = str(path.relative_to(run))
        data = read(path)
        if "MUT-" in path.parent.name:
            continue
        probes[key] = {"label": data["label"], "cases": data["cases"],
                       "lat04": data["lat04"], "resources_start": data["resources_start"],
                       "resources_end": data["resources_end"],
                       "infrastructure_error": bool(data.get("infrastructure_error")),
                       "samples": data["samples"]}
        evidence_paths.append(path)
        response = path.parent / "pull_response.json"
        if response.exists():
            evidence_paths.append(response)
    for path in sorted(run.glob("**/flutter_results/*/samples.json")):
        if "sources" not in path.relative_to(run).parts:
            flutter[str(path.relative_to(run))] = read(path)
            evidence_paths.append(path)
    for path in sorted(run.glob("**/results/MUT-*/classification.json")):
        mutants[str(path.relative_to(run))] = read(path)
        evidence_paths.extend([path, path.parent / "mutation.diff", path.parent / "probe.json"])
    for row in rows:
        sid = row["scenario"]
        matches = [key for key, p in probes.items() if any(c["scenario"] == sid for c in p["cases"])]
        if sid == "DB-01" and flutter:
            matches.extend(flutter)
        if matches:
            row.update(state="PARTIAL_EXECUTED", runs=matches)
        if sid == "BR-01":
            row.update(state="PREPARED", runs=["manifest.json"],
                       limitation="Frozen four SHAs; component probes only, no full mobile/API package comparison yet")
        if sid in ("BR-02", "BR-03") and any(not p["infrastructure_error"] for p in probes.values()):
            row.update(state="PARTIAL_EXECUTED", runs=list(probes),
                       limitation="Common external ASGI client and separate desktop SQLite component probes; not full mobile/API bundle comparison")
        if sid == "BR-04":
            row.update(state="PENDING", runs=[],
                       limitation="No crossed API/client version experiments executed; earlier partial label was incorrect")
        if sid.startswith("ACC-") or sid.startswith("REID-") or sid in ("DLV-01", "DLV-02", "DLV-03", "DLV-04", "DLV-06"):
            row["state"] = "BLOCKED_EXTERNAL_PREREQUISITES"
        if sid == "RES-03":
            logs = list(run.glob("**/flutter_results/RES-03/test.jsonl"))
            successful = []
            for log in logs:
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        item = json.loads(line)
                        if item.get("type") == "done" and item.get("success"):
                            successful.append(str(log.relative_to(run)))
                    except ValueError:
                        pass
            if successful:
                row.update(state="PARTIAL_EXECUTED", runs=successful,
                           limitation="B chat/inbox account guards only; catalog/analysis/Quintal late responses pending")
    pattern = r"^\| (MUT-\d+) P\d \| (.*?) \| (.*?) \|$"
    text = (run / "approved_plans" / "api" / "test_plan_efficiency.md").read_text(encoding="utf-8")
    registry = [{"family": mid, "minimal_change": change, "expected_oracle": oracle,
                 "state": "PENDING", "classification": None} for mid, change, oracle in re.findall(pattern, text, re.M)]
    assert len(registry) == 28
    for entry in registry:
        result = next((v for v in mutants.values() if v["mutant"] == entry["family"]), None)
        if result:
            entry.update(state="PARTIAL_EXECUTED", classification=result["classification"],
                         elapsed_seconds=result["elapsed_seconds"], baseline_evidence=result["baseline_evidence"])
        else:
            entry["elapsed_seconds"] = None
        entry["executor"] = "scripts/campaign/mutation_cases.py" if entry["family"] in ("MUT-01", "MUT-12") else "PENDING_IMPLEMENTATION: use approved oracle, baseline gate and one copy per mutant"
    for index, path in enumerate(dict.fromkeys(evidence_paths), 1):
        if path.exists():
            evidence.append({"id": f"EV-{index:03}", "path": str(path.relative_to(run)),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    save(destination / "matrix.json", rows)
    lines = ["# Matriz da campanha-piloto", "", "Estado do cenário completo; subcasos aprovados não encerram o ID.", "",
             "| ID | Estado | Evidências relativas à campanha |", "|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['scenario']} | {row['state']} | {', '.join(row['runs']) or '—'} |")
    (destination / "matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    save(destination / "mutation_registry.json", registry)
    save(destination / "evidence_index.json", evidence)
    # Compress synthetic raw samples; no bulky stdout logs or private images in Git.
    raw = json.dumps({"api": probes, "flutter": flutter, "mutants": mutants}, ensure_ascii=False).encode()
    (destination / "pilot_samples.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    summary = {"api": {}, "flutter": {}, "mutants": mutants}
    for key, probe in probes.items():
        values = [s for s in probe["samples"] if s["label"] == "LAT-04" and not s.get("warmup")]
        summary["api"][key] = {k: v for k, v in probe.items() if k != "samples"}
        summary["api"][key]["select_counts_including_auth"] = sorted({
            sum(q["kind"] == "SELECT" for q in s["sql"]) for s in values})
    for key, probe in flutter.items():
        values = [s for s in probe["samples"] if not s["warmup"]]
        elapsed = sorted(s["elapsed_us"] for s in values)
        summary["flutter"][key] = {"n": len(values), "query_counts": sorted({s["queries"] for s in values}),
                                    "median_us_exploratory": elapsed[(len(elapsed) - 1) // 2],
                                    "resources": probe["resources"]}
    save(destination / "pilot_summary.json", summary)
    canonical = run / "attempts" / "pilot-03" / "results" / "02-B" / "pull_response.json"
    if canonical.exists():
        save(destination / "canonical_pull_response.json", read(canonical))
    for key, source in manifest["sources"].items():
        arm, project = key.split("-")
        source["repo"] = project
        source["path"] = f"sources/{arm}/{project}"
    save(destination / "source_manifest.json", manifest)
    verification = run / "product_source_verification.json"
    if verification.exists():
        save(destination / "product_source_verification.json", read(verification))
    print(json.dumps({"states": dict(Counter(r["state"] for r in rows)), "evidence": len(evidence),
                      "api_runs": len(probes), "flutter_runs": len(flutter), "mutants": len(mutants)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build(args.run, args.output)
