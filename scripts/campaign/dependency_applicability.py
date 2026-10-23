"""Offline prerequisite review of already authorized/cached advisory metadata."""

import argparse
from datetime import UTC, datetime
import hashlib
from importlib import metadata
import json
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def review_record(package, summary):
    """Conservative reviewed rules; no rule means exploitation or remediation."""
    text = summary.lower()
    if package == "pillow":
        if "jpegdecode" in text or "image.open()" in text or "xbmimageplugin" in text:
            return (
                "POTENTIAL_REACHABLE_PARSER",
                "Image.open on supplied bytes is called before preprocessing format validation; sync/gallery/report media also open stored bytes. Exact malformed-input prerequisites were not executed.",
                [
                    "app/services/preprocessing_service.py",
                    "app/routers/sync.py",
                    "app/services/social_feed_queries.py",
                    "app/services/report_media.py",
                ],
            )
        if any(
            s in text
            for s in [
                "pdfparser",
                "pdf page",
                "pdf save",
                "saving tiff",
                "encoding tiff",
                "tiff encoder",
                "libtiff encoder",
                "gif comment copying during save",
                "rank filters",
                "_imagingmorph",
                "image.merge",
                "image.quantize",
                "image.transform",
                "freetype",
            ]
        ):
            return (
                "REQUIRED_OPERATION_NOT_OBSERVED_IN_APP",
                "Reviewed app image writes are JPEG/PNG/WEBP; the named PDF/TIFF/GIF encoder, filter/morph/quantize/transform/font operation was not found in app call sites. This does not rule out transitive/native behavior or another deployment.",
                [
                    "app/services/avatar_service.py",
                    "app/services/preprocessing_service.py",
                    "app/services/report_media.py",
                    "app/services/social_feed_queries.py",
                ],
            )
        return (
            "ADDITIONAL_MEDIA_PATH_REVIEW_REQUIRED",
            "The preprocessing allowlist alone is insufficient to classify all routes: sync saves uploaded bytes/suffix/MIME and gallery/report open those bytes. Check the precise format/frame/decoder prerequisite from the primary advisory and native build; no payload or exploit executed.",
            [
                "app/services/preprocessing_service.py",
                "app/routers/sync.py",
                "app/services/social_feed_queries.py",
                "app/services/report_media.py",
            ],
        )
    if package == "pyjwt":
        if "recursion" in text:
            return (
                "POTENTIAL_REACHABLE_DECODE",
                "Both application authentication and Apple route invoke jwt.decode. Apple also disables signature verification. No deeply nested-token payload was executed; runtime error/availability impact is unconfirmed. The independent Apple trust failure has its own actual HTTP evidence.",
                ["app/services/auth_service.py", "app/routers/auth.py"],
            )
        if "jwks" in text or "jwk" in text:
            return (
                "NAMED_JWK_API_NOT_OBSERVED_IN_APP",
                "No direct PyJWK/PyJWKClient/JWK-set call observed in app; application sessions use a configured HS256 secret. Google verification uses a separate library. Transitive use and actual deployment still need review.",
                ["app/services/auth_service.py", "app/routers/auth.py"],
            )
        if "options-dict" in text:
            return (
                "SHARED_OPTIONS_PRECONDITION_NOT_OBSERVED",
                "Apple call creates a literal options map per call; no shared reused options dictionary was found in reviewed app decode sites. Its disabled verification remains a separate confirmed product finding.",
                ["app/routers/auth.py", "app/services/auth_service.py"],
            )
        if "revocation" in text:
            return (
                "RAW_JWT_REVOCATION_PRECONDITION_NOT_OBSERVED",
                "Access authentication checks persisted session sid/revoked_at rather than a raw-JWT denylist; refresh lookup hashes an opaque refresh credential. No signature-encoding variant was executed.",
                ["app/services/auth_service.py", "app/routers/auth.py"],
            )
        return (
            "KEY_FORMAT_PRECONDITION_REQUIRES_DEPLOYMENT_REVIEW",
            "Application access decode fixes HS256 and uses configured jwt_secret, without a user-supplied key. Verify secret provenance/format in deployment; no asymmetric-key confusion or key parser payload executed. Apple verification bypass is an independent product failure.",
            ["app/services/auth_service.py", "app/routers/auth.py", "app/config.py"],
        )
    if package == "hydra-core":
        return (
            "CONDITIONAL_MODEL_CONFIGURATION_PATH",
            "SAM2 loader calls build_sam2 with configured model config/checkpoint. No HTTP input to Hydra instantiate/logging configuration found in app. Check model dependency implementation, activation and configuration provenance before claiming absence or exposure.",
            [
                "app/services/segmentation_service.py",
                "app/config.py",
                "requirements.txt",
            ],
        )
    if package == "mako":
        return (
            "TEMPLATE_LOOKUP_INPUT_NOT_OBSERVED_IN_APP",
            "No app Mako/TemplateLookup call observed; inspect migration/tooling dependency and template URI provenance. Windows host alone does not establish attacker-controlled lookup.",
            ["requirements.txt"],
        )
    if package == "urllib3":
        return (
            "CONDITIONAL_TRANSITIVE_NETWORK_PATH",
            "Google verification constructs google.auth.transport.requests.Request. Inspect installed requests chain, HTTPS proxy configuration and reachable upstream response streaming behavior; no proxy/deflate/chunk payload or external request executed in this review.",
            ["app/routers/auth.py", "requirements.txt"],
        )
    return (
        "TRUST_STORE_DEPLOYMENT_REVIEW_REQUIRED",
        "Installed certifi bundle matches cached metadata. Determine transport trust-store selection, bundle/container provenance and affected authorities in the real deployment; no TLS interception or outbound verification performed.",
        ["requirements.txt"],
    )


def run(campaign, attempt):
    campaign = Path(campaign).resolve()
    stage = campaign / "attempts" / attempt
    assert (stage / "executor_manifest.json").is_file()
    output = stage / "dependency_static"
    output.mkdir(exist_ok=False)
    api = (
        Path(__file__).resolve().parents[2]
        if "executors" not in Path(__file__).parts
        else Path(
            json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))[
                "sources"
            ]["B-api"]["repo"]
        )
    )
    prior = (
        api / "docs/campaigns/2026-10-22-continuation/dependency_advisory_review.json"
    )
    cached = json.loads(prior.read_text(encoding="utf-8"))
    source = campaign / "sources/B/api"
    groups = []
    for aliases in cached["alias_groups"]:
        records = [
            cached["records"][key] for key in aliases if key in cached["records"]
        ]
        assert records
        record = next((r for r in records if r["id"].startswith("GHSA-")), records[0])
        packages = sorted(
            {
                canonicalize_name(a["package"]["name"])
                for r in records
                for a in r.get("affected", [])
            }
        )
        assert len(packages) == 1
        classification, rationale, paths = review_record(
            packages[0], record.get("summary", "")
        )
        assert all((source / path).is_file() for path in paths)
        groups.append(
            {
                "aliases": aliases,
                "representative": record["id"],
                "package": packages[0],
                "summary": record.get("summary"),
                "withdrawn_records": [r["id"] for r in records if r.get("withdrawn")],
                "classification": classification,
                "rationale": rationale,
                "reviewed_code": [
                    {
                        "path": path,
                        "sha256": hashlib.sha256(
                            (source / path).read_bytes()
                        ).hexdigest(),
                    }
                    for path in paths
                ],
                "primary_references": record.get("references", []),
                "confirmed_exploitation": False,
                "correction_commit": None,
            }
        )
    names = {g["package"] for g in groups}
    parents = {name: [] for name in names}
    installed = {}
    for distribution in metadata.distributions():
        parent = distribution.metadata.get("Name", "")
        normalized = canonicalize_name(parent)
        if normalized in names:
            installed[normalized] = {
                "version": distribution.version,
                "requires": distribution.requires or [],
            }
        for requirement in distribution.requires or []:
            try:
                dependency = canonicalize_name(Requirement(requirement).name)
            except ValueError:
                continue
            if dependency in names:
                parents[dependency].append(
                    {
                        "parent": parent,
                        "requirement": requirement,
                        "marker_evaluated": False,
                    }
                )
    report = {
        "scenario": "SEC-09",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "cached_metadata_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
        "groups": groups,
        "groups_reviewed": len(groups),
        "installed_metadata": installed,
        "installed_reverse_dependencies": parents,
        "network_requests": 0,
        "exploit_executions": 0,
        "state": "STATIC_PREREQUISITE_REVIEW_NOT_EXPLOITABILITY_CERTIFICATION",
        "limitations": [
            "Summaries and already cached primary references, not new full-advisory fetches",
            "Static app review does not prove absence in transitive/native code",
            "Installed reverse dependencies include optional markers/extras, recorded but not treated as active",
            "Actual deployment/release image, trust store and native SBOM not inspected",
            "No dependency or product change; confirmed Apple product failure is separately evidenced",
        ],
    }
    (output / "review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "groups_reviewed": len(groups),
                "network_requests": 0,
                "confirmed_exploitation": False,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    run(args.run, args.attempt)
