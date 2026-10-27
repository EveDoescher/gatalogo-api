"""Publish completed real-model variants with explicit ground-truth boundaries."""

from collections import Counter
import json
import re

import numpy as np

from publish_no_usb_20261027 import ROOT, SEQ, DOC, read, sha, junit


def inputs(folder):
    return [{"private_path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)}
        for p in sorted(folder.rglob("*")) if p.is_file()]


def stripped(row):
    result = dict(row["response"])
    for field in ("analyzed_at", "warnings"):
        result.pop(field, None)
    return result


def attribute_summary(samples):
    accepted = [r for r in samples if r["outcome"] == "accepted"]
    return {"total": len(samples), "outcomes": dict(Counter(r["outcome"] for r in samples)),
        "rejection_reasons": dict(Counter(r["reason"] for r in samples if r["outcome"] == "rejected")),
        "conditional_accepted_denominator": len(accepted),
        "matches": {key: sum(r["comparison"][key] for r in accepted)
            for key in ("primary_match", "colors_match", "pattern_match", "overall_match")}}


def species_summary(rows):
    tp = sum(r["expected_cat"] and r["detected_cat"] for r in rows)
    fp = sum(not r["expected_cat"] and r["detected_cat"] for r in rows)
    fn = sum(r["expected_cat"] and not r["detected_cat"] for r in rows)
    tn = sum(not r["expected_cat"] and not r["detected_cat"] for r in rows)
    negatives = any(not r["expected_cat"] for r in rows)
    precision = tp/(tp+fp) if negatives and tp+fp else None
    recall = tp/(tp+fn) if tp+fn else None
    cat_rows = [r for r in rows if r["expected_cat"]]
    return {"true_positive": tp, "false_positive": fp if negatives else None, "false_negative": fn, "true_negative": tn if negatives else None,
        "precision": precision, "recall": recall, "f1": 2*tp/(2*tp+fp+fn) if negatives and 2*tp+fp+fn else None,
        "negative_controls_present": negatives,
        "foreground_mean_image_iou": float(np.mean([r["foreground_iou"] for r in cat_rows])),
        "foreground_pooled_iou": sum(r["intersection_pixels"] for r in cat_rows)/sum(r["union_pixels"] for r in cat_rows),
        "quality_gate_unusable": sum(not r["quality_usable"] for r in rows)}


def real_quality():
    pair, replay, masks = (SEQ / name for name in (
        "IMG06-20261027-real-paired", "IMG06-20261027-replay", "IMG06-20261027-masks-02"))
    if not all((p / "summary.json").exists() for p in (pair, replay, masks)):
        return None
    assert all(read(p / "summary.json")["sources_and_inputs_unchanged"] for p in (pair, replay, masks))
    before, after = (read(pair / role / "samples.json") for role in ("before-main", "after-cleanup"))
    replay_rows = read(replay / "after-replay/samples.json")
    assert len(before) == len(after) == len(replay_rows) == 20
    assert not any(r["outcome"] == "error" for r in before+after+replay_rows)
    for old, current in zip(before, replay_rows, strict=True):
        assert old["sample_id"] == current["sample_id"] and old["input_sha256"] == current["input_sha256"]
        assert old["outcome"] == current["outcome"]
        assert stripped(old) == stripped(current) if old["outcome"] == "accepted" else old["reason"] == current["reason"]
    manifests = [read(pair / role / "manifest.json") for role in ("before-main", "after-cleanup")]
    assert manifests[0]["settings"] == manifests[1]["settings"]
    assert manifests[0]["prompt_sha256"] == manifests[1]["prompt_sha256"]
    assert manifests[0]["versions"] == manifests[1]["versions"]
    assert not read(replay / "after-replay/manifest.json")["usage"]
    discrepancies = []
    mask_pairs = []
    for old, current in zip(before, after, strict=True):
        assert old["input_sha256"] == current["input_sha256"]
        row = {"sample_id": old["sample_id"], "before_outcome": old["outcome"], "after_outcome": current["outcome"]}
        if old["outcome"] == current["outcome"] == "accepted":
            row.update(before_comparison=old["comparison"], after_comparison=current["comparison"],
                before_attributes={k: old["response"][k] for k in ("primary_color", "coat_type", "colors")},
                after_attributes={k: current["response"][k] for k in ("primary_color", "coat_type", "colors")},
                all_response_fields_equal=stripped(old) == stripped(current))
            if not old["comparison"]["overall_match"] or not current["comparison"]["overall_match"] or not row["all_response_fields_equal"]:
                discrepancies.append(row)
        else:
            discrepancies.append(row)
        old_path = pair / "before-main/masks" / (old["sample_id"]+".npz")
        new_path = pair / "after-cleanup/masks" / (old["sample_id"]+".npz")
        if old_path.exists() and new_path.exists():
            with np.load(old_path) as first, np.load(new_path) as second:
                a, b = first["mask"] > 0, second["mask"] > 0
                if a.shape == b.shape:
                    union = int(np.count_nonzero(a | b))
                    mask_pairs.append({"sample_id": old["sample_id"], "between_version_mask_iou":
                        int(np.count_nonzero(a & b))/union if union else None})
    ground_truth = {role: read(masks / role / "observations.json") for role in ("before-main", "after-cleanup")}
    assert all(len(rows) == 12 for rows in ground_truth.values())
    return {"scenario": "IMG-06", "state": "COMPLETED", "date": "2026-10-27",
        "source": read(pair / "source_manifest.json")["sources"], "manifests": manifests,
        "attribute_results": {"before": attribute_summary(before), "after": attribute_summary(after)},
        "discrepancies_for_review": discrepancies, "between_version_masks": mask_pairs,
        "offline_replay": {"cases": 20, "same_outcome_and_normalized_payload": True,
            "normalization_excludes": ["analyzed_at", "warnings"], "paid_calls": 0,
            "source": read(replay / "source_manifest.json")["sources"]},
        "ground_truth_detection_and_segmentation": {"dataset": read(masks / "before-main/dataset.json"),
            "source": read(masks / "source_manifest.json")["sources"],
            "summaries": {role: species_summary(rows) for role, rows in ground_truth.items()}, "observations": ground_truth},
        "executor": "scripts/campaign/real_quality_campaign.py --kind quality|replay|segmentation --output <new private run>",
        "evidence": [e for folder in (pair, replay, masks) for e in inputs(folder)],
        "limitations": ["Twenty authorial photos with pre-existing human attribute labels; correlated convenience sample. No defensible population confidence interval or tail percentile claim.",
            "Attribute matches are conditional on acceptance; rejection coverage is reported separately. Acceptance ground truth and individual identity labels are absent.",
            "Oxford contributes species and foreground trimaps, never inferred coat or identity labels. Boundary label 3 excluded. Twelve fixed positive images are diagnostic; local subset contains no dogs, so precision/FPR/F1 are not estimable here. First fixture attempt expected unavailable full-corpus filenames and failed before inference; preserved separately.",
            "Between-version mask IoU measures consistency, not ground-truth quality; the separate Oxford section measures annotated foreground IoU.",
            "Real Gemini draws may differ despite equal model/prompt/configuration. Offline same-response replay separates code equivalence; no provider quality improvement claim.",
            "Replay equality excludes generated timestamps and warning lists: enabling semantic cache adds the existing cache warning. It does not certify identical entire JSON/UI across different cache states.",
            "Shared PC and concurrent disposable soak; elapsed times cannot establish branch latency gains. No physical device or reidentification certification."],
        "recommendations": ["Review the listed label disagreements before proposing prompt/model changes; do not auto-tune thresholds or convert suggestions into confirmed identity.",
            "A larger independently labeled set grouped by individual is needed for generalization and reidentification metrics."],
        "issues": [{"id": "IMG06-ATTR-01", "state": "IDENTIFIED_NOT_CHANGED",
            "description": "Several accepted photos disagree with existing human color/coat labels in both versions.",
            "cause_hypothesis": "Semantic/model/fusion disagreement; stochastic responses demonstrably vary. Equal replay outcomes prevent attributing the observed aggregate difference to the refactor alone.",
            "recommended_correction": "Review labels and masks independently, then validate any prompt/model/calibration proposal on a held-out set; preserve current thresholds meanwhile.",
            "correction_commit": None},
            {"id": "IMG06-DETECT-01", "state": "IDENTIFIED_NOT_CHANGED",
            "missed_positive_images": {role: [r["image"] for r in rows if r["expected_cat"] and not r["detected_cat"]] for role, rows in ground_truth.items()},
            "cause_hypothesis": "The local pipeline returned no cat for these labeled positives. Low cat scores, pose, exposure and domain coverage need separate diagnosis; no specific training-data cause is established.",
            "recommended_correction": "Validate positive/negative cases before any threshold or model change; do not fit a correction to one image.", "correction_commit": None}],
    }


def cache():
    folder = SEQ / "IMG04-20261027-cache"
    if not (folder / "summary.json").exists():
        return None
    assert read(folder / "summary.json")["sources_and_inputs_unchanged"]
    trials = {}
    for role in ("cache-process-0", "cache-process-1"):
        summary = read(folder / role / "summary.json")
        assert summary["passed"] and summary["variants"] == 16 and summary["seed_runs"] == 2
        trials[role] = {"summary": summary, "observations": read(folder / role / "observations.json")}
    return {"scenario": "IMG-04", "state": "COMPLETED", "date": "2026-10-27", "trials": trials,
        "source": read(folder / "source_manifest.json")["sources"], "evidence": inputs(folder),
        "executor": "scripts/campaign/real_quality_campaign.py --kind cache --output <new private run>",
        "scope": "Two fresh processes; two fixed photos each; segmentation/semantic cache empty/full x provider key absent/present. Four real seed runs plus 32 variants. Exact cached final-result equality; cache hit/call counts and MissingApiKey branch verified.",
        "limitations": ["Local detector/SAM weights already cached on disk; process cold does not mean OS disk cache cold.",
            "Small controlled experiment, shared PC; no population P99, confidence interval or speedup claim. Real uncached provider outputs can vary."]}


def reference():
    folder = SEQ / "LOAD04-20261027-real"
    if not (folder / "summary.json").exists():
        return None
    status = read(folder / "summary.json")
    assert status["exit_code"] == 0 and status["evaluated_sources_unchanged"]
    tests = junit(folder / "junit.xml")
    assert tests["tests"] == 2
    rows = [read(folder / "observations" / f"LOAD04-real-{n}.json") for n in (1, 2)]
    assert all(r["completed_once"] == r["paid_generation_attempts"] == 2 for r in rows)
    return {"scenario": "LOAD-04", "state": "COMPLETED", "date": "2026-10-27", "source": read(folder / "candidate_manifest.json"),
        "tests": tests, "real_reference_batches": rows, "evidence": inputs(folder),
        "controlled_batch_evidence": "RSC06-20261027-results.json",
        "retry_fencing_superseded_evidence": "matrix.json: DATA-06, RES-04 and queue-baseline historical runs",
        "executor": "scripts/run_security_regression.py --scenario LOAD-04-REAL --output <new private run>",
        "scope": "Controlled 1/10/50 x 1/2 consumers revalidated under two resource settings (244 jobs), plus separate real detector/SAM/Gemini reference batches of two jobs each with 1/2 consumers. Real migrated DB and business result persistence, attempts exactly one, separate DINO jobs remain pending.",
        "limitations": ["Four real jobs use one repeated CC0 photo; consistency and queue mechanics, not model accuracy or production throughput. Shared CPU; no physical device."]}


def main():
    matrix = read(DOC / "matrix.json")
    for builder in (real_quality, cache, reference):
        report = builder()
        if report is None:
            continue
        name = report["scenario"].replace("-", "") + "-20261027-results.json"
        report["report_executor"] = "scripts/campaign/publish_real_models.py"
        (DOC / name).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        row = next(r for r in matrix if r["scenario"] == report["scenario"])
        row.update(state="COMPLETED", executor=report["executor"], limitation="Planned controlled and real local/provider variants executed; sample, ground-truth and shared-host limits remain explicit in " + name)
        row.setdefault("closure_evidence", [])
        if name not in row["closure_evidence"]:
            row["closure_evidence"].append(name)
    (DOC / "matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# Matriz sequencial", "", "| Cenário | Estado |", "| --- | --- |"]
    lines.extend(f"| {r['scenario']} | {r['state']} |" for r in matrix)
    (DOC / "matrix.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    counts = Counter(r["state"] for r in matrix)
    line = f"**{counts['COMPLETED']} concluídos, {counts['PARTIAL_EXECUTED']} parciais, {counts['PENDING']} pendentes e {counts['BLOCKED_EXTERNAL_PREREQUISITES']} bloqueados externos.**"
    readme = DOC / "README.md"
    readme.write_text(re.sub(r"\*\*\d+ concluídos, \d+ parciais, \d+ pendentes e \d+ bloqueados externos\.\*\*", line, readme.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    print(json.dumps(dict(counts)))


if __name__ == "__main__":
    main()
