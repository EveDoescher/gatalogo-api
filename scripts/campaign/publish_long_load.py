"""Publish finished full-duration loads only; preserve raw/private credentials locally."""

from collections import Counter
import json
from pathlib import Path

from publish_no_usb_20261027 import ROOT, SEQ, DOC, read, sha, junit
from load_metrics import summarize


def main():
    matrix = read(DOC / "matrix.json")
    for scenario in ("LOAD-01", "LOAD-02", "LOAD-06"):
        folder = SEQ / (scenario.replace("-", "") + "-20261027-full")
        summary = folder / "summary.json"
        if not summary.exists():
            continue
        status = read(summary)
        assert status["exit_code"] == 0 and status["evaluated_sources_unchanged"], folder
        observation = read(folder / "observations/long-load.json")
        assert not observation["pilot_only"]
        tests = junit(folder / "junit.xml")
        # JWT-bearing phase configs remain private; publish only aggregate observations.
        evidence = [{"private_path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)}
            for p in sorted(folder.rglob("*")) if p.is_file()]
        report = {
            "scenario": scenario, "state": "COMPLETED", "date": "2026-10-27",
            "source": read(folder / "candidate_manifest.json"),
            "summary": status, "tests": tests, "observations": observation,
            "descriptive_metrics": summarize(folder, observation["phases"]),
            "evidence": evidence, "executor": "scripts/campaign/long_load_cases.py",
            "report_executor": "scripts/campaign/publish_long_load.py",
            "limitations": [
                "One local execution per plateau; no causal branch comparison or production capacity claim.",
                "Client operation times include token refresh when required; generator queue delay is separate.",
                "Refresh counts include warmup. Warmup requests are excluded from measured phase totals.",
                "Shared PC; a small real-provider transport probe ran during LOAD-01. No physical device.",
                "Raw latency distributions are empirical, without a claim of stable population P99.",
            ],
        }
        report["transport_findings"] = [{"phase": p["phase"], "errors": p["errors"],
            "offered": p["offered"],
            "successful_valid_responses": sum(r["count"] for r in p["responses"] if r["status"] == "200" and r["valid"]),
            "interpretation": "Transport failure observed, not an HTTP error response. Fixture forces 1s server keepalive; client defaults differ. Keepalive reuse race/resource scheduling are hypotheses, not proven product defects. Revalidate with aligned expiry and isolated timing before changing product."}
            for p in observation["phases"] if p["errors"]]
        report["completion_meaning"] = "Planned experiment variants executed and integrity checked; completion does not mean zero measured transport failures. Findings remain explicit."
        name = scenario.replace("-", "") + "-20261027-results.json"
        (DOC / name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        row = next(r for r in matrix if r["scenario"] == scenario)
        row.update(state="COMPLETED", limitation="Full planned disposable E2 load variants executed; shared-host exploratory measurements, no speedup/production SLA claim.", executor=report["executor"])
        row.setdefault("closure_evidence", [])
        if name not in row["closure_evidence"]:
            row["closure_evidence"].append(name)
    (DOC / "matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Matriz sequencial", "", "| Cenário | Estado |", "| --- | --- |"]
    lines.extend(f"| {r['scenario']} | {r['state']} |" for r in matrix)
    (DOC / "matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = dict(Counter(r["state"] for r in matrix))
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
