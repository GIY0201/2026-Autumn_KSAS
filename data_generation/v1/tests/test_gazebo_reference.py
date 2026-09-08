"""Independent-force reference tests: source identities, forces, and real integration."""

import importlib
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]


def api():
    # A missing implementation must produce an explicit RED assertion.
    assert importlib.util.find_spec("data_generation.v1.gazebo_reference") is not None
    return importlib.import_module("data_generation.v1.gazebo_reference")


def test_motor_force_square_law_and_clamping():
    mod = api()
    assert mod.motor_thrust(200, 1e-5, 100) == pytest.approx(0.1)
    assert mod.motor_thrust(50, 1e-5, 100) == pytest.approx(0.025)
    assert mod.motor_thrust(0, 1e-5, 100) == 0


def surface():
    return dict(
        a0=0.1,
        cla=4.0,
        cda=0.5,
        alpha_stall=0.3,
        cla_stall=-2.0,
        cda_stall=1.0,
        area=2.0,
        air_density=1.0,
    )


def test_lift_drag_neutral_forces_and_reverse_airflow():
    mod = api()
    f = mod.lift_drag(
        np.array([10.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), surface()
    )
    np.testing.assert_allclose(f, [-5, 0, 40], atol=1e-10)
    np.testing.assert_array_equal(
        mod.lift_drag(
            np.array([-10.0, 0.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            surface(),
        ),
        np.zeros(3),
    )


def test_lift_drag_stall_branch_not_unbounded_linear_lift():
    mod = api()
    s = surface()
    s["a0"] = 0.5
    f = mod.lift_drag(
        np.array([10.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), s
    )
    np.testing.assert_allclose(f, [-35, 0, 80], atol=1e-10)


def test_rk4_integrates_force_not_velocity_target():
    mod = api()
    initial = np.array([0.0, 0.0, 10.0, 2.0, 0.0, 0.0])
    final = mod.integrate(initial, 1.0, lambda v: np.array([0.0, 0.0, -10.0]))
    np.testing.assert_allclose(final, [2, 0, 5, 2, 0, -10], atol=1e-10)
    np.testing.assert_array_equal(initial, [0, 0, 10, 2, 0, 0])


def test_offline_catalog_includes_actual_source_mass_and_models():
    mod = api()
    models, _ = mod.load_catalog(
        ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml"
    )
    assert set(models) == {"rc_cessna", "x500", "standard_vtol", "helicopter"}
    assert models["x500"].mass == pytest.approx(2.0643076923076915)
    assert models["helicopter"].mass == pytest.approx(1.339)
    assert len(models["rc_cessna"].surfaces) == 6


def test_motor_axes_keep_source_link_thrust_and_joint_drag_separate():
    mod = api()
    models, _ = mod.load_catalog(
        ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml"
    )
    cessna = models["rc_cessna"].motors[0]
    vtol = next(m for m in models["standard_vtol"].motors if m["role"] == "forward")

    for motor in (cessna, vtol):
        np.testing.assert_allclose(motor["thrust_axis_body"], [1.0, 0.0, 0.0], atol=1e-3)
        np.testing.assert_allclose(motor["drag_axis_body"], [0.0, 0.0, 1.0], atol=1e-3)
        assert motor["link"] != motor["joint"]

    reduced = mod.ReferenceModel(
        model_id="axis_test",
        kind="wing",
        mass=1.0,
        motors=(
            dict(
                cessna,
                k=2e-5,
                max_omega=200.0,
                drag=0.01,
            ),
        ),
        surfaces=(),
        blades=(),
        definition={},
    )
    actuator = dict(
        axes=np.eye(3),
        omegas=[100.0],
        rotor_omega=0.0,
        collective=0.0,
    )
    force = mod.forces(reduced, np.array([10.0, 0.0, 0.0]), actuator, quadrature=8)
    np.testing.assert_allclose(force, [-9.8, 0.0, 0.0], atol=1e-12)


def test_catalog_rejects_modified_source(tmp_path):
    mod = api()
    p = tmp_path / "source.sdf"
    p.write_text("<sdf/>", encoding="utf8")
    with pytest.raises(ValueError, match="hash"):
        mod.checked_xml(p, "0" * 64)


@pytest.mark.parametrize(
    "model_id,mode",
    [
        ("rc_cessna", "wing"),
        ("x500", "rotor"),
        ("standard_vtol", "wing"),
        ("standard_vtol", "rotor"),
        ("helicopter", "rotor"),
    ],
)
def test_each_model_produces_finite_motion_from_forces(model_id, mode):
    mod = api()
    trials = importlib.import_module("data_generation.v1.gazebo_trials")
    models, config = mod.load_catalog(
        ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml"
    )
    case = dict(
        trial_id="test",
        model_id=model_id,
        mode=mode,
        speed_mps=10.0 if mode == "wing" else 2.0,
        vertical_mps=0.0,
        path_angle_deg=0.0,
        turn_deg_s=0.0,
        bank_deg=0.0,
    )
    settings = dict(
        config["settings"],
        settle_s=1.0,
        entry_s=1.0,
        hold_s=2.0,
        recovery_s=1.0,
        terminal_window_s=1.0,
    )
    result = trials.run_trial(models[model_id], case, settings, config["controller"])
    assert result["summary"]["status"] == "complete"
    rows = result["samples"]
    assert rows[0]["t_s"] == 0 and rows[-1]["t_s"] == pytest.approx(5.0)
    assert all(np.isfinite([r["x_m"], r["vx_mps"], r["ax_mps2"]]).all() for r in rows)
    assert rows[-1]["x_m"] > rows[0]["x_m"]


def test_impossible_wing_request_is_reported_not_state_clipped():
    mod = api()
    trials = importlib.import_module("data_generation.v1.gazebo_trials")
    models, config = mod.load_catalog(
        ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml"
    )
    case = dict(
        trial_id="stress",
        model_id="rc_cessna",
        mode="wing",
        speed_mps=150.0,
        vertical_mps=0.0,
        path_angle_deg=60.0,
        turn_deg_s=0.0,
        bank_deg=0.0,
    )
    settings = dict(
        config["settings"],
        settle_s=1.0,
        entry_s=1.0,
        hold_s=2.0,
        recovery_s=1.0,
        terminal_window_s=1.0,
    )
    result = trials.run_trial(models["rc_cessna"], case, settings, config["controller"])
    assert not result["summary"]["target_met"]
    assert max(r["speed_mps"] for r in result["samples"]) < 150.0


def test_controller_error_is_captured_as_a_trial_failure(monkeypatch):
    mod = api()
    trials = importlib.import_module("data_generation.v1.gazebo_trials")
    models, config = mod.load_catalog(
        ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml"
    )
    case = dict(
        trial_id="controller_error",
        model_id="x500",
        mode="rotor",
        speed_mps=0.0,
        vertical_mps=0.0,
        path_angle_deg=0.0,
        turn_deg_s=0.0,
        bank_deg=0.0,
    )
    monkeypatch.setattr(
        trials,
        "_controller",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("controller failure")),
    )
    result = trials.run_trial(models["x500"], case, config["settings"], config["controller"])
    assert result["summary"]["status"] == "failed"
    assert result["summary"]["failure_reason"] == "controller failure"


def test_failure_keeps_the_last_finite_row_between_output_ticks():
    mod = api()
    trials = importlib.import_module("data_generation.v1.gazebo_trials")
    model = mod.ReferenceModel(
        model_id="falling",
        kind="multirotor",
        mass=1.0,
        motors=(),
        surfaces=(),
        blades=(),
        definition={"modes": ["rotor"], "initial_speed_mps": 0.0},
    )
    case = dict(
        trial_id="finite_failure",
        model_id="falling",
        mode="rotor",
        speed_mps=0.0,
        vertical_mps=0.0,
        path_angle_deg=0.0,
        turn_deg_s=0.0,
        bank_deg=0.0,
    )
    _, config = mod.load_catalog(
        ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml"
    )
    settings = dict(config["settings"], failure_speed_mps=0.1)
    result = trials.run_trial(model, case, settings, config["controller"])
    assert result["summary"]["status"] == "failed"
    assert result["samples"][-1]["t_s"] == pytest.approx(0.02)
    assert np.isfinite([result["samples"][-1]["x_m"], result["samples"][-1]["vx_mps"]]).all()


def test_batch_writes_distinct_evaluation_only_csv_runs(tmp_path):
    import csv

    from omegaconf import OmegaConf

    assert importlib.util.find_spec("data_generation.v1.gazebo_reference_run") is not None
    runner = importlib.import_module("data_generation.v1.gazebo_reference_run")
    config = OmegaConf.load(ROOT / "data_generation/v1/configs/motion_reference/gazebo_trials.yaml")
    config.source_root = str(ROOT / "data_sources/gazebo_reference_2026")
    config.models = {"x500": config.models.x500}
    config.grids.rotor = dict(speeds_mps=[2.0], vertical_speeds_mps=[0.0], turns_deg_s=[0.0])
    for key in ("settle_s", "entry_s", "hold_s", "recovery_s", "terminal_window_s"):
        config.settings[key] = 1.0
    path = tmp_path / "config.yaml"
    OmegaConf.save(config, path)
    first = runner.run_batch(path, tmp_path / "out")
    second = runner.run_batch(path, tmp_path / "out")
    assert first != second and first.exists() and second.exists()
    assert not (first / "public").exists()
    with (first / "evaluation/combinations.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["model_id"] == "x500"
    with (first / "evaluation/samples.csv").open(encoding="utf-8-sig") as stream:
        samples = list(csv.DictReader(stream))
    assert len(samples) == 41 and float(samples[-1]["t_s"]) == 4.0
