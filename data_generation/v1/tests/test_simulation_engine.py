"""Behavioral checks for the interactive simulation, independent of rendering."""

import numpy as np
import pytest

from data_generation.v1.simulation_engine import SimulationEngine, simulation_catalog


def make_engine(kind="fixed_wing", *, airborne=False, count=1):
    engine = SimulationEngine(
        {
            "mode": "manual",
            "start_mode": "air" if airborne else "ground",
            "seed": 17,
            "sigma_m": 1,
            "objects": [
                {
                    "kind": kind,
                    "position_enu_m": [i * 100, 0, 100 if airborne else 0],
                    "heading_deg": 0,
                    "initial_speed_mps": 25 if kind == "fixed_wing" else 0,
                    "initial_mode": "CTOL" if kind == "fixed_wing" else "VTOL",
                }
                for i in range(count)
            ],
        }
    )
    engine.start()
    return engine


def advance(engine, seconds, keys=()):
    engine.set_keys(list(keys))
    engine.advance(round(seconds / engine.settings["dt_s"]))
    return engine.snapshot()["objects"][0]


def test_single_manual_has_no_extra_aircraft():
    e = make_engine()
    assert len(e.snapshot()["objects"]) == 1
    assert e.snapshot()["objects"][0]["position_enu_m"][2] == 0


def test_ground_acceleration_and_takeoff_gate():
    e = make_engine()
    assert advance(e, 2, ["ArrowUp"])["altitude_m"] == 0
    assert advance(e, 12, ["KeyW"])["altitude_m"] == 0
    assert advance(e, 3, ["ArrowUp"])["altitude_m"] > 0


def test_rotor_yaw_does_not_rotate_existing_velocity():
    e = make_engine("helicopter", airborne=True)
    before = advance(e, 2, ["ArrowUp"])
    after = advance(e, 1, ["KeyD"])
    assert after["heading_rad"] < before["heading_rad"]
    assert after["velocity_enu_m"][0] > 0
    assert abs(after["velocity_enu_m"][1]) < 1e-8


def test_transition_switches_controls_immediately_and_ignores_repeat():
    e = make_engine("vtol", airborne=True)
    e.set_keys(["KeyT"])
    first = e.snapshot()["objects"][0]
    assert first["control_mode"] == "CTOL"
    assert first["phase"] == "transition_to_ctol"
    advance(e, 1)
    e.set_keys(["KeyT"])
    assert e.snapshot()["objects"][0]["control_mode"] == "CTOL"
    advance(e, 25)
    e.set_keys(["KeyT"])
    assert e.snapshot()["objects"][0]["control_mode"] == "VTOL"


def test_pause_opposites_and_reproducible_observations():
    a, b = make_engine(airborne=True), make_engine(airborne=True)
    for engine in (a, b):
        advance(engine, 1, ["KeyW", "KeyS", "ArrowLeft", "ArrowRight"])
        engine.pause()
        t = engine.snapshot()["time_s"]
        engine.advance(10)
        assert engine.snapshot()["time_s"] == t
        engine.resume()
        advance(engine, 1)
    assert a.snapshot()["objects"][0]["heading_rad"] == 0
    assert a.drain_observations() == b.drain_observations()


def test_selection_holds_previous_without_teleport():
    e = make_engine("helicopter", airborne=True, count=2)
    before = advance(e, 1, ["ArrowUp"])
    e.select(e.snapshot()["objects"][1]["id"])
    after = e.snapshot()["objects"][0]
    np.testing.assert_allclose(after["position_enu_m"], before["position_enu_m"])
    assert after["pilot"] == "hold"


def test_contact_normal_landing_and_crash_only_one_object():
    e = make_engine("helicopter", airborne=True)
    obj = e.objects[0]
    obj.position[2] = 0.003
    obj.velocity[2] = -0.2
    obj.power = 0.5
    advance(e, 0.02)
    assert e.snapshot()["objects"][0]["phase"] == "landed"
    e = make_engine("helicopter", airborne=True, count=2)
    e.objects[0].position[2] = 0.01
    e.objects[0].velocity[2] = -5
    advance(e, 0.02)
    assert e.snapshot()["objects"][0]["phase"] == "crashed"
    assert e.snapshot()["objects"][1]["phase"] != "crashed"


@pytest.mark.parametrize(
    "patch",
    [
        {"sigma_m": 0},
        {"mode": "bogus"},
        {"seed": -1},
        {"objects": []},
        {"objects": [{"kind": "unknown"}]},
    ],
)
def test_invalid_inputs_rejected(patch):
    config = {"mode": "manual", "seed": 17, "sigma_m": 1, "objects": [{"kind": "helicopter"}]}
    config.update(patch)
    with pytest.raises(ValueError):
        SimulationEngine(config)


def test_catalog_has_three_aircraft_and_scenarios():
    c = simulation_catalog()
    assert {x["id"] for x in c["aircraft"]} == {"fixed_wing", "helicopter", "vtol"}
    assert all(x["scenarios"] for x in c["aircraft"])


def test_airborne_scenario_restores_prior_transition_for_takeover():
    e = make_engine("vtol", airborne=True, count=2)
    obj = e.snapshot()["objects"][1]
    assert obj["control_mode"] == "CTOL"
    e.select(obj["id"])
    assert e.snapshot()["objects"][1]["control_mode"] == "CTOL"


def test_held_transition_does_not_retrigger_after_completion():
    e = make_engine("vtol", airborne=True)
    advance(e, 30, ["KeyT"])
    e.set_keys(["KeyT"])
    assert e.snapshot()["objects"][0]["control_mode"] == "CTOL"


@pytest.mark.parametrize(
    "key,axis,sign",
    [("ArrowUp", 0, 1), ("ArrowDown", 0, -1), ("ArrowLeft", 1, 1), ("ArrowRight", 1, -1)],
)
def test_rotor_translation_directions(key, axis, sign):
    e = make_engine("helicopter", airborne=True)
    obj = advance(e, 1, [key])
    assert obj["velocity_enu_m"][axis] * sign > 0


def test_recorded_commands_replay_exactly():
    from data_generation.v1.simulation_engine import replay_events

    e = make_engine("vtol", airborne=True)
    advance(e, 2, ["ArrowUp", "KeyW"])
    advance(e, 4, ["KeyT"])
    advance(e, 2, ["ArrowLeft"])
    expected = e.drain_observations()
    replay = replay_events(e.config, e.events, e.step)
    assert replay.drain_observations() == expected


def test_replay_pause_then_stop_preserves_terminal_state():
    from data_generation.v1.simulation_engine import replay_events

    engine = make_engine()
    advance(engine, 1)
    engine.pause()
    engine.stop()
    assert replay_events(engine.config, engine.events, engine.step).status == "stopped"
