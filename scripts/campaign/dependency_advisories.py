"""Query the exact user-approved OSV package payload. No code or credentials sent."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import urllib.request


def request(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    query = urllib.request.Request(
        "https://api.osv.dev/v1/" + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Gatalogo-quality-campaign",
        },
    )
    with urllib.request.urlopen(query, timeout=20) as response:
        return json.load(response)


def scan(approved, output):
    approved, output = Path(approved), Path(output)
    if output.exists():
        raise ValueError("Never overwrite advisory scan")
    prepared = json.loads(approved.read_text(encoding="utf-8"))
    queries = prepared["payload"]["queries"]
    assert prepared["destination_if_authorized"] == "https://api.osv.dev/v1/querybatch"
    assert len(queries) == 254, (
        "This invocation is scoped to the 254 explicitly approved packages"
    )
    results = []
    errors = []
    advisories = {}
    for start in range(0, len(queries), 100):
        block = queries[start : start + 100]
        try:
            result = request("querybatch", {"queries": block})["results"]
            if len(result) != len(block):
                raise ValueError("OSV cardinality mismatch")
            for q, r in zip(block, result, strict=True):
                accumulated = list(r.get("vulns", []))
                token = r.get("next_page_token")
                for _ in range(3):
                    if not token:
                        break
                    page = request(
                        "querybatch", {"queries": [{**q, "page_token": token}]}
                    )["results"][0]
                    accumulated += page.get("vulns", [])
                    token = page.get("next_page_token")
                results.append(
                    {"query": q, "vulns": accumulated, "pagination_complete": not token}
                )
        except Exception as error:
            errors.append(
                {
                    "first_query_index": start,
                    "count": len(block),
                    "error_type": type(error).__name__,
                }
            )
    ids = sorted({v["id"] for r in results for v in r["vulns"]})
    for ident in ids[:25]:
        try:
            d = request("vulns/" + ident)
            advisories[ident] = {
                k: d.get(k)
                for k in (
                    "id",
                    "aliases",
                    "summary",
                    "modified",
                    "published",
                    "withdrawn",
                    "references",
                )
            }
        except Exception as error:
            errors.append({"advisory": ident, "error_type": type(error).__name__})
    result = {
        "scenario": "SEC-09",
        "queried_at": datetime.now(UTC).isoformat(),
        "methodology": "https://google.github.io/osv.dev/post-v1-querybatch/",
        "user_authorization": "Explicit approval in this conversation for these 254 package names/versions and OSV destinations on 2026-10-22",
        "approved_file_sha256": hashlib.sha256(approved.read_bytes()).hexdigest(),
        "requested_packages": len(queries),
        "checked_packages": len(results),
        "queries": results,
        "advisories": advisories,
        "errors": errors,
        "state": "EXECUTED_MATCH_REVIEW_REQUIRED"
        if not errors
        and len(results) == len(queries)
        and all(r["pagination_complete"] for r in results)
        else "PARTIAL_EXTERNAL_QUERY",
        "limitations": [
            "Database/version matches are not demonstrated application exploits",
            "No matches does not prove vulnerability absence",
            "Shared local Python runtime is not a pinned production deployment",
            "Source, app data and credentials were not sent",
            "Git/path dependencies, native SDKs and release/CORS configuration need separate review",
        ],
    }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "checked_packages": len(results),
                "requested_packages": len(queries),
                "advisory_ids": ids,
                "errors": errors,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-payload", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    scan(args.approved_payload, args.output)
