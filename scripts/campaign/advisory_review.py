"""Fetch and deduplicate only IDs returned by the explicitly approved OSV query."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import urllib.request


def fetch(ident):
    assert re.fullmatch(r"[A-Za-z0-9-]+", ident)
    with urllib.request.urlopen(
        "https://api.osv.dev/v1/vulns/" + ident, timeout=20
    ) as response:
        d = json.load(response)
    return ident, {
        k: d.get(k)
        for k in (
            "id",
            "aliases",
            "summary",
            "modified",
            "published",
            "withdrawn",
            "severity",
            "database_specific",
            "affected",
            "references",
        )
    }


def review(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("Never overwrite advisory review")
    original = json.loads(source.read_text(encoding="utf-8"))
    assert original["user_authorization"].startswith("Explicit approval")
    ids = sorted({v["id"] for r in original["queries"] for v in r["vulns"]})
    assert len(ids) <= 200, "Bounded metadata review only"
    records, errors = {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {ident: pool.submit(fetch, ident) for ident in ids}
        for ident, future in futures.items():
            try:
                key, value = future.result()
                records[key] = value
            except Exception as error:
                errors.append({"id": ident, "error_type": type(error).__name__})
    groups = []
    for ident, record in records.items():
        aliases = {ident, *(record.get("aliases") or [])}
        overlap = [g for g in groups if g & aliases]
        for g in overlap:
            aliases.update(g)
            groups.remove(g)
        groups.append(aliases)
    matched = []
    for row in original["queries"]:
        if row["vulns"]:
            matched.append(
                {"package": row["query"], "raw_ids": [v["id"] for v in row["vulns"]]}
            )
    result = {
        "scenario": "SEC-09",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "source_query": source.name,
        "matched_packages": matched,
        "raw_ids_count": len(ids),
        "metadata_records_fetched": len(records),
        "alias_groups": [sorted(g) for g in groups],
        "alias_group_count": len(groups),
        "records": records,
        "errors": errors,
        "triage": "VERSION_MATCHES_REQUIRE_APPLICABILITY_REVIEW",
        "limitations": [
            "Aliases are grouped to avoid counting GHSA/PYSEC/CVE identifiers as separate findings",
            "No exploit executed, no dependency update or product fix applied",
            "Affected package may be dev/transitive or lack a reachable application path; report does not certify exploitability",
            "Withdrawn markers and upstream metadata must be considered during human triage",
        ],
    }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "matched_packages": len(matched),
                "raw_ids": len(ids),
                "records": len(records),
                "alias_groups": len(groups),
                "errors": errors,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    review(args.input, args.output)
