"""Steady-force boundaries must not confuse a search ceiling with a limit."""

from pathlib import Path

import pytest

from data_generation.v1.gazebo_envelope import find_upper_boundary, solve_trim
from data_generation.v1.gazebo_reference import load_catalog
from data_generation.v1.gazebo_envelope_run import diagnose


def test_boundary_refines_feasible_and_infeasible_bracket():
    def probe(speed):
        return {"feasible": speed <= 42.3, "speed_mps": speed}

    result, _ = find_upper_boundary(probe, [0, 20, 40, 60, 80], 0.05)
    assert result["status"] == "bracketed_trim_boundary"
    assert result["feasible_speed_mps"] <= 42.3 < result["infeasible_speed_mps"]
    assert result["infeasible_speed_mps"] - result["feasible_speed_mps"] <= 0.05


def test_search_ceiling_is_not_reported_as_maximum():
    result, _ = find_upper_boundary(
        lambda speed: {"feasible": True, "speed_mps": speed}, [0, 10, 20], 0.1
    )
    assert result["status"] == "open_at_search_ceiling"
    assert result["infeasible_speed_mps"] is None


def test_search_does_not_stop_at_first_infeasible_point():
    result, _ = find_upper_boundary(
        lambda speed: {"feasible": 30 <= speed <= 45, "speed_mps": speed},
        [0, 10, 20, 30, 40, 50],
        0.1,
    )
    assert 44.9 < result["feasible_speed_mps"] <= 45


@pytest.mark.parametrize("model_id,mode,speed", [("rc_cessna", "wing", 20), ("x500", "rotor", 10)])
def test_trim_balances_existing_source_forces(model_id, mode, speed):
    path = Path(__file__).parents[1] / "configs/motion_reference/gazebo_trials.yaml"
    models, config = load_catalog(path)
    result = solve_trim(models[model_id], mode, speed, 0, 0, config, 1e-4)
    assert result["feasible"]
    assert result["force_residual_mps2"] <= 1e-4
    assert 0 <= result["motor_fraction"] <= 1


def test_invalid_precision_rejected():
    with pytest.raises(ValueError):
        find_upper_boundary(lambda x: {}, [0, 10], 0)


def test_no_trim_found_is_not_a_zero_speed_limit():
    result, _ = find_upper_boundary(
        lambda speed: {"feasible": False, "speed_mps": speed}, [0, 10, 20], 0.1
    )
    assert result["status"] == "no_trim_found_on_scan"
    assert result["feasible_speed_mps"] is None


def test_diagnosis_explains_speed_error_without_claiming_motor_limit():
    row = dict(mode="wing", speed_mps="30", path_angle_deg="0", status="complete",
               terminal_window_complete="True", target_met="False",
               achieved_speed_mps_min="28.5", achieved_speed_mps_max="28.7",
               achieved_path_angle_deg_min="-0.4", achieved_path_angle_deg_max="-0.3")
    result = diagnose(row, dict(speed_tolerance_mps=0.75, angle_tolerance_deg=1.5))
    assert result["unmet_reasons"] == "속력 오차"
    assert result["speed_mps_worst_error"] == pytest.approx(1.5)


def test_rotor_speed_convention_and_vertical_balance():
    path = Path(__file__).parents[1] / "configs/motion_reference/gazebo_trials.yaml"
    models, config = load_catalog(path)
    result = solve_trim(models["x500"], "rotor", 10, 2, 10, config, 1e-4)
    assert result["feasible"]
    assert result["az_mps2"] == pytest.approx(0, abs=1e-4)
    assert result["turn_deg_s"] == pytest.approx(10, abs=0.001)
    assert result["radius_m"] == pytest.approx(10 / (10 * 3.141592653589793 / 180), rel=1e-4)


def test_vtol_rotor_trim_can_tilt_backward_during_climb():
    path = Path(__file__).parents[1] / "configs/motion_reference/gazebo_trials.yaml"
    models, config = load_catalog(path)
    result = solve_trim(models["standard_vtol"], "rotor", 15, 4, 0, config, 1e-4)
    assert result["feasible"]
    assert result["force_residual_mps2"] <= 1e-4
