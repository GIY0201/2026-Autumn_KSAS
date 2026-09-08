"""TDD coverage for the constrained Stage 1 point-mass kernel."""

import math

import numpy as np
import pytest

from data_generation.v1.point_mass import (
    PointMassLimits,
    PointMassState,
    PointMassTarget,
    advance_point_mass,
)


def _limits() -> PointMassLimits:
    return PointMassLimits(
        minimum_horizontal_speed_mps=0.0,
        maximum_horizontal_speed_mps=20.0,
        maximum_vertical_speed_mps=5.0,
        maximum_track_turn_rate_rad_s=1.0,
        horizontal_acceleration_max_mps2=4.0,
        tangential_acceleration_max_mps2=2.0,
        vertical_acceleration_max_mps2=2.0,
    )


def test_constant_speed_straight_motion_integrates_enu_velocity() -> None:
    state = PointMassState(
        position_enu_m=np.array([2.0, -1.0, 3.0]),
        horizontal_speed_mps=10.0,
        track_heading_rad=0.0,
        vertical_speed_mps=0.0,
        track_turn_rate_rad_s=0.0,
    )
    target = PointMassTarget(
        horizontal_speed_mps=10.0,
        vertical_speed_mps=0.0,
        track_turn_rate_rad_s=0.0,
    )

    advanced = advance_point_mass(state, target, _limits(), dt_s=1.0)

    np.testing.assert_allclose(advanced.position_enu_m, [12.0, -1.0, 3.0])
    assert advanced.horizontal_speed_mps == 10.0
    assert advanced.track_heading_rad == 0.0


def test_constant_turn_matches_the_analytic_circle_geometry() -> None:
    speed_mps = 10.0
    turn_rate_rad_s = 0.1
    elapsed_s = 2.0
    state = PointMassState(
        position_enu_m=np.zeros(3),
        horizontal_speed_mps=speed_mps,
        track_heading_rad=0.0,
        vertical_speed_mps=0.0,
        track_turn_rate_rad_s=turn_rate_rad_s,
    )
    target = PointMassTarget(speed_mps, 0.0, turn_rate_rad_s)

    advanced = advance_point_mass(state, target, _limits(), dt_s=elapsed_s)

    radius_m = speed_mps / turn_rate_rad_s
    expected = np.array(
        [
            radius_m * math.sin(turn_rate_rad_s * elapsed_s),
            radius_m * (1.0 - math.cos(turn_rate_rad_s * elapsed_s)),
            0.0,
        ]
    )
    np.testing.assert_allclose(advanced.position_enu_m, expected, rtol=0.0, atol=1e-12)
    assert advanced.track_heading_rad == pytest.approx(turn_rate_rad_s * elapsed_s)
    assert radius_m == pytest.approx(speed_mps / abs(advanced.track_turn_rate_rad_s))


def test_left_and_right_turns_are_mirrored_about_the_initial_track() -> None:
    state = PointMassState(
        position_enu_m=np.zeros(3),
        horizontal_speed_mps=10.0,
        track_heading_rad=0.0,
        vertical_speed_mps=0.0,
        track_turn_rate_rad_s=0.0,
    )
    left = advance_point_mass(state, PointMassTarget(10.0, 0.0, 0.2), _limits(), dt_s=1.0)
    right = advance_point_mass(state, PointMassTarget(10.0, 0.0, -0.2), _limits(), dt_s=1.0)

    assert left.position_enu_m[0] == pytest.approx(right.position_enu_m[0])
    assert left.position_enu_m[1] == pytest.approx(-right.position_enu_m[1])
    assert left.track_heading_rad == pytest.approx(-right.track_heading_rad)


@pytest.mark.parametrize(
    ("state", "target", "dt_s", "error"),
    [
        (
            PointMassState(np.zeros(3), 10.0, 0.0, 0.0, 0.0),
            PointMassTarget(10.0, 0.0, 0.0),
            -0.1,
            "dt_s",
        ),
        (
            PointMassState(np.array([math.nan, 0.0, 0.0]), 10.0, 0.0, 0.0, 0.0),
            PointMassTarget(10.0, 0.0, 0.0),
            0.1,
            "finite",
        ),
        (
            PointMassState(np.zeros(3), 10.0, 0.0, 0.0, 0.0),
            PointMassTarget(21.0, 0.0, 0.0),
            0.1,
            "horizontal_speed",
        ),
    ],
)
def test_invalid_time_nonfinite_state_and_infeasible_speed_are_rejected(
    state: PointMassState,
    target: PointMassTarget,
    dt_s: float,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        advance_point_mass(state, target, _limits(), dt_s=dt_s)


def test_fixed_wing_minimum_speed_and_path_angle_are_rejected_before_motion() -> None:
    limits = PointMassLimits(
        minimum_horizontal_speed_mps=14.0,
        maximum_horizontal_speed_mps=22.0,
        maximum_vertical_speed_mps=2.0,
        maximum_track_turn_rate_rad_s=0.2,
        horizontal_acceleration_max_mps2=2.7,
        tangential_acceleration_max_mps2=1.0,
        vertical_acceleration_max_mps2=1.0,
        maximum_track_path_angle_rad=math.radians(5.0),
    )
    state = PointMassState(np.zeros(3), 14.0, 0.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="minimum"):
        advance_point_mass(state, PointMassTarget(13.0, 0.0, 0.0), limits, dt_s=0.1)
    with pytest.raises(ValueError, match="path angle"):
        advance_point_mass(state, PointMassTarget(14.0, 2.0, 0.0), limits, dt_s=0.1)


def test_fixed_wing_rejects_a_current_state_above_the_path_angle_limit() -> None:
    limits = PointMassLimits(
        minimum_horizontal_speed_mps=14.0,
        maximum_horizontal_speed_mps=22.0,
        maximum_vertical_speed_mps=1.0,
        maximum_track_turn_rate_rad_s=0.2,
        horizontal_acceleration_max_mps2=2.7,
        tangential_acceleration_max_mps2=1.0,
        vertical_acceleration_max_mps2=0.5,
        maximum_track_path_angle_rad=math.radians(1.4),
    )
    state = PointMassState(np.zeros(3), 18.0, 0.0, 0.5, 0.0)
    target = PointMassTarget(22.0, 0.5, 0.0)

    with pytest.raises(ValueError, match="state track path angle"):
        advance_point_mass(state, target, limits, dt_s=0.1)


def test_fixed_wing_rejects_a_next_state_above_the_path_angle_limit() -> None:
    limits = PointMassLimits(
        minimum_horizontal_speed_mps=14.0,
        maximum_horizontal_speed_mps=22.0,
        maximum_vertical_speed_mps=1.0,
        maximum_track_turn_rate_rad_s=0.2,
        horizontal_acceleration_max_mps2=2.7,
        tangential_acceleration_max_mps2=1.0,
        vertical_acceleration_max_mps2=0.5,
        maximum_track_path_angle_rad=math.radians(1.4),
    )
    state = PointMassState(np.zeros(3), 18.0, 0.0, 0.0, 0.0)
    target = PointMassTarget(22.0, 0.5, 0.0)

    with pytest.raises(ValueError, match="next state track path angle"):
        advance_point_mass(state, target, limits, dt_s=1.0)


def test_rejects_a_turn_that_is_only_feasible_after_slowing_down() -> None:
    limits = PointMassLimits(
        minimum_horizontal_speed_mps=14.0,
        maximum_horizontal_speed_mps=22.0,
        maximum_vertical_speed_mps=1.0,
        maximum_track_turn_rate_rad_s=0.187691710568191,
        horizontal_acceleration_max_mps2=2.62768394795467,
        tangential_acceleration_max_mps2=1.0,
        vertical_acceleration_max_mps2=0.5,
    )
    state = PointMassState(np.zeros(3), 22.0, 0.0, 0.0, 0.0)
    target = PointMassTarget(14.0, 0.0, 0.187691710568191)

    with pytest.raises(ValueError, match="current speed"):
        advance_point_mass(state, target, limits, dt_s=0.02)


def test_rejects_a_current_state_that_exceeds_the_shared_turn_budget() -> None:
    limits = PointMassLimits(
        minimum_horizontal_speed_mps=14.0,
        maximum_horizontal_speed_mps=22.0,
        maximum_vertical_speed_mps=1.0,
        maximum_track_turn_rate_rad_s=0.2,
        horizontal_acceleration_max_mps2=2.7,
        tangential_acceleration_max_mps2=1.0,
        vertical_acceleration_max_mps2=0.5,
    )
    state = PointMassState(np.zeros(3), 22.0, 0.0, 0.0, 0.18)
    target = PointMassTarget(22.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="state turn demand"):
        advance_point_mass(state, target, limits, dt_s=0.02)


def test_speed_change_and_turn_share_the_horizontal_acceleration_budget() -> None:
    state = PointMassState(np.zeros(3), 10.0, 0.0, 0.0, 0.0)
    target = PointMassTarget(12.0, 0.0, 0.2)

    advanced = advance_point_mass(state, target, _limits(), dt_s=1.0)

    tangential_acceleration = advanced.horizontal_speed_mps - state.horizontal_speed_mps
    midpoint_speed = (state.horizontal_speed_mps + advanced.horizontal_speed_mps) / 2.0
    normal_acceleration = midpoint_speed * advanced.track_turn_rate_rad_s
    assert math.hypot(tangential_acceleration, normal_acceleration) <= 4.0 + 1e-12
    assert advanced.horizontal_speed_mps > state.horizontal_speed_mps


def test_vertical_speed_transition_integrates_without_forcing_position() -> None:
    state = PointMassState(np.array([0.0, 0.0, 4.0]), 1.0, 0.0, 1.0, 0.0)
    target = PointMassTarget(1.0, 0.0, 0.0)

    advanced = advance_point_mass(state, target, _limits(), dt_s=0.5)

    assert advanced.vertical_speed_mps == pytest.approx(0.0)
    assert advanced.position_enu_m[2] == pytest.approx(4.25)


def test_substeps_converge_toward_a_fine_step_reference() -> None:
    initial = PointMassState(np.zeros(3), 8.0, 0.0, 0.0, 0.0)
    target = PointMassTarget(12.0, 1.0, 0.2)

    coarse = advance_point_mass(initial, target, _limits(), dt_s=1.0)
    half = advance_point_mass(
        advance_point_mass(initial, target, _limits(), dt_s=0.5),
        target,
        _limits(),
        dt_s=0.5,
    )
    fine = initial
    for _ in range(20):
        fine = advance_point_mass(fine, target, _limits(), dt_s=0.05)

    coarse_error = np.linalg.norm(coarse.position_enu_m - fine.position_enu_m)
    half_error = np.linalg.norm(half.position_enu_m - fine.position_enu_m)
    assert half_error < coarse_error
