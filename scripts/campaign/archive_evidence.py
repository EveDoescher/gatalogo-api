"""Archive raw evidence locally, verify every member, and never remove originals."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
from datetime import UTC, datetime


def archive(run):
    run = Path(run).resolve()
    if not (run / "manifest.json").is_file() or run.parent.name != "quality-campaign":
        raise ValueError("An existing campaign manifest is required")
    folder = run.parent.parent / "quality-archives"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{run.name}-{stamp}.tar.gz"
    files = []
    for path in sorted(run.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(run)
        if relative.parts[0] == "sources" and "integration-" not in str(relative):
            continue  # product archives are reproducible from Git; include their raw integration logs
        files.append(path)
    hashes = {str(p.relative_to(run)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with tarfile.open(target, "w:gz") as output:
        for path in files:
            output.add(path, arcname=str(path.relative_to(run)), recursive=False)
    with tarfile.open(target, "r:gz") as check:
        for item in check.getmembers():
            data = check.extractfile(item).read()
            assert hashlib.sha256(data).hexdigest() == hashes[item.name]
    receipt = {"archive": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
               "created_at": datetime.now(UTC).isoformat(), "files": hashes,
               "verified_members": len(hashes), "originals_removed": False,
               "privacy": "LOCAL ONLY: may contain raw service logs; do not publish archive"}
    target.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("archive", "sha256", "verified_members", "originals_removed")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    archive(parser.parse_args().run)
