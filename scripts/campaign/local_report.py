"""Append a local campaign report without overwriting earlier evidence or reports."""

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess


PREFIXES = (
    "identity-",
    "delivery-",
    "crossed-api-",
    "social-volume-",
    "image-",
    "storage-resilience-",
    "flutter-social-",
    "mechanisms-",
    "pipeline-",
    "worker-race-",
    "query-plan-",
    "flutter-visual-",
    "socket-fanout-",
    "wire-contract-",
    "health-bounded-",
    "flutter-notifications-",
    "flutter-catalog-",
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def save(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def scenarios(name):
    return sorted(
        {
            f"{a}-{b}"
            for a, b in re.findall(
                r"(BR|LAT|LOAD|DB|IMG|DATA|RES|SEC|UX|RSC|DLV|ACC)-?(\d{2})", name
            )
        }
    )


def run(campaign, output):
    campaign, output = Path(campaign).resolve(), Path(output).resolve()
    output.mkdir(exist_ok=False)
    previous = output.parent / "2026-10-22-continuation"
    matrix = load(previous / "matrix.json")
    by_id = {r["scenario"]: r for r in matrix}
    manifest = load(campaign / "manifest.json")
    results, traces, evidence, observations, snapshots = {}, [], [], {}, {}
    for attempt in sorted((campaign / "attempts").iterdir()):
        if not attempt.name.startswith(PREFIXES):
            continue
        frozen = attempt / "executor_manifest.json"
        assert frozen.is_file(), f"Missing snapshot: {attempt}"
        snapshot = load(frozen)
        snapshots[attempt.name] = snapshot
        for file in sorted(attempt.rglob("*")):
            if not file.is_file() or "executors" in file.relative_to(attempt).parts:
                continue
            rel = file.relative_to(campaign).as_posix()
            evidence.append(
                {
                    "path": rel,
                    "bytes": file.stat().st_size,
                    "sha256": digest(file),
                    "publication": "LOCAL_ONLY",
                }
            )
            if file.name == "summary.json":
                summary = load(file)
                results[rel] = summary
                for case_index, case in enumerate(summary.get("cases", [])):
                    if "/identity-A/" in rel:
                        evaluated = ["A-api"]
                    elif "crossed-api-" in rel:
                        evaluated = [
                            load(attempt / "api_attempt.json")["api_arm"] + "-api",
                            case["name"][-2] + "-flutter",
                        ]
                    elif "wire-contract-" in rel:
                        evaluated = ["B-api", "B-flutter"]
                    elif "/flutter_" in rel:
                        evaluated = [
                            ("A" if "visual-A-" in attempt.name else "B") + "-flutter"
                        ]
                    else:
                        evaluated = ["B-api"]
                    tags = sorted(
                        set(
                            scenarios(case["name"])
                            + ([summary["scenario"]] if "scenario" in summary else [])
                        )
                    )
                    for tag in tags:
                        if tag in by_id:
                            row = by_id[tag]
                            row.setdefault("local_runs", []).append(rel)
                            row["state"] = "PARTIAL_EXECUTED"
                            row["limitation"] = (
                                "Somente variantes rastreadas; demais condições do plano continuam pendentes. Falhas e caracterizações não são aprovação."
                            )
                    traces.append(
                        {
                            "attempt": attempt.name,
                            "test": case["name"],
                            "scenarios": tags,
                            "result": case.get("status", case.get("result")),
                            "skipped": case.get("skipped", False),
                            "summary": rel,
                            "executor_snapshot": frozen.relative_to(
                                campaign
                            ).as_posix(),
                            "executor_snapshot_sha256": digest(frozen),
                            "evaluated_source_keys": evaluated,
                            "variant_context": summary.get("volumes", [])[case_index][
                                "count"
                            ]
                            if "volumes" in summary
                            else file.parent.name,
                            "code_commits": {
                                k: v["sha"] for k, v in snapshot["source_base"].items()
                            },
                            "selectors": summary.get(
                                "selectors", summary.get("files", [])
                            ),
                            "code_mapping": "Exact executable selectors and four frozen product SHAs; reviewed paths in findings.md. Tags alone never establish scenario completion.",
                        }
                    )
            elif (
                file.suffix == ".json"
                and "observations" in file.relative_to(attempt).parts
                and file.name not in {"api-event.json", "flutter-consumed.json"}
            ):
                # Observations are allowlisted records; do not publish logs, tokens, wire bodies or databases.
                data = load(file)
                if isinstance(data, dict) and "scenario" in data:
                    observations[rel] = data
    # The common four-way HTTP/SQLite experiment is now actually executed.
    crossed = [r for r in results if "crossed-api-" in r and "-02/" in r]
    assert len(crossed) == 2 and all(results[r]["exit_code"] == 0 for r in crossed)
    by_id["BR-04"].update(
        state="PARTIAL_EXECUTED",
        local_runs=crossed,
        limitation="Quatro combinações API A/B × cliente A/B, mesmo contrato externo de serviço, perda de resposta após commit e reinício com SQLite. Não inclui toda a interface, todos os endpoints ou IA real.",
    )
    for row in matrix:
        row["previous_report"] = "../2026-10-22-continuation/matrix.json"
        if "local_runs" in row:
            row["local_runs"] = sorted(set(row["local_runs"]))
    save(output / "source_manifest.json", manifest)
    save(output / "executor_snapshots.json", snapshots)
    save(output / "results.json", results)
    save(output / "traceability.json", traces)
    save(output / "evidence_index.json", evidence)
    save(output / "observations.json", observations)
    save(output / "matrix.json", matrix)
    counts = Counter(r["state"] for r in matrix)
    save(
        output / "counts.json",
        {
            "scenarios": len(matrix),
            "states": dict(counts),
            "executions_including_failed_attempts": len(traces),
            "case_outcomes_not_unique_scenarios": dict(
                Counter(t["result"] for t in traces)
            ),
            "completed_scenarios": 0,
        },
    )
    save(
        output / "report_provenance.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "run": campaign.name,
            "api_executor_publication_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "frozen_snapshots_authoritative": True,
            "historical_reports_preserved": True,
            "mutation_report": "../2026-10-22-continuation/mutation_history.json",
            "new_mutations_this_increment": 0,
        },
    )
    lines = [
        "# Matriz incremental — 23/10/2026",
        "",
        "Nenhum cenário integralmente concluído. Os resultados abrangem somente as variantes rastreadas; a matriz anterior permanece preservada.",
        "",
        "| Cenário | Estado | Novos registros |",
        "|---|---|---:|",
    ]
    lines += [
        f"| {r['scenario']} | {r['state']} | {len(r.get('local_runs', []))} |"
        for r in matrix
    ]
    (output / "matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "results": len(results),
                "cases": len(traces),
                "evidence_files": len(evidence),
                "matrix": dict(counts),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.run, args.output)
