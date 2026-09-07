"""Behavioral tests for continuous X8 command tracking and scenario execution."""

from __future__ import annotations

import math

import numpy as np

from data_generation.v1.scenario import (
    FlightTarget,
    FlightTargetUpdate,
    ResponseLatch,
    apply_target_update,
    run_default_x8_episode,
)


def test_partial_event_update_preserves_other_targets_and_latches_signed_response() -> None:
    """An event may change bank only, and a negative-speed response must retain its sign."""
    initial = FlightTarget(airspeed_mps=18.0, bank_rad=0.0, climb_mps=0.0, course_hold=True)
    updated = apply_target_update(initial, FlightTargetUpdate(bank_rad=math.radians(-10.0)))
    latch = ResponseLatch(start_value=20.0, target_value=18.0, changed_threshold=0.1)

    latch.observe(18.5, fraction=0.7)

    assert updated.airspeed_mps == 18.0
    assert updated.climb_mps == 0.0
    assert updated.course_hold is True
    assert updated.bank_rad == pytest_approx(math.radians(-10.0))
    assert latch.changed is True
    assert latch.progress == pytest_approx(0.75)
    assert latch.latched is True


def test_default_scenario_generates_one_finite_continuous_60_second_episode() -> None:
    """The default command queue must create 301 finite samples without state teleportation."""
    episode = run_default_x8_episode(seed=17)
    steps = np.arange(301, dtype=int)
    distances = np.linalg.norm(np.diff(episode.position_enu_m, axis=0), axis=1)

    assert episode.status == "complete"
    assert np.array_equal(episode.steps, steps)
    assert np.allclose(episode.t_s, steps * 0.2)
    assert episode.position_enu_m.shape == (301, 3)
    assert episode.velocity_enu_mps.shape == (301, 3)
    assert np.all(np.isfinite(episode.position_enu_m))
    assert np.all(np.isfinite(episode.velocity_enu_mps))
    assert np.all(distances < 5.0)
    assert np.linalg.norm(episode.position_enu_m[-1] - episode.position_enu_m[0]) > 100.0
    assert [record.event_id for record in episode.event_records] == [
        "straight_100m",
        "left_turn",
        "add_climb",
        "recover_and_accelerate",
        "right_turn",
        "add_descent",
        "final_straight",
    ]
    assert all(record.completed for record in episode.event_records[:-1])
    assert episode.event_records[-1].completion_reason == "episode_end"


def test_default_left_turn_is_an_actual_negative_heading_response_not_a_position_override() -> None:
    """The left-bank event must produce a negative course change through simulated dynamics."""
    episode = run_default_x8_episode(seed=17)
    left_turn = next(record for record in episode.event_records if record.event_id == "left_turn")

    assert left_turn.completed is True
    assert left_turn.final_course_change_rad < math.radians(-20.0)
    assert left_turn.trigger_kind == "heading_change"
    assert left_turn.end_s > left_turn.start_s


def pytest_approx(value: float):
    """Keep scalar approximate assertions readable without mocking the implementation."""
    import pytest

    return pytest.approx(value, abs=1e-12)
