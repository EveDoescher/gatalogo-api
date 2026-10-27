"""Record local motion/semantics coverage; do not certify unexecuted device variants."""

import json
import subprocess

from critical_report import flutter_tests
from publish_no_usb_20261027 import ROOT, SEQ, DOC, read, sha


def main():
    client = ROOT.parent / "catlogue"
    before = SEQ / "ACC-MOTION-20261027-before/flutter.jsonl"
    after = SEQ / "ACC-MOTION-20261027-after/flutter.jsonl"
    tests = flutter_tests(after)
    actual = [t for t in tests if not t["name"].startswith("loading ")]
    assert len(actual) == 20 and all(t["result"] == "success" and not t["skipped"] for t in actual)
    report_name = "ACC-MOTION-20261027-results.json"
    report = {
        "scenarios": ["ACC-05", "ACC-06"], "state": "PARTIAL_EXECUTED", "date": "2026-10-27",
        "client_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=client, text=True).strip(),
        "executor": "test/campaign_motion_semantics_test.dart", "tests": tests,
        "code_sha256": {p: sha(client / p) for p in ("test/campaign_motion_semantics_test.dart",
            "lib/widgets/quintal_game_action.dart", "lib/widgets/quintal_hold_control.dart", "lib/widgets/milestone_widgets.dart")},
        "cycle": {
            "hypothesis": "Accessible labels must occur once, both input paths must activate once, reduced motion preserves feedback and controls.",
            "observed_before": "Coletar\\nColetar in yard action semantic node, both motion modes. Initial test also disposed SemanticsHandle too late; this harness issue was corrected with try/finally.",
            "correction": "Parent Semantics owns the same onTap; exclude descendant semantic duplication. Painting, sizes, labels displayed and InkWell remain unchanged.",
            "fix_commit": "9936e80fe07a0675bf9ec50afbada750cd19ba80",
            "revalidation": "Eight new widget cases plus ten header regressions and two frame-independent motion tests passed. Enabled semantics/pointer actions invoke once, disabled has no semantic tap; duration 90/1100ms normally and zero with preference; celebration content preserved.",
        },
        "evidence": [{"private_path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)} for p in (before, after)],
        "historical_device_evidence": "ANDROID-USB-20261025-results.json",
        "limitations": ["No current device execution. Previous human TalkBack confirmations remain preserved, not repeated.",
            "Local controls and celebration covered; every route transition and live scene effect are not fully certified.",
            "New semantic correction still lacks device reader revalidation. Full ACC scenarios remain partial; no unsupported closure."],
    }
    (DOC / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    matrix = read(DOC / "matrix.json")
    for scenario in report["scenarios"]:
        row = next(r for r in matrix if r["scenario"] == scenario)
        row.setdefault("closure_evidence", [])
        if report_name not in row["closure_evidence"]:
            row["closure_evidence"].append(report_name)
        row["limitation"] = "Historical Android/TalkBack evidence preserved. New local control/motion cases passed and duplicated yard label fixed; remaining full scene/route variants and device revalidation of this new correction are not certified. See ACC-MOTION-20261027-results.json."
    (DOC / "matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
