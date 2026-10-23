"""Publish an additive report for explicitly selected new attempts only."""

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

from local_report import digest, load, save, scenarios


def run(campaign, previous, output, attempts):
    campaign, previous, output = map(
        lambda p: Path(p).resolve(), [campaign, previous, output]
    )
    output.mkdir(exist_ok=False)
    matrix = load(previous / "matrix.json")
    by_id = {r["scenario"]: r for r in matrix}
    source = load(campaign / "manifest.json")
    results, evidence, traces, snapshots, observations, static = {}, [], [], {}, {}, {}
    for name in attempts:
        assert Path(name).name == name
        attempt = campaign / "attempts" / name
        frozen = attempt / "executor_manifest.json"
        snapshots[name] = load(frozen)
        for file in sorted(attempt.rglob("*")):
            if not file.is_file() or "executors" in file.relative_to(attempt).parts:
                continue
            rel = file.relative_to(campaign).as_posix()
            evidence.append(
                {
                    "path": rel,
                    "sha256": digest(file),
                    "bytes": file.stat().st_size,
                    "raw_publication": "LOCAL_ONLY",
                }
            )
            if file.name == "summary.json":
                summary = load(file)
                results[rel] = summary
                for case in summary.get("cases", []):
                    tags = scenarios(case["name"])
                    if (
                        name.startswith("accessibility-flows-")
                        and "ACC flows" in case["name"]
                    ):
                        tags = ["ACC-01", "ACC-02", "ACC-03", "ACC-05", "ACC-07"]
                    if name.startswith("offline-flow-"):
                        evaluated = ["B-api", "B-flutter"]
                    elif name.startswith("accessibility-flows-"):
                        evaluated = ["B-flutter"]
                    else:
                        evaluated = [
                            "A-api" if "controls-A" in file.parent.name else "B-api"
                        ]
                    traces.append(
                        {
                            "attempt": name,
                            "test": case["name"],
                            "result": case.get("status", case.get("result")),
                            "skipped": case.get("skipped", False),
                            "scenarios": tags,
                            "summary": rel,
                            "selectors": summary.get(
                                "selectors", summary.get("files", [])
                            ),
                            "evaluated_sources": {
                                key: source["sources"][key]["sha"] for key in evaluated
                            },
                            "executor_snapshot": frozen.relative_to(
                                campaign
                            ).as_posix(),
                            "executor_snapshot_sha256": digest(frozen),
                        }
                    )
                    for tag in tags:
                        if tag not in by_id:
                            continue
                        row = by_id[tag]
                        row.setdefault("followup_runs", []).append(rel)
                        if case.get("status", case.get("result")) in {
                            "PASS",
                            "success",
                        }:
                            row["state"] = "PARTIAL_EXECUTED"
                            row["limitation"] = (
                                "Novas variantes locais rastreadas; conferir escopo e pendências no relatório. Nenhuma aprovação integral ou correção de produto."
                            )
            elif (
                file.suffix == ".json"
                and "observations" in file.relative_to(attempt).parts
            ):
                data = load(file)
                if isinstance(data, dict) and (
                    "scenario" in data or "scenarios" in data
                ):
                    observations[rel] = data
            elif file.name == "review.json" and file.parent.name == "dependency_static":
                static[rel] = load(file)
                by_id["SEC-09"].setdefault("followup_runs", []).append(rel)
    for row in matrix:
        row["previous_report"] = "../" + previous.name + "/matrix.json"
        if "followup_runs" in row:
            row["followup_runs"] = sorted(set(row["followup_runs"]))
    save(output / "matrix.json", matrix)
    save(output / "results.json", results)
    save(output / "traceability.json", traces)
    save(output / "executor_snapshots.json", snapshots)
    save(output / "source_manifest.json", source)
    save(output / "evidence_index.json", evidence)
    save(output / "observations.json", observations)
    save(output / "dependency_applicability.json", static)
    counts = {
        "scenarios": len(matrix),
        "states": dict(Counter(r["state"] for r in matrix)),
        "case_executions_including_retries": len(traces),
        "outcomes_including_retries": dict(Counter(r["result"] for r in traces)),
        "static_reviews_are_not_runtime_tests": True,
    }
    save(output / "counts.json", counts)
    save(
        output / "provenance.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "previous": "../" + previous.name + "/README.md",
            "attempts": attempts,
            "executor_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "frozen_snapshots_authoritative": True,
            "product_correction_commits": [],
            "new_mutations": 0,
        },
    )
    table = [
        "# Matriz incremental",
        "",
        "O escopo original e os relatórios anteriores permanecem preservados. Nenhuma linha foi integralmente aprovada por esta atualização.",
        "",
        "| Cenário | Estado | Novos registros |",
        "|---|---|---:|",
    ]
    table += [
        f"| {r['scenario']} | {r['state']} | {len(r.get('followup_runs', []))} |"
        for r in matrix
    ]
    (output / "matrix.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print(json.dumps(counts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--attempts", required=True)
    args = parser.parse_args()
    run(args.run, args.previous, args.output, args.attempts.split(","))
