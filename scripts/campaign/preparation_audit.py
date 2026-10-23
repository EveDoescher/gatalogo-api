"""Audit BR-01 prerequisites without rerunning or approving A/B benchmarks."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from verify_sources import verify


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(run, output):
    run = Path(run).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Never overwrite an audit")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    sources = manifest["sources"]
    if set(sources) != {"A-api", "B-api", "A-flutter", "B-flutter"}:
        raise AssertionError("Four source versions required")
    verification_name = output.stem + "-sources.json"
    verify(run, verification_name)
    for project in ("api", "flutter"):
        source = sources[f"A-{project}"]
        current = subprocess.check_output(
            ["git", "-C", source["repo"], "rev-parse", "main"], text=True
        ).strip()
        assert current == source["sha"], "Protected main changed"
    fixture = json.loads((run / "fixtures/manifest.json").read_text(encoding="utf-8"))
    for name, expected in fixture["image_hashes"].items():
        assert digest(run / "fixtures" / name) == expected
    pilot = run / "attempts/pilot-03"
    executors = json.loads((pilot / "executor_manifest.json").read_text(encoding="utf-8"))
    for name, expected in executors["hashes"].items():
        assert digest(pilot / "executors" / name) == expected
    rounds = [
        json.loads((pilot / "results" / label / "probe.json").read_text(encoding="utf-8"))
        for label in ("01-A", "02-B", "03-B", "04-A")
    ]
    assert all(row["fixture"] == rounds[0]["fixture"] for row in rounds)
    schema = {}
    for arm in ("A", "B"):
        root = Path(sources[f"{arm}-api"]["path"])
        schema[arm] = {
            str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(
                path.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for path in sorted((root / "alembic/versions").glob("*.py"))
        }
    assert schema["A"] and schema["A"].keys() == schema["B"].keys()
    reviewed_differences = []
    for name in schema["A"]:
        if schema["A"][name] == schema["B"][name]:
            continue
        a = (Path(sources["A-api"]["path"]) / name).read_text(encoding="utf-8")
        b = (Path(sources["B-api"]["path"]) / name).read_text(encoding="utf-8")
        assert name == "alembic/versions/20260927_11_progression.py"
        assert a.replace("from datetime import UTC, datetime\n", "", 1) == b
        reviewed_differences.append({"file": name, "change": "removal of unused datetime import only; migration statements identical"})
    value = {
        "scenario": "BR-01", "state": "COMPLETED",
        "criteria": {
            "four_immutable_shas_and_product_content": "PASS",
            "protected_main_refs": "PASS", "fixture_image_hashes": "PASS",
            "common_immutable_executor_hashes": "PASS",
            "ABBA_observed_fixture_configuration_equality": "PASS",
            "migration_statements_equivalence": "PASS",
        },
        "source_shas": {name: source["sha"] for name, source in sources.items()},
        "fixture_manifest_sha256": digest(run / "fixtures/manifest.json"),
        "executor_manifest_sha256": digest(pilot / "executor_manifest.json"),
        "migration_hashes": schema,
        "reviewed_migration_differences": reviewed_differences,
        "observed_common_fixture": rounds[0]["fixture"],
        "configuration": {
            "transport": rounds[0]["transport"],
            "external_services": rounds[0]["external_services"],
            "database": "own random-port recognition_test, separate schema per arm/round",
            "runtimes": "shared installed dependencies: controlled source comparison only",
            "host": manifest["host"],
        },
        "evidence": ["manifest.json", "fixtures/manifest.json",
                     "attempts/pilot-03/executor_manifest.json",
                     verification_name],
        "limits": [
            "Preparation is complete for E1/E2 controlled source comparison only.",
            "This does not complete BR-02/03/04/05, qualify latency samples, or certify historical deployment dependencies.",
            "No mobile build, real provider, load or benchmark was executed by this audit.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenario": "BR-01", "state": value["state"], "checks": value["criteria"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    audit(args.run, args.output)
