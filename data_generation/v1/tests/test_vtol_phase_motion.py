"""TDD coverage for the opt-in phase-aware Tal Tailsitter motion experiment."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from copy import deepcopy

import numpy as np
import pytest

from contracts.v1.validation import OUTPUT_DT_S, SAMPLE_COUNT
from data_generation.v1 import vtol_phase_motion
from data_generation.v1.bounded_motion import run_bounded_motion_episode
from data_generation.v1.bounded_motion_scenario import (
    MotionMode,
    PhaseExecutionPolicy,
    PhaseMotionScenario,
    PhaseMotionTarget,
)
from data_generation.v1.profiles import load_generation_profile
from data_generation.v1.vtol_phase_motion import PhaseMotionRuntime


def _phase_profile() -> dict[str, object]:
    profile = deepcopy(load_generation_profile("tal_tailsitter_motion_v1"))
    assert isinstance(profile, dict)
    return profile


def _experiment(profile: dict[str, object]) -> dict[str, object]:
    experiment = profile["experiment"]
    assert isinstance(experiment, dict)
    return experiment


def _phase_policy(profile: dict[str, object]) -> dict[str, object]:
    policy = _experiment(profile)["execution_policy"]
    assert isinstance(policy, dict)
    return policy


def _mode(profile: dict[str, object], name: str) -> dict[str, object]:
    modes = _experiment(profile)["modes"]
    assert isinstance(modes, dict)
    mode = modes[name]
    assert isinstance(mode, dict)
    return mode


def _event(episode, name: str):
    return next(record for record in episode.event_records if record.event_id.endswith(f"-{name}"))


def _assert_normalized_endpoint_flat_blend(blend: Callable[[float], float]) -> None:
    step = 1e-4
    values = np.asarray([blend(float(progress)) for progress in np.linspace(0.0, 1.0, 101)])

    assert blend(-0.5) == pytest.approx(0.0)
    assert blend(0.0) == pytest.approx(0.0)
    assert blend(0.5) == pytest.approx(0.5)
    assert blend(1.0) == pytest.approx(1.0)
    assert blend(1.5) == pytest.approx(1.0)
    assert np.all(np.diff(values) >= 0.0)
    assert abs((blend(step) - blend(0.0)) / step) <= 1e-4
    assert abs((blend(1.0) - blend(1.0 - step)) / step) <= 1e-4


def _two_phase_runtime() -> PhaseMotionRuntime:
    mode = MotionMode(
        name="reference_transition",
        mode_class="transition",
        duration_s=(1.0, 1.0),
        target_speed_mps=(0.0, 8.0),
        target_vertical_speed_mps=(-2.0, 2.0),
        target_turn_rate_rad_s=(-0.5, 0.5),
    )
    policy = PhaseExecutionPolicy(
        policy_id="vtol_phase_aware_v1",
        reference_blend_s=1.0,
        settling_dwell_s=0.1,
        max_extension_s=2.0,
    )
    scenario = PhaseMotionScenario(
        object_id="vtol",
        initial_position_enu_m=np.zeros(3),
        initial_speed_mps=1.0,
        initial_heading_rad=0.0,
        initial_vertical_speed_mps=-0.6,
        initial_turn_rate_rad_s=-0.15,
        hover_speed_tolerance_mps=0.2,
        target_tolerance_speed_mps=0.1,
        target_tolerance_vertical_speed_mps=0.1,
        target_tolerance_turn_rate_rad_s=0.1,
        hover_turn_rate_tolerance_rad_s=0.1,
        cruise_min_speed_mps=2.0,
        mode_index_map={"transition": 0},
        policy=policy,
        targets=(
            PhaseMotionTarget(
                mode=mode,
                reference_duration_s=1.0,
                target_speed_mps=5.0,
                target_vertical_speed_mps=1.2,
                target_turn_rate_rad_s=0.3,
            ),
            PhaseMotionTarget(
                mode=mode,
                reference_duration_s=1.0,
                target_speed_mps=2.0,
                target_vertical_speed_mps=-1.0,
                target_turn_rate_rad_s=-0.25,
            ),
        ),
    )
    return PhaseMotionRuntime(scenario)


def _reference_values(runtime: PhaseMotionRuntime, time_s: float) -> np.ndarray:
    command = runtime.command_at(time_s)
    return np.asarray(
        [
            command.target_speed_mps,
            command.target_vertical_speed_mps,
            command.target_turn_rate_rad_s,
        ]
    )


def test_phase_reference_blend_is_normalized_endpoint_flat_and_rejects_linear_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_normalized_endpoint_flat_blend(vtol_phase_motion._endpoint_flat_blend)

    monkeypatch.setattr(
        vtol_phase_motion,
        "_endpoint_flat_blend",
        lambda progress: min(max(progress, 0.0), 1.0),
    )
    with pytest.raises(AssertionError):
        _assert_normalized_endpoint_flat_blend(vtol_phase_motion._endpoint_flat_blend)


def test_phase_runtime_handoff_preserves_each_reference_and_endpoint_flat_slopes() -> None:
    runtime = _two_phase_runtime()
    step = 1e-4
    first_start = _reference_values(runtime, 0.0)
    first_near_start = _reference_values(runtime, step)
    first_near_end = _reference_values(runtime, 1.0 - step)
    first_end = _reference_values(runtime, 1.0)
    first_end_at_handoff = _reference_values(runtime, 1.1)

    runtime.observe(1.0, target_reached=True)
    runtime.observe(1.1, target_reached=True)
    second_start = _reference_values(runtime, 1.1)
    second_near_start = _reference_values(runtime, 1.1 + step)
    second_near_end = _reference_values(runtime, 2.1 - step)
    second_end = _reference_values(runtime, 2.1)

    assert runtime.event_records[0].completed is True
    assert not np.allclose(first_start, first_end)
    assert not np.allclose(second_start, second_end)
    for component, old_value, new_value in zip(
        ("speed", "vertical_speed", "turn_rate"),
        first_end,
        second_start,
        strict=True,
    ):
        assert old_value == pytest.approx(new_value, abs=1e-12), component
    np.testing.assert_allclose(first_end_at_handoff, second_start, rtol=0.0, atol=1e-12)

    for phase_start, phase_near_start, phase_near_end, phase_end in (
        (first_start, first_near_start, first_near_end, first_end),
        (second_start, second_near_start, second_near_end, second_end),
    ):
        start_slope = (phase_near_start - phase_start) / step
        end_slope = (phase_end - phase_near_end) / step
        assert np.all(np.abs(start_slope) <= 1e-4)
        assert np.all(np.abs(end_slope) <= 1e-4)


def test_vtol_profile_opted_into_the_versioned_phase_execution_policy() -> None:
    profile = _phase_profile()
    policy = _phase_policy(profile)

    assert policy["id"] == "vtol_phase_aware_v1"
    assert policy["reference_blend_s"] > 0.0
    assert policy["settling_dwell_s"] > 0.0
    assert policy["max_extension_s"] > 0.0


def test_phase_policy_rejects_an_infeasible_fixed_speed_turn_before_simulation() -> None:
    profile = _phase_profile()
    experiment = _experiment(profile)
    turn = _mode(profile, "left_turn")
    turn.update(target_speed_mps=[6.0, 6.0], target_turn_rate_deg_s=[35.0, 35.0])
    experiment["ordering_alternatives"] = [["left_turn"]]

    with pytest.raises(ValueError, match="feasible turn-rate"):
        run_bounded_motion_episode(seed=7, profile=profile)


def test_phase_does_not_advance_only_because_its_reference_duration_elapsed() -> None:
    profile = _phase_profile()
    experiment = _experiment(profile)
    policy = _phase_policy(profile)
    transition = _mode(profile, "forward_transition")
    hover = _mode(profile, "final_hover")
    transition.update(
        duration_s=[0.2, 0.2],
        target_speed_mps=[6.0, 6.0],
        target_vertical_speed_mps=[0.0, 0.0],
        target_turn_rate_deg_s=[0.0, 0.0],
    )
    hover.update(duration_s=[1.0, 1.0])
    experiment["ordering_alternatives"] = [["forward_transition", "final_hover"]]
    profile["motion"]["speed_response_s"] = 1000.0  # type: ignore[index]
    policy.update(reference_blend_s=0.1, settling_dwell_s=0.02, max_extension_s=0.2)

    episode = run_bounded_motion_episode(seed=7, profile=profile)

    assert episode.status == "failed"
    assert episode.failure_reason == "phase_target_timeout:forward_transition"
    assert [record.event_id.rsplit("-", maxsplit=1)[-1] for record in episode.event_records] == [
        "forward_transition"
    ]
    assert episode.event_records[0].completed is False
    assert episode.event_records[0].completion_reason == "phase_target_timeout"


def test_phase_timeout_and_episode_window_cutoff_are_distinct() -> None:
    profile = _phase_profile()
    experiment = _experiment(profile)
    policy = _phase_policy(profile)
    transition = _mode(profile, "forward_transition")
    transition.update(
        duration_s=[60.0, 60.0],
        target_speed_mps=[6.0, 6.0],
        target_vertical_speed_mps=[0.0, 0.0],
        target_turn_rate_deg_s=[0.0, 0.0],
    )
    experiment["ordering_alternatives"] = [["forward_transition"]]
    profile["motion"]["speed_response_s"] = 1000.0  # type: ignore[index]
    policy.update(reference_blend_s=0.1, settling_dwell_s=0.02, max_extension_s=0.2)

    episode = run_bounded_motion_episode(seed=7, profile=profile)

    assert episode.status == "complete"
    assert episode.failure_reason is None
    assert len(episode.steps) == SAMPLE_COUNT
    assert episode.t_s[0] == pytest.approx(0.0)
    assert episode.t_s[-1] == pytest.approx(60.0)
    assert episode.event_records[-1].completed is False
    assert episode.event_records[-1].completion_reason == "episode_window_cut_off"


def test_phase_aware_default_is_seeded_smooth_bounded_and_single_takeoff() -> None:
    profile = _phase_profile()
    policy = _phase_policy(profile)
    first = run_bounded_motion_episode(seed=42, profile=profile)
    repeated = run_bounded_motion_episode(seed=42, profile=profile)
    different = run_bounded_motion_episode(seed=43, profile=profile)
    names = [record.event_id.rsplit("-", maxsplit=1)[-1] for record in first.event_records]

    assert first.status == "complete"
    assert len(first.steps) == SAMPLE_COUNT
    assert first.t_s[0] == pytest.approx(0.0)
    assert first.t_s[-1] == pytest.approx(60.0)
    assert np.array_equal(first.position_enu_m, repeated.position_enu_m)
    assert np.array_equal(first.velocity_enu_mps, repeated.velocity_enu_mps)
    assert not np.array_equal(first.position_enu_m, different.position_enu_m)
    assert names.count("climb") == 1
    assert names.count("forward_transition") == 1
    assert names.count("back_transition") == 1
    assert names.count("final_hover") == 1
    assert sum(name in {"left_turn", "right_turn"} for name in names) == 1
    assert names[-1] == "final_hover"
    assert all(record.completed for record in first.event_records)
    assert np.all(np.isfinite(first.position_enu_m))
    assert np.all(np.isfinite(first.velocity_enu_mps))

    horizontal_acceleration = np.linalg.norm(
        np.diff(first.velocity_enu_mps[:, :2], axis=0) / OUTPUT_DT_S, axis=1
    )
    vertical_acceleration = np.abs(np.diff(first.velocity_enu_mps[:, 2]) / OUTPUT_DT_S)
    assert np.max(horizontal_acceleration) <= profile["motion"]["horizontal_accel_max_mps2"] + 1e-9
    assert np.max(vertical_acceleration) <= profile["motion"]["vertical_accel_max_mps2"] + 1e-9

    blend_s = float(policy["reference_blend_s"])
    turn_name = "left_turn" if "left_turn" in names else "right_turn"
    for event_name, diagnostic_name in (
        ("climb", "target_vertical_speed_mps"),
        ("forward_transition", "target_speed_mps"),
        (turn_name, "target_turn_rate_deg_s"),
    ):
        event = _event(first, event_name)
        blend_samples = first.diagnostic_arrays[diagnostic_name][
            (first.t_s > event.start_s + 1e-12)
            & (first.t_s < event.start_s + blend_s - 1e-12)
        ]
        assert len(blend_samples) >= 3
        assert np.ptp(blend_samples) > 1e-3
        assert np.max(np.abs(np.diff(blend_samples))) < np.ptp(blend_samples)

    experiment = _experiment(profile)
    hover_index = experiment["mode_index_map"]["hover"]  # type: ignore[index]
    hover_samples = first.diagnostic_arrays["mode_index"] == hover_index
    assert np.any(hover_samples)
    horizontal_speed = np.linalg.norm(first.velocity_enu_mps[:, :2], axis=1)
    assert np.all(
        np.hypot(horizontal_speed[hover_samples], first.velocity_enu_mps[hover_samples, 2])
        <= experiment["hover_speed_tolerance_mps"] + 1e-12
    )
    assert np.all(
        np.abs(first.diagnostic_arrays["actual_turn_rate_deg_s"][hover_samples])
        <= experiment["target_tolerance_turn_rate_deg_s"] + 1e-12
    )


def test_phase_policy_handles_an_explicit_turn_reversal_without_acceleration_escape() -> None:
    profile = _phase_profile()
    experiment = _experiment(profile)
    policy = _phase_policy(profile)
    experiment.update(
        initial_speed_mps=6.0,
        initial_vertical_speed_mps=0.0,
        initial_turn_rate_deg_s=0.0,
        ordering_alternatives=[["left_turn", "right_turn", "final_hover"]],
    )
    for name, turn_rate in (("left_turn", 10.0), ("right_turn", -10.0)):
        _mode(profile, name).update(
            duration_s=[2.0, 2.0],
            target_speed_mps=[6.0, 6.0],
            target_vertical_speed_mps=[0.0, 0.0],
            target_turn_rate_deg_s=[turn_rate, turn_rate],
        )
    _mode(profile, "final_hover").update(duration_s=[5.0, 5.0])
    policy.update(reference_blend_s=0.4, settling_dwell_s=0.1, max_extension_s=8.0)

    episode = run_bounded_motion_episode(seed=7, profile=profile)
    horizontal_acceleration = np.linalg.norm(
        np.diff(episode.velocity_enu_mps[:, :2], axis=0) / OUTPUT_DT_S, axis=1
    )

    assert episode.status == "complete"
    assert np.max(episode.diagnostic_arrays["actual_turn_rate_deg_s"]) > 1.0
    assert np.min(episode.diagnostic_arrays["actual_turn_rate_deg_s"]) < -1.0
    assert np.max(horizontal_acceleration) <= profile["motion"]["horizontal_accel_max_mps2"] + 1e-9


def test_helicopter_legacy_episode_hash_remains_unchanged() -> None:
    episode = run_bounded_motion_episode(
        seed=17, profile=load_generation_profile("uh60_motion_v1")
    )

    assert episode.status == "complete"
    assert hashlib.sha256(episode.position_enu_m.tobytes()).hexdigest() == (
        "9d874b458892e1c6a7a70ed04750c04ac101341b80bdffea52431efa0d04f282"
    )
    assert hashlib.sha256(episode.velocity_enu_mps.tobytes()).hexdigest() == (
        "468b281640ec27bbdfc6101be1393dbf8e41caf9056de777e747a58f42494c1c"
    )
