"""Review the current dependency graph against the approved OSV metadata only."""

import argparse
from collections import deque
from datetime import UTC, datetime
import hashlib
from importlib import metadata
import json
from pathlib import Path
import subprocess

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import yaml

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(scan_path, details_path, output):
    output = Path(output)
    assert not output.exists()
    scan = json.loads(Path(scan_path).read_text())
    details = json.loads(Path(details_path).read_text())
    assert (
        scan["checked_packages"] == 254 and not scan["errors"] and not details["errors"]
    )
    roots = [
        Requirement(line)
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    direct = {canonicalize_name(r.name) for r in roots}
    seen = set()
    paths = {}
    missing = []
    queue = deque(
        (canonicalize_name(r.name), frozenset(r.extras), [canonicalize_name(r.name)])
        for r in roots
    )
    while queue:
        name, extras, chain = queue.popleft()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        paths.setdefault(name, chain)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        for raw in dist.requires or []:
            child = Requirement(raw)
            if child.marker and not any(
                child.marker.evaluate({"extra": extra}) for extra in extras or [""]
            ):
                continue
            dep = canonicalize_name(child.name)
            queue.append((dep, frozenset(child.extras), chain + [dep]))
    current = {
        canonicalize_name(d.metadata["Name"]): d.version
        for d in metadata.distributions()
        if d.metadata["Name"]
    }
    lock = ROOT.parent / "catlogue/pubspec.lock"
    pub = yaml.safe_load(lock.read_text())["packages"]
    version_drift = []
    for row in scan["queries"]:
        q = row["query"]
        package = q["package"]
        name = package["name"]
        actual = (
            current.get(canonicalize_name(name))
            if package["ecosystem"] == "PyPI"
            else pub.get(name, {}).get("version")
        )
        if actual != q["version"]:
            version_drift.append(
                {"package": package, "queried": q["version"], "current": actual}
            )
    reviewed = {
        "pillow": (
            "INPUT_PARSER_REQUIRES_UPSTREAM_REMEDIATION_REVIEW",
            "Image.open handles supplied/stored bytes; parser reachability is present, but advisory-specific malformed files were not executed.",
            [
                "app/services/preprocessing_service.py",
                "app/services/report_media.py",
                "app/routers/sync.py",
            ],
        ),
        "pyjwt": (
            "TOKEN_PARSER_WITH_EXPLICIT_ALGORITHMS",
            "Access tokens use HS256/issuer and persisted session checks; Apple now validates RS256/audience/issuer. Prior bypass wording is historical and is not asserted here. Advisory-specific prerequisites require individual review.",
            [
                "app/services/auth_service.py",
                "app/services/apple_identity.py",
                "app/routers/auth.py",
            ],
        ),
        "hydra-core": (
            "OPTIONAL_MODEL_CONFIGURATION_DEPENDENCY",
            "SAM-2/configuration path is included in declared requirements; no attacker-provided Hydra configuration was demonstrated.",
            ["app/services/segmentation_service.py", "requirements.txt"],
        ),
        "mako": (
            "MIGRATION_TOOLING_DEPENDENCY",
            "Present through migration tooling; no application-controlled Mako template lookup was found in app imports.",
            ["requirements.txt"],
        ),
        "urllib3": (
            "TRANSITIVE_NETWORK_DEPENDENCY",
            "Transitive runtime dependency; transport configuration and advisory-specific prerequisites are not established by a version match.",
            ["requirements.txt"],
        ),
        "certifi": (
            "TRANSITIVE_TRUST_STORE_DEPENDENCY",
            "Certificate bundle is transitive; matched CA changes require upstream trust-store review, not an assumed application exploit.",
            ["requirements.txt"],
        ),
        "filelock": (
            "TRANSITIVE_MODEL_OR_TOOLING_DEPENDENCY",
            "No direct app FileLock invocation found; model/download/tooling dependency path recorded below.",
            ["requirements.txt"],
        ),
        "setuptools": (
            "ENVIRONMENT_BUILD_TOOLING",
            "Build tooling present in shared environment; production runtime reachability not established.",
            ["requirements.txt"],
        ),
    }
    matches = []
    for row in scan["queries"]:
        if not row["vulns"]:
            continue
        name = canonicalize_name(row["query"]["package"]["name"])
        classification, rationale, files = reviewed.get(
            name,
            (
                "REQUIRES_REVIEW",
                "No reviewed reachability rule exists.",
                ["requirements.txt"],
            ),
        )
        matches.append(
            {
                "query": row["query"],
                "installed_direct_requirement": name in direct,
                "declared_dependency_path": paths.get(name),
                "classification": classification,
                "rationale": rationale,
                "reviewed_sources": [
                    {"path": f, "sha256": sha(ROOT / f)} for f in files
                ],
                "advisory_ids": [v["id"] for v in row["vulns"]],
                "confirmed_exploitation": False,
            }
        )
    result = {
        "scenario": "SEC-09",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "api_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "flutter_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent / "catlogue", text=True
        ).strip(),
        "current_inventory_count": len(current),
        "queries": 254,
        "query_version_drift": version_drift,
        "missing_declared_distributions": sorted(set(missing)),
        "direct_requirements": sorted(direct),
        "matched_packages": matches,
        "raw_advisory_ids": details["raw_ids_count"],
        "deduplicated_alias_groups": details["alias_group_count"],
        "withdrawn_ids": [
            ident for ident, r in details["records"].items() if r.get("withdrawn")
        ],
        "native_and_git_inventory": {
            "SAM-2_requirement": next(
                str(r) for r in roots if canonicalize_name(r.name) == "sam-2"
            ),
            "sdk_dependencies": {
                name: p for name, p in pub.items() if p["source"] == "sdk"
            },
            "native_plugins": "Resolved versions and native plugin graph are recorded by pubspec.lock and .flutter-plugins-dependencies; OSV hosted-package matching is not a native SDK binary audit.",
        },
        "source_hashes": {
            "requirements.txt": sha(ROOT / "requirements.txt"),
            "pubspec.lock": sha(lock),
            "app/config.py": sha(ROOT / "app/config.py"),
            "app/main.py": sha(ROOT / "app/main.py"),
            "lib/config/api_config.dart": sha(
                ROOT.parent / "catlogue/lib/config/api_config.dart"
            ),
        },
        "findings": [
            {
                "id": "SEC09-DEPENDENCY-VERSIONS",
                "state": "OPEN_REVIEW",
                "description": "Eight environment packages have OSV version matches. No exploit is demonstrated; dependency paths and aliases retained.",
                "recommendation": "Select upstream fixed versions per advisory and validate the actual production dependency closure before an upgrade.",
                "correction_commit": None,
            },
            {
                "id": "SEC09-DEFAULT-SECRETS",
                "state": "OPEN_DEPLOYMENT_CONFIGURATION",
                "description": "Settings retain explicit development JWT/OTP placeholders and the local app exposes docs/OpenAPI. Real deployment environment was not inspected.",
                "recommendation": "Require independent non-placeholder secrets in deployment and explicitly decide docs exposure; no production exposure inferred from the local fixture.",
                "correction_commit": None,
            },
            {
                "id": "SEC09-REPRODUCIBILITY",
                "state": "OPEN_REPRODUCIBILITY",
                "description": "API requirements primarily use lower bounds, so this shared installed environment is not a production lock.",
                "recommendation": "Create platform-specific reproducible production resolution and validate it separately rather than treating all shared-environment packages as shipped.",
            },
        ],
        "limitations": [
            "E1/E2 local review only; no production environment, reverse proxy, exploit, native SDK or release APK certification.",
            "Eight version-matched packages are not eight confirmed exploitable product defects.",
            "No code, credentials or user data were sent to OSV; only the exact previously authorized 254 package names/versions.",
            "Historical frozen-source applicability findings remain preserved; current Apple verification correction is recognized.",
        ],
    }
    assert not version_drift, (
        "Approved metadata differs from current packages; do not claim current inventory coverage"
    )
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "matched_packages": len(matches),
                "alias_groups": details["alias_group_count"],
                "version_drift": len(version_drift),
                "missing_declared_distributions": sorted(set(missing)),
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--scan", required=True)
    p.add_argument("--details", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    review(a.scan, a.details, a.output)
