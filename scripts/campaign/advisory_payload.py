"""Prepare reviewable public-package metadata offline. Performs no network requests."""

import argparse
import hashlib
import json
from pathlib import Path
import yaml


def prepare(inventory, lock, output):
    inventory, lock, output = Path(inventory), Path(lock), Path(output)
    if output.exists():
        raise ValueError("Never overwrite approval payload")
    installed = json.loads(inventory.read_text(encoding="utf-8"))[
        "installed_python_distributions"
    ]
    queries = [
        {"package": {"name": p["name"], "ecosystem": "PyPI"}, "version": p["version"]}
        for p in installed
    ]
    packages = yaml.safe_load(lock.read_text(encoding="utf-8"))["packages"]
    queries += [
        {"package": {"name": name, "ecosystem": "Pub"}, "version": p["version"]}
        for name, p in packages.items()
        if p["source"] == "hosted"
        and p.get("description", {}).get("url", "").rstrip("/") == "https://pub.dev"
    ]
    result = {
        "state": "PREPARED_OFFLINE_NOT_SENT",
        "destination_if_authorized": "https://api.osv.dev/v1/querybatch",
        "payload": {"queries": queries},
        "details_if_authorized": "https://api.osv.dev/v1/vulns/{id}",
        "inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
        "flutter_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "privacy": "Only package ecosystem/name/version. No code, credentials, user data or file paths in the network payload.",
    }
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"queries": len(queries), "file": str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    prepare(args.inventory, args.lock, args.output)
