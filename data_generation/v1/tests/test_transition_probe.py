"""Check real point-mass transitions without modifying its existing integrator."""

import copy

import numpy as np
import pytest

from data_generation.v1.transition_probe import run_probe, smooth_fraction


def turn_case():
    return dict(
        case_id="turn", label="turn", object_id="diagnostic",
        initial_position=[0, 0, 100], initial_target=[10, 0, 0],
        padding_s=2, numeric_tolerance=1e-6, position_fd_tolerance_mps=0.05,
        limits=dict(minimum_horizontal_speed_mps=0, maximum_horizontal_speed_mps=40,
                    maximum_vertical_speed_mps=8, maximum_track_turn_rate_rad_s=0.5,
                    horizontal_acceleration_max_mps2=3,
                    tangential_acceleration_max_mps2=2,
                    vertical_acceleration_max_mps2=1),
        jerk_limit_mps3=2, tracking_tolerance_mps=0.01,
        segments=[dict(label="entry", duration_s=5, target=[10, 0, 0.1]),
                  dict(label="hold", duration_s=2, target=[10, 0, 0.1]),
                  dict(label="exit", duration_s=5, target=[10, 0, 0])],
    )


def test_blend_has_flat_first_and_second_derivatives_at_endpoints():
    assert smooth_fraction(0) == 0
    assert smooth_fraction(1) == 1
    assert smooth_fraction(0.5) == pytest.approx(0.5)
    eps = 1e-4
    assert smooth_fraction(eps) / eps**2 < 0.002
    assert (1 - smooth_fraction(1 - eps)) / eps**2 < 0.002


def test_smooth_turn_passes_and_step_turn_is_detected():
    case = turn_case()
    assert run_probe(case, 0.02)["summary"]["passed"]
    step = run_probe(case, 0.02, interpolation="step")
    assert not step["summary"]["passed"]
    assert "jerk" in step["summary"]["failed_checks"]


def test_abrupt_turn_jerk_grows_on_time_step_refinement():
    coarse = run_probe(turn_case(), 0.02, interpolation="step")["summary"]
    fine = run_probe(turn_case(), 0.01, interpolation="step")["summary"]
    assert fine["peak_jerk_mps3"] > coarse["peak_jerk_mps3"] * 1.9


def test_vertical_landing_integrates_ten_metres_without_position_reset():
    case = turn_case()
    # Two seconds of initial descent precede the ten-metre landing transition.
    case.update(initial_position=[0, 0, 12], initial_target=[0, -1, 0],
                expected_final_altitude_m=0,
                segments=[dict(label="landing", duration_s=20, target=[0, 0, 0])])
    result = run_probe(case, 0.02)
    assert result["position"][100, 2] == pytest.approx(10, abs=1e-8)
    assert result["summary"]["passed"]
    assert result["position"][-1, 2] == pytest.approx(0, abs=1e-8)
    assert np.min(result["position"][:, 2]) >= -1e-8
    assert np.max(np.abs(result["position"][:, :2])) == 0


def test_unreasonably_short_speed_change_is_rejected_by_tracking_check():
    case = copy.deepcopy(turn_case())
    case["segments"] = [dict(label="fast", duration_s=0.2, target=[20, 0, 0])]
    result = run_probe(case, 0.01)
    assert not result["summary"]["passed"]
    assert "tracking" in result["summary"]["failed_checks"]


def test_invalid_time_step_rejected():
    with pytest.raises(ValueError):
        run_probe(turn_case(), 0)


def test_turn_target_uses_interval_centre_to_avoid_first_order_heading_bias():
    coarse = run_probe(turn_case(), 0.02)
    fine = run_probe(turn_case(), 0.01)
    aligned = np.column_stack([
        np.interp(coarse["time"], fine["time"], fine["position"][:, i])
        for i in range(3)
    ])
    assert np.max(np.linalg.norm(coarse["position"] - aligned, axis=1)) < 0.001
