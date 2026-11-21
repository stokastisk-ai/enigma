"""Pure maths: histogram_quantile matches Prometheus semantics."""
import pytest

from enigma_api.series import hist_delta, histogram_quantile


def test_quantile_interpolates_inside_bucket():
    assert histogram_quantile(0.5, [1, 2, 4], [0, 10, 10, 10]) == pytest.approx(1.5)
    assert histogram_quantile(0.95, [1, 2, 4], [0, 10, 10, 10]) == pytest.approx(1.95)
    assert histogram_quantile(0.5, [1, 2], [10, 10, 10]) == pytest.approx(0.5)             # lowest bucket interpolates from 0


def test_quantile_in_inf_bucket_returns_highest_finite_bound():
    assert histogram_quantile(0.99, [1, 2], [0, 0, 5]) == 2


def test_quantile_empty_or_invalid_is_none():
    assert histogram_quantile(0.5, [1, 2], [0, 0, 0]) is None
    assert histogram_quantile(1.5, [1], [1, 1]) is None
    assert histogram_quantile(0.5, [1, 2], [1, 2]) is None


def test_hist_delta_detects_reset_and_layout_change():
    a = {"le": [1], "cum": [5, 9], "sum": 3, "count": 9}
    b = {"le": [1], "cum": [2, 3], "sum": 1, "count": 3}
    assert hist_delta(a, b) is None                                                         # went backwards: reset
    assert hist_delta(b, a) == {"le": [1], "cum": [3, 6], "sum": 2, "count": 6}
    assert hist_delta(a, {"le": [2], "cum": [9, 9], "sum": 3, "count": 9}) is None          # bucket layout changed
    assert hist_delta(None, a) is None
