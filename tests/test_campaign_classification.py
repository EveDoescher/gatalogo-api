"""Prevent genuine Flutter assertions and infrastructure errors being misclassified."""

import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "review",
    Path(__file__).resolve().parents[1]
    / "scripts/campaign/review_flutter_mutations.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "result,assertion,compile_error,expected",
    [
        ("failure", True, False, "KILLED"),
        ("error", False, False, "INCONCLUSIVE_ORACLE"),
        ("failure", True, True, "INCONCLUSIVE_INFRASTRUCTURE"),
        ("success", False, False, "SURVIVED"),
    ],
)
def test_review_flutter_reporter_protocol_preserves_original_evidence(
    tmp_path, result, assertion, compile_error, expected
):
    root = tmp_path / "flutter_resilience"
    baseline = root / "baseline"
    baseline.mkdir(parents=True)
    (baseline / "summary.json").write_text(
        json.dumps(
            {
                "exit_code": 0,
                "cases": [
                    {"name": "behavioral oracle", "result": "success", "skipped": False}
                ],
            }
        )
    )
    target = root / "MUT-19"
    target.mkdir()
    events = [{"type": "testStart", "test": {"id": 1, "name": "behavioral oracle"}}]
    if result != "success":
        events.append(
            {
                "type": "error",
                "testID": 1,
                "isFailure": assertion,
                "error": "Expected: empty. Actual: leaked"
                if assertion
                else "Bad state",
            }
        )
    if compile_error:
        events.append({"type": "print", "message": "Compilation failed"})
    log = "\n".join(json.dumps(e) for e in events)
    (target / "tests.jsonl").write_text(log)
    (target / "summary.json").write_text(
        json.dumps(
            {
                "exit_code": 0 if result == "success" else 1,
                "cases": [
                    {"name": "behavioral oracle", "result": result, "skipped": False}
                ],
            }
        )
    )
    original = {"classification": "SURVIVED"}
    (target / "classification.json").write_text(json.dumps(original))
    module.review(tmp_path)
    reviewed = json.loads((target / "reviewed_classification.json").read_text())
    assert reviewed["classification"] == expected
    assert (
        reviewed["jsonl_sha256"]
        == hashlib.sha256((target / "tests.jsonl").read_bytes()).hexdigest()
    )
    assert json.loads((target / "classification.json").read_text()) == original
    with pytest.raises(ValueError):
        module.review(tmp_path)
