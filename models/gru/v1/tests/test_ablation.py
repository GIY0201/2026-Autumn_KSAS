import numpy as np
import pytest


def test_phase_summary_uses_fixed_net_altitude_threshold():
    from models.gru.v1.ablation import phase_summary

    target = np.zeros((3, 75, 3))
    target[0, :, 2] = np.linspace(1, 60, 75)
    target[2, :, 2] = -np.linspace(1, 60, 75)
    rows = phase_summary(target.copy(), target)
    assert [r["window_count"] for r in rows] == [1, 1, 1]
    assert all(r["z_rmse_m"] == 0 for r in rows)
    assert rows[0]["predicted_dz_m"] == 60
    assert rows[2]["predicted_dz_m"] == -60


def test_phase_summary_handles_empty_group():
    from models.gru.v1.ablation import phase_summary

    rows = phase_summary(np.zeros((1, 75, 3)), np.zeros((1, 75, 3)))
    assert rows[0]["window_count"] == 0
    assert rows[0]["z_rmse_m"] is None


def test_comparison_rejects_changed_validation_source():
    from models.gru.v1.ablation import validate_sources
    expected = {"train": "a", "validation": "b", "test": "sealed"}
    validate_sources(expected, {"train": "a", "validation": "b"})
    with pytest.raises(ValueError, match="validation"):
        validate_sources(expected, {"train": "a", "validation": "changed"})
