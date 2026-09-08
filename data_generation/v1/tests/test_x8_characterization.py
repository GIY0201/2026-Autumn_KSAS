"""Independent geometry, frame, protocol, and reference-run regression tests."""

from __future__ import annotations

import csv
import importlib
from dataclasses import replace

import numpy as np
import pytest
from omegaconf import OmegaConf


def _api():
    return importlib.import_module("data_generation.v1.x8_characterization")


def _settings():
    return _api().TrialSettings(
        physics_dt_s=0.0025,
        record_dt_s=0.01,
        phase_durations_s={
            "baseline": 0.1,
            "entry": 0.1,
            "hold": 0.2,
            "recovery": 0.1,
            "post": 0.1,
        },
        initial_altitude_m=100.0,
        speed_bounds_mps=(14.0, 22.0),
        alpha_bounds_deg=(-5.0, 15.0),
        max_abs_beta_deg=10.0,
        max_abs_bank_deg=45.0,
        max_abs_pitch_deg=30.0,
        speed_tolerance_mps=0.5,
        bank_tolerance_deg=1.0,
        climb_tolerance_mps=0.1,
        terminal_dwell_s=0.05,
        speed_floor_mps=1e-6,
        turn_rate_floor_rad_s=1e-5,
    )


def _case():
    return _api().TrialCase("straight", "직선 순항", 18.0, 18.0, 0.0, 0.0)


def test_turn_metrics_use_horizontal_track_not_body_yaw_or_3d_speed():
    metrics = _api().motion_metrics(
        np.array([10.0, 0.0, 2.0]),
        np.array([0.0, 2.0, 0.0]),
        speed_floor_mps=1e-6,
        turn_rate_floor_rad_s=1e-5,
    )
    assert metrics["horizontal_speed_mps"] == pytest.approx(10.0)
    assert metrics["track_turn_rate_rad_s"] == pytest.approx(0.2)
    assert metrics["turn_radius_m"] == pytest.approx(50.0)
    assert metrics["lateral_acceleration_mps2"] == pytest.approx(2.0)
    assert metrics["tangential_acceleration_mps2"] == pytest.approx(0.0)
    assert metrics["flight_path_angle_deg"] == pytest.approx(11.309932474)


@pytest.mark.parametrize("velocity", [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
def test_stationary_and_straight_motion_do_not_report_zero_radius(velocity):
    metrics = _api().motion_metrics(
        np.array(velocity),
        np.zeros(3),
        speed_floor_mps=1e-6,
        turn_rate_floor_rad_s=1e-5,
    )
    assert metrics["turn_radius_m"] is None


def test_nonfinite_metrics_fail_instead_of_emitting_plausible_numbers():
    with pytest.raises(ValueError, match="finite"):
        _api().motion_metrics(
            np.array([np.nan, 0.0, 0.0]),
            np.zeros(3),
            speed_floor_mps=1e-6,
            turn_rate_floor_rad_s=1e-5,
        )


def test_body_rotation_term_is_removed_before_inertial_acceleration_report():
    state = np.zeros(19)
    state[6] = 1.0
    state[3] = 10.0
    state[12] = 0.2
    derivative = np.zeros(19)
    derivative[4] = -2.0
    _, velocity, acceleration = _api().enu_motion(state, derivative)
    np.testing.assert_allclose(velocity, [0.0, 10.0, 0.0])
    np.testing.assert_allclose(acceleration, [0.0, 0.0, 0.0], atol=1e-12)


@pytest.mark.parametrize(
    "changes",
    [
        {"physics_dt_s": 0.003},
        {"record_dt_s": 0.015},
        {"terminal_dwell_s": -1.0},
        {"speed_floor_mps": float("nan")},
        {"phase_durations_s": {"baseline": 1.0}},
    ],
)
def test_invalid_protocol_is_rejected_before_integration(changes):
    with pytest.raises(ValueError):
        _api().run_trial(_case(), replace(_settings(), **changes))


def test_bank_request_beyond_existing_controller_limit_is_not_silently_clipped():
    with pytest.raises(ValueError, match="bank"):
        _api().run_trial(replace(_case(), bank_deg=16.0), _settings())


def test_real_x8_trial_has_monotone_time_and_reproducible_continuous_motion():
    api = _api()
    case = _case()
    settings = _settings()
    first = api.run_trial(case, settings)
    second = api.run_trial(case, settings)
    assert first.status == "complete"
    assert first.samples == second.samples
    assert len(first.samples) == 61
    np.testing.assert_allclose([r["t_s"] for r in first.samples], np.arange(61) / 100)
    position = np.array([[r["x_m"], r["y_m"], r["z_m"]] for r in first.samples])
    assert np.all(np.linalg.norm(np.diff(position, axis=0), axis=1) < 0.23)
    assert set(r["phase"] for r in first.samples) == {
        "baseline",
        "entry",
        "hold",
        "recovery",
        "post",
    }
    assert all(np.isfinite(r["az_mps2"]) for r in first.samples)
    # This is an airborne reference, not a ground-takeoff episode.
    assert first.samples[0]["z_m"] == pytest.approx(100.0)


def test_terminal_target_check_rejects_a_late_redeparture():
    api = _api()
    assert api.terminal_target_time([0.0, 0.1, 0.2, 0.3], [False, True, True, True], 0.1) == 0.1
    assert api.terminal_target_time([0.0, 0.1, 0.2, 0.3], [True, True, True, False], 0.1) is None
    assert api.terminal_target_time([0.0, 0.1], [False, True], 0.1) is None


def test_batch_preserves_runs_and_excludes_reference_from_public_dataset(tmp_path):
    from dataclasses import asdict

    runner = importlib.import_module("data_generation.v1.x8_characterization_run")
    config = {
        "schema_version": "x8-characterization-v1",
        "settings": asdict(_settings()),
        "cases": [asdict(_case())],
        "provenance": {"parameter_role": "experiment_protocol", "notes": "test fixture"},
    }
    path = tmp_path / "input.yaml"
    OmegaConf.save(OmegaConf.create(config), path)
    first = runner.run_batch(path, tmp_path / "outputs")
    second = runner.run_batch(path, tmp_path / "outputs")
    assert first != second
    assert not (first / "public").exists()
    assert (first / "evaluation" / "samples.csv").read_bytes() == (
        second / "evaluation" / "samples.csv"
    ).read_bytes()
    with (first / "manifest.csv").open(encoding="utf-8", newline="") as stream:
        manifest = next(csv.DictReader(stream))
    assert manifest["data_role"] == "REFERENCE_SIMULATION"
    assert manifest["validation_status"] == "REPORT_ONLY"
    assert manifest["status"] == "complete"
    assert len(manifest["code_hash"]) == 64
    import hashlib

    assert manifest["input_config_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (first / "input_config.yaml").read_bytes() == path.read_bytes()
    with (first / "files.csv").open(encoding="utf-8", newline="") as stream:
        for entry in csv.DictReader(stream):
            actual_hash = hashlib.sha256((first / entry["path"]).read_bytes()).hexdigest()
            assert actual_hash == entry["sha256"]


def test_failed_trial_is_not_padded_to_the_requested_duration():
    result = _api().run_trial(_case(), replace(_settings(), alpha_bounds_deg=(80.0, 90.0)))
    assert result.status == "failed"
    assert result.failure_reason == "alpha_deg_outside_experiment_envelope"
    assert len(result.samples) == 1
    assert not any(r["terminal_target_met"] for r in result.summary)


def test_all_failed_batch_reports_failed_not_partial(tmp_path):
    from dataclasses import asdict

    runner = importlib.import_module("data_generation.v1.x8_characterization_run")
    config = {
        "schema_version": "x8-characterization-v1",
        "settings": asdict(replace(_settings(), alpha_bounds_deg=(80.0, 90.0))),
        "cases": [asdict(_case())],
        "provenance": {"parameter_role": "experiment_protocol"},
    }
    path = tmp_path / "input.yaml"
    OmegaConf.save(OmegaConf.create(config), path)
    output = runner.run_batch(path, tmp_path / "outputs")
    with (output / "manifest.csv").open(encoding="utf-8", newline="") as stream:
        assert next(csv.DictReader(stream))["status"] == "failed"


def test_near_multiple_clock_is_rejected_before_the_integrator():
    with pytest.raises(ValueError, match="physics_dt_s"):
        _api().validate_trial(_case(), replace(_settings(), physics_dt_s=0.00333333332))


def test_hold_terminal_dwell_includes_the_actual_boundary_state():
    settings = replace(_settings(), terminal_dwell_s=0.2)
    result = _api().run_trial(_case(), settings)
    hold = next(r for r in result.summary if r["phase"] == "hold")
    assert hold["terminal_target_met"] is True
    assert hold["terminal_target_time_s"] == pytest.approx(0.0, abs=1e-10)


def test_bad_boundary_state_invalidates_terminal_target_flag():
    result = _api().run_trial(_case(), _settings())
    rows = [dict(row) for row in result.samples]
    rows[40]["bank_deg"] = 2.0
    summary = _api()._summary(_case(), _settings(), rows)
    assert not next(r for r in summary if r["phase"] == "hold")["terminal_target_met"]


def test_terminal_verdict_uses_control_history_even_when_csv_is_decimated():
    result = _api().run_trial(_case(), _settings())
    rows = [dict(row) for row in result.samples]
    rows[39]["bank_deg"] = 2.0
    summary = _api()._summary(
        _case(), replace(_settings(), record_dt_s=0.02), rows[::2], control_rows=rows
    )
    assert not next(r for r in summary if r["phase"] == "hold")["terminal_target_met"]


def test_numerical_value_error_returns_failed_prefix(monkeypatch):
    api = _api()
    original = api.integrate_rk4
    calls = 0

    def fail_after_two_steps(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("quaternion norm must be finite and positive")
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "integrate_rk4", fail_after_two_steps)
    result = api.run_trial(_case(), _settings())
    assert result.status == "failed"
    assert "quaternion" in result.failure_reason
    assert len(result.samples) == 3
    assert result.samples[-1]["t_s"] == pytest.approx(0.02)


def test_final_command_is_explicitly_dated_to_its_actual_control_tick():
    result = _api().run_trial(_case(), _settings())
    assert all(r["command_time_s"] == r["t_s"] for r in result.samples[:-1])
    assert result.samples[-1]["command_time_s"] == pytest.approx(0.59)
    assert result.samples[-1]["t_s"] == pytest.approx(0.6)


def test_unexpected_value_error_is_not_disguised_as_a_numerical_trial_failure(monkeypatch):
    api = _api()

    def invalid_api(*args, **kwargs):
        raise ValueError("unexpected API/configuration error")

    monkeypatch.setattr(api, "integrate_rk4", invalid_api)
    with pytest.raises(ValueError, match="unexpected API"):
        api.run_trial(_case(), _settings())


def test_failed_decimated_trial_preserves_its_last_computed_control_state(monkeypatch):
    api = _api()
    original = api.integrate_rk4
    calls = 0

    def fail_between_record_ticks(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise FloatingPointError("integration failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "integrate_rk4", fail_between_record_ticks)
    result = api.run_trial(_case(), replace(_settings(), record_dt_s=0.02))
    assert result.status == "failed"
    assert [row["t_s"] for row in result.samples] == pytest.approx([0.0, 0.02, 0.03])
