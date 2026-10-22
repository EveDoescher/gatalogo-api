"""Statistical edge cases that could otherwise create unsupported performance claims."""

import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "stats",
    Path(__file__).resolve().parents[1] / "scripts/campaign/statistics_checks.py",
)
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


def test_small_tail_has_unbounded_confidence_endpoint():
    result = stats.quantile_interval(list(range(1, 31)), 0.99)
    assert result["upper"] is None and result["upper_rank"] == 31


def test_quantile_interval_ranks_and_large_sample_tail():
    result = stats.quantile_interval(list(range(1, 1001)), 0.99)
    assert 0 < result["lower_rank"] < result["upper_rank"] <= 1000
    assert result["lower"] <= result["estimate"] <= result["upper"]


def test_block_pairing_uses_block_not_request_as_unit():
    pairs = [([1, 2, 3], [2, 4, 6]), ([10, 20, 30], [20, 40, 60])]
    result = stats.paired_block_interval(pairs)
    assert result["ratio_mean"] == result["lower"] == result["upper"] == 2
    assert result["paired_blocks"] == 2


def test_unstable_windows_do_not_pass_warmup():
    assert not stats.stationarity([[1] * 10, [2] * 10, [1] * 10], 0.1)["stable"]
    assert stats.stationarity([[1] * 10, [1] * 10, [1] * 10], 0.1)["stable"]
    with pytest.raises(ValueError):
        stats.quantile_interval([])
