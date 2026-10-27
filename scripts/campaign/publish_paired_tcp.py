"""Exploratory common-workload A/B report; never widens BR-02 completion."""

import importlib.metadata
import json
import subprocess
import sys

from load_metrics import distribution
from publish_no_usb_20261027 import ROOT, SEQ, DOC, read, sha, junit


def main():
    blocks = []
    for name in ("01-A", "02-B", "03-B", "04-A"):
        folder = SEQ / ("BR02-20261027-tcp2-"+name)
        status = read(folder / "summary.json")
        assert status["exit_code"] == 0 and status["evaluated_sources_unchanged"]
        observed = read(folder / "observations/BR02-tcp.json")
        phases = []
        evidence = [folder / p for p in ("summary.json", "junit.xml", "candidate_manifest.json", "frozen_execution_manifest.json", "observations/BR02-tcp.json")]
        for models in (0, 1, 5):
            child = folder / "observations/latency" / f"models-{models}"
            generated, server = read(child / "generator-result.json"), read(child / "server-result.json")
            assert len(generated["rows"]) == 3000
            phases.append({"controlled_inflight": models, "samples": len(generated["rows"]),
                "successful_rps": len(generated["rows"])/generated["elapsed_seconds"],
                "latency_by_route": {kind: distribution([r["http_ms"] for r in generated["rows"] if r["kind"] == kind])
                    for kind in sorted({r["kind"] for r in generated["rows"]})},
                "server_resources_before": server["server_resources_before"],
                "server_resources_after": server["server_resources_after"],
                "pool_checkedout_before_release": server["pool_checkedout_before_release"],
                "pool_checkedout_peak_observed": server["pool_checkedout_peak_observed"]})
            evidence.extend(child / p for p in ("generator-result.json", "server-result.json", "private-generator-config.json"))
        blocks.append({"block": name, "source": read(folder / "candidate_manifest.json"),
            "junit": junit(folder / "junit.xml"), "observed": observed, "phases": phases,
            "evidence": [{"private_path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)} for p in evidence]})
    assert all(b["observed"]["hardware"] == blocks[0]["observed"]["hardware"] for b in blocks)
    assert all(b["observed"]["database_version"] == blocks[0]["observed"]["database_version"] for b in blocks)
    assert all(b["source"]["executor_sha256"] == blocks[0]["source"]["executor_sha256"] for b in blocks)
    report = {"scenario": "BR-02", "state": "PARTIAL_EXECUTED", "date": "2026-10-27",
        "blocks": blocks, "measured_requests": 36000, "warmups": 1080,
        "controlled_analysis_completions": 24,
        "equal_final_counts": all(b["observed"]["final_counts"] == blocks[0]["observed"]["final_counts"] for b in blocks),
        "flutter_refs_not_executed": {ref: subprocess.check_output(["git", "rev-parse", ref], cwd=ROOT.parent / "catlogue", text=True).strip() for ref in ("main", "HEAD")},
        "shared_runtime_inspected_after_runs": {"python": sys.version,
            "packages": {name: importlib.metadata.version(name) for name in ("fastapi", "httpx", "sqlalchemy", "asyncpg")}},
        "limitations": ["ABBA order, two observations per version, common deterministic initial data and identical frozen executors; no dependency changes during blocks.",
            "Actual TCP read workloads, 0/1/5 I/O-gated analyses, SQL and process resources; not real model CPU or all LAT/LOAD/DB/RSC scenarios. Internal reference worker absent on main is not imported.",
            "Shared PC with an 8 req/s disposable soak as background. Timing distributions are descriptive; two paired blocks do not establish causal latency gain or a population P99/CI.",
            "Full planned BR-02 variants remain partial; Flutter refs are recorded but this executor is Python, not Flutter or a physical Android.",
            "Initial attempt lacked optional psutil and failed before collection; fixed using existing Win32 runtime, no package installed. Failed evidence remains preserved."],
        "executor": "scripts/run_security_regression.py --scenario BR-02-TCP with pinned QUALITY_API_SOURCE_REF, ABBA",
        "report_executor": "scripts/campaign/publish_paired_tcp.py"}
    name = "BR02-TCP-20261027-results.json"
    (DOC / name).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    matrix = read(DOC / "matrix.json")
    row = next(r for r in matrix if r["scenario"] == "BR-02")
    row.setdefault("closure_evidence", [])
    if name not in row["closure_evidence"]:
        row["closure_evidence"].append(name)
    row["limitation"] = "Common ABBA TCP/SQL/resource workloads completed on pinned main/cleanup with deterministic fixtures; no full LAT/LOAD/DB/RSC or mobile bundle certification. Other planned variants remain partial. See BR02-TCP-20261027-results.json."
    (DOC / "matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
