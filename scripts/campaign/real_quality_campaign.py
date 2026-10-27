"""Fresh A/B sources and paired private labeled photos; never edits main or old runs."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("quality", "cache", "replay"), default="quality")
    parser.add_argument("--replay-from", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    assert output.is_relative_to(ROOT / ".tmp/quality-campaign")
    output.mkdir(exist_ok=False, parents=True)
    executors = output / "executors"
    executors.mkdir()
    shutil.copy2(__file__, executors / Path(__file__).name)
    child_name = "real_cache_child.py" if args.kind == "cache" else "real_quality_child.py"
    for name in (child_name, "paid_provider.py", "measurements.py"):
        shutil.copy2(Path(__file__).with_name(name), executors / name)
    shutil.copy2(ROOT / "scripts/run_own_cats_calibration.py", executors)
    labels = ROOT / "data/own-cats/calibration_references.json"
    refs = json.loads(labels.read_text(encoding="utf-8"))
    inputs = {f"sample-{i+1:02}": digest(ROOT / r["image_path"]) for i, r in enumerate(refs)}
    shared = output.parent.parent / "paid-provider-budget.sqlite3"
    # The approved ledger lives at the campaign root, never a fresh per-run budget.
    assert shared.exists(), shared
    sources = {}
    roles = (("before-main", "main"), ("after-cleanup", "HEAD")) if args.kind == "quality" else (("cache-process-0", "HEAD"), ("cache-process-1", "HEAD"))
    if args.kind == "replay":
        assert args.replay_from and args.replay_from.resolve().is_relative_to(ROOT / ".tmp/quality-campaign")
        assert (args.replay_from / "gemini_semantic.sqlite3").exists()
        roles = (("after-replay", "HEAD"),)
    for role, ref in roles:
        sha = subprocess.check_output(["git", "rev-parse", ref], cwd=ROOT, text=True).strip()
        if role == "before-main":
            assert sha == "eced9c3873bc45c56343ce5d46dae6c1843f2a51"
        archive, source = output / (role + ".tar"), output / (role + "-source")
        subprocess.run(["git", "archive", "--format=tar", "-o", str(archive), sha], cwd=ROOT, check=True)
        source.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        sources[role] = {"sha": sha, "path": str(source), "archive_sha256": digest(archive)}
    protected = [labels, *(ROOT / r["image_path"] for r in refs), *executors.glob("*.py")]
    protected.extend(ROOT / ".model-cache" / name for name in (
        "sam2.1_hiera_tiny.pt", "checkpoints/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth"))
    for source in sources.values():
        protected.extend(Path(source["path"]).glob("app/**/*.py"))
    if args.kind == "replay":
        protected.extend(args.replay_from.rglob("*sqlite3"))
    before = {str(p): digest(p) for p in protected}
    (output / "source_manifest.json").write_text(json.dumps({"sources": sources, "input_sha256": inputs,
        "labels_sha256": digest(labels), "protected": before}, indent=2)+"\n", encoding="utf-8")
    for role, source in sources.items():
        config = {"root": str(ROOT), "source": source["path"], "source_sha": source["sha"],
            "output": str(output / role), "ledger": str(shared), "role": ("IMG06-" if args.kind == "quality" else "IMG04-") + role}
        if args.kind == "replay":
            config.update(replay_cache=str(args.replay_from.resolve()), role="IMG06-semantic-replay")
        path = output / (role + "-config.json")
        path.write_text(json.dumps(config), encoding="utf-8")
        with (output / (role + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run([sys.executable, "-X", "utf8", str(executors / child_name), str(path)],
                cwd=source["path"], stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        assert result.returncode == 0, f"Preserve and inspect {role}.log"
        assert all(digest(Path(p)) == sha for p, sha in before.items()), "Evaluated source or labeled input changed"
    (output / "summary.json").write_text(json.dumps({"executed": True, "sources_and_inputs_unchanged": True,
        "kind": args.kind, "samples_per_version": 2 if args.kind == "cache" else len(refs),
        "paired_inputs": inputs}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
