"""Archive sealed new evidence locally; never clean originals or publish raw tokens."""

import hashlib
import json
import zipfile

from publish_no_usb_20261027 import ROOT, SEQ, DOC, read, sha


def main():
    folders = []
    for folder in sorted(SEQ.iterdir()):
        if not folder.is_dir() or "20261027" not in folder.name:
            continue
        summary = folder / "summary.json"
        if summary.exists():
            state = read(summary)
            if isinstance(state.get("exit_code"), int) or state.get("executed") is True:
                folders.append(folder)
        elif folder.name in ("ACC-MOTION-20261027-before", "ACC-MOTION-20261027-after", "PROVIDER-PILOT-20261027"):
            folders.append(folder)
        elif (folder / "attempt-state.json").exists() and read(folder / "attempt-state.json").get("sealed"):
            folders.append(folder)
    files = sorted(p for folder in folders for p in folder.rglob("*") if p.is_file())
    ledger = SEQ.parent / "paid-provider-budget.sqlite3"
    if ledger.exists():
        files.append(ledger)
    manifest = [{"private_path": p.relative_to(ROOT).as_posix(), "sha256": sha(p), "bytes": p.stat().st_size} for p in files]
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    archive = ROOT / "artifacts/reports/quality-campaign/2026-10-27-no-usb" / ("sealed-"+digest+".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for path in files:
                zip_file.write(path, path.relative_to(ROOT).as_posix())
    assert all(sha(path) == row["sha256"] for path, row in zip(files, manifest, strict=True)), "Evidence changed during archive"
    result = {"date": "2026-10-27", "originals_preserved": True, "local_only": True,
        "contains_disposable_tokens_and_private_derived_data": True,
        "sealing_means_finished_not_passed": True,
        "archive": archive.relative_to(ROOT).as_posix(), "archive_sha256": sha(archive),
        "folders": [p.name for p in folders], "files": manifest}
    (DOC / "RAW-SEALED-20261027-manifest.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"sealed_folders": len(folders), "files": len(files), "archive_sha256": result["archive_sha256"]}))


if __name__ == "__main__":
    main()
