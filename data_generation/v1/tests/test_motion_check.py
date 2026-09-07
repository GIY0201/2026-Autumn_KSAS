"""Tests for source-replay residual summaries."""

from __future__ import annotations

import math

import numpy as np

from data_generation.v1.motion_check import summarize_residual, wrapped_angle_residual


def test_wrapped_euler_residual_uses_the_short_angle_across_pi_boundary() -> None:
    """A 0.02 rad heading mismatch around ±pi must not be reported as a full-turn error."""
    model = np.array([-math.pi + 0.01])
    source = np.array([math.pi - 0.01])

    residual = wrapped_angle_residual(model, source)
    summary = summarize_residual(residual)

    assert residual[0] == pytest_approx(0.02)
    assert summary.rmse == pytest_approx(0.02)
    assert summary.bias == pytest_approx(0.02)
    assert summary.max_abs == pytest_approx(0.02)


def test_metric_summary_distinguishes_full_clip_and_first_second_windows() -> None:
    """Source replay reporting must retain both full and post-initialization error windows."""
    residual = np.array([[1.0, -1.0], [3.0, -3.0], [5.0, -5.0]])

    summary = summarize_residual(residual)

    assert summary.rmse == pytest_approx(math.sqrt((2 + 18 + 50) / 6))
    assert summary.bias == pytest_approx(0.0)
    assert summary.max_abs == pytest_approx(5.0)


def pytest_approx(value: float):
    """Keep scalar comparisons readable without replacing computation with a mock."""
    import pytest

    return pytest.approx(value, abs=1e-12)
