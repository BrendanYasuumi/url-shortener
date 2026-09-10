"""Unit tests for benchmark statistics."""

import pytest

from benchmark import percentile


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [
        (0.0, 1.0),
        (0.5, 2.5),
        (0.95, 3.85),
        (1.0, 4.0),
    ],
)
def test_percentile_interpolates_sorted_values(
    fraction: float,
    expected: float,
) -> None:
    """Common percentiles should use linear interpolation."""
    assert percentile([1.0, 2.0, 3.0, 4.0], fraction) == pytest.approx(expected)


def test_percentile_supports_one_value() -> None:
    """Every percentile of a one-value sample should be that value."""
    assert percentile([7.5], 0.95) == 7.5


def test_percentile_rejects_empty_values() -> None:
    """An empty sample has no percentile."""
    with pytest.raises(ValueError, match="at least one"):
        percentile([], 0.95)


@pytest.mark.parametrize("fraction", [-0.01, 1.01])
def test_percentile_rejects_out_of_range_fraction(fraction: float) -> None:
    """Percentile fractions outside zero through one should fail."""
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        percentile([1.0, 2.0], fraction)
