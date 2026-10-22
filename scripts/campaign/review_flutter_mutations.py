"""Correct runner enum handling against preserved JSONL; never rewrites original evidence."""

import argparse
import hashlib
import json
from pathlib import Path


def review(attempt):
    root = Path(attempt) / "flutter_resilience"
    baseline = json.loads((root / "baseline/summary.json").read_text(encoding="utf-8"))
    assert (
        baseline["exit_code"] == 0
        and baseline["cases"]
        and all(
            c["result"] == "success" and not c["skipped"] for c in baseline["cases"]
        )
    )
    expected = {c["name"] for c in baseline["cases"]}
    for p in sorted(root.glob("MUT-*/summary.json")):
        summary = json.loads(p.read_text(encoding="utf-8"))
        target = p.parent / "reviewed_classification.json"
        if target.exists():
            raise ValueError("Never overwrite classification review")
        log = p.parent / "tests.jsonl"
        raw = log.read_text(encoding="utf-8", errors="replace")
        assertions = {}
        names = {}
        for line in raw.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "testStart":
                names[d["test"]["id"]] = d["test"]["name"]
            if d.get("type") == "error" and d.get("isFailure"):
                assertions[d["testID"]] = True
            if (
                d.get("type") == "print"
                and "Expected:" in d.get("message", "")
                and "Actual:" in d.get("message", "")
            ):
                assertions[d["testID"]] = True
        verified = {names[i] for i in assertions if names.get(i) in expected}
        failed = [
            c["name"]
            for c in summary["cases"]
            if c["name"] in verified
            and c["result"] in ("failure", "error")
            and not c["skipped"]
        ]
        infra = any(
            s in raw
            for s in (
                "Compilation failed",
                "Failed to load",
                "SemanticsHandle was active",
                "debug variable was changed",
                "pending timers",
            )
        )
        verdict = (
            "INCONCLUSIVE_INFRASTRUCTURE"
            if infra or not summary["cases"]
            else "KILLED"
            if failed and summary["exit_code"] == 1
            else "SURVIVED"
            if summary["exit_code"] == 0
            and all(c["result"] == "success" for c in summary["cases"])
            else "INCONCLUSIVE_ORACLE"
        )
        result = {
            "mutant": p.parent.name,
            "classification": verdict,
            "baseline": "baseline/summary.json",
            "verified_assertions": failed,
            "original_classification": json.loads(
                (p.parent / "classification.json").read_text(encoding="utf-8")
            )["classification"],
            "jsonl_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "correction": "Flutter JSON reporter uses failure as well as error. Review uses baseline-matched assertion events; originals retained, no rerun or fabricated evidence.",
        }
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(p.parent.name, verdict)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", required=True)
    review(parser.parse_args().attempt)
