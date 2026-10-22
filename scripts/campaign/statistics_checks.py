"""Dependency-free uncertainty/preflight helpers, not an automatic load authorization."""

import argparse
import json
import math
from pathlib import Path
import random
import statistics


def quantile(values, p):
    ordered = sorted(values)
    if not ordered or not 0 < p < 1:
        raise ValueError("Need samples and 0<p<1")
    return ordered[math.ceil(len(ordered) * p) - 1]


def quantile_interval(values, p=0.95, confidence=0.95):
    """Conservative binomial order-statistic CI, conditional on iid continuous observations.

    Ties and serial dependence require review/block inference. Unbounded endpoints
    stay null; never present n=30 P99 as a precise latency tail estimate.
    """
    ordered = sorted(values)
    n = len(ordered)
    if not n or not 0 < p < 1 or not 0 < confidence < 1:
        raise ValueError("Invalid interval input")
    probabilities = [
        math.exp(
            math.lgamma(n + 1)
            - math.lgamma(k + 1)
            - math.lgamma(n - k + 1)
            + k * math.log(p)
            + (n - k) * math.log1p(-p)
        )
        for k in range(n + 1)
    ]
    cumulative = [0.0]
    for v in probabilities:
        cumulative.append(cumulative[-1] + v)
    cumulative[-1] = 1.0
    alpha = (1 - confidence) / 2
    lower = max(k for k in range(n + 1) if cumulative[k] <= alpha)
    upper = next(k for k in range(1, n + 2) if 1 - cumulative[k] <= alpha)
    return {
        "n": n,
        "quantile": p,
        "confidence": confidence,
        "estimate": quantile(values, p),
        "lower_rank": lower,
        "upper_rank": upper,
        "lower": ordered[lower - 1] if lower else None,
        "upper": ordered[upper - 1] if upper <= n else None,
        "assumption": "iid continuous samples; invalid as confirmatory CI with unchecked serial dependence",
    }


def paired_block_interval(pairs, p=0.95, repetitions=2000, seed=2102026):
    """Paired percentile bootstrap of block-level B/A quantile ratios.

    Resample independent blocks, never individual correlated requests. With few
    blocks, bootstrap support is sparse; precision alone does not certify validity.
    """
    if len(pairs) < 2:
        raise ValueError("Independent paired blocks required")
    ratios = [quantile(b, p) / quantile(a, p) for a, b in pairs]
    rng = random.Random(seed)
    boot = [
        statistics.mean(rng.choices(ratios, k=len(ratios))) for _ in range(repetitions)
    ]
    return {
        "paired_blocks": len(pairs),
        "ratio_mean": statistics.mean(ratios),
        "lower": quantile(boot, 0.025),
        "upper": quantile(boot, 0.975),
        "seed": seed,
        "bootstrap_repetitions": repetitions,
        "scope": "interval of mean block ratios, not pooled request quantile",
        "limitation": "independent representative blocks and stationarity must be established; small-block bootstrap is exploratory",
    }


def stationarity(windows, relative_tolerance):
    """Caller preregisters window size/tolerance. No data-driven favorable stopping."""
    if len(windows) < 3 or any(not w for w in windows):
        return {"stable": False, "reason": "Need three nonempty preregistered windows"}
    recent = [statistics.median(w) for w in windows[-3:]]
    center = statistics.median(recent)
    spread = (max(recent) - min(recent)) / center if center > 0 else math.inf
    return {
        "stable": spread <= relative_tolerance,
        "relative_median_range": spread,
        "tolerance": relative_tolerance,
        "note": "necessary warmup diagnostic, not proof of stationary CPU/IO/thermal state",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    destination = Path(args.output)
    if destination.exists():
        raise ValueError("Never overwrite evidence")
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = {
        "mode": "OFFLINE_ANALYSIS_ONLY",
        "p95_iid_conditional": quantile_interval(payload["samples"], 0.95),
        "p99_iid_conditional": quantile_interval(payload["samples"], 0.99),
        "confirmatory": False,
        "reason": "No host stability/independent block qualification supplied",
    }
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
