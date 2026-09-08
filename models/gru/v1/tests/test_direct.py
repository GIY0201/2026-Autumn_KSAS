import csv
import importlib.util

import numpy as np
import pytest


def test_modules_exist():
    assert importlib.util.find_spec("models.gru.v1.data") is not None
    assert importlib.util.find_spec("models.gru.v1.model") is not None


def make_corpus(path):
    from contracts.v1.validation import PUBLIC_OBSERVATION_COLUMNS

    (path / "public").mkdir()
    with (path / "public/episodes.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "episode_id",
                "split",
                "duration_s",
                "sample_count",
                "dt_s",
                "identity_label",
            ],
        )
        writer.writeheader()
        for split in ["train", "validation", "test"]:
            writer.writerow(
                dict(
                    episode_id=split,
                    split=split,
                    duration_s=39.8,
                    sample_count=200,
                    dt_s=0.2,
                    identity_label="fixture",
                )
            )
    for split in ["train", "validation"]:
        directory = path / "training" / ("valid" if split == "validation" else split)
        directory.mkdir(parents=True)
        with (directory / "observations.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PUBLIC_OBSERVATION_COLUMNS)
            writer.writeheader()
            for step in range(200):
                writer.writerow(
                    dict(
                        episode_id=split,
                        variant_id="sigma_1m",
                        step=step,
                        t_s=step * 0.2,
                        x_m=step,
                        y_m=2 * step,
                        z_m=3 * step,
                        sigma_x_m=1,
                        sigma_y_m=1,
                        sigma_z_m=1,
                        valid=1,
                    )
                )


def test_preparation_seals_test_and_has_correct_targets(tmp_path):
    from models.gru.v1.data import prepare_data

    make_corpus(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    prepared = prepare_data(
        tmp_path,
        {
            "data": {"train_windows_per_episode": 64, "evaluation_stride_samples": 75},
            "training": {"seed": 17},
        },
        output,
    )
    assert len(prepared.train) == 64
    assert len(prepared.validation) == 2
    assert len(prepared.validation.source_sha256) == 64
    assert len(prepared.train.source_sha256) == 64
    sample = prepared.train[0]
    assert sample["input_features"].shape == (16, 4)
    assert sample["labels"].shape == (75, 3)
    np.testing.assert_allclose(sample["input_features"][-1], 0)
    np.testing.assert_allclose(prepared.train.targets_m[0, 0], [1, 2, 3])
    expected = np.percentile(np.tile(np.arange(1, 76) * np.sqrt(14), 64), 95)
    assert prepared.normalization["scale_m"] == pytest.approx(expected)
    assert (output / "window_index.csv").is_file()
    assert all(r["split"] != "test" or r["episode_id"] == "test" for r in prepared.window_rows)


def test_model_shape_gradients_and_reconstruction():
    import torch

    from models.gru.v1.model import GRUDirectModel

    model = GRUDirectModel({})
    features = torch.zeros(2, 16, 4)
    result = model(features, torch.ones(2, 75, 3))
    assert result["logits"].shape == (2, 75, 3)
    result["loss"].backward()
    assert all(torch.isfinite(p.grad).all() for p in model.parameters())
    positions = torch.ones(2, 16, 3) * 20
    times = torch.arange(16).repeat(2, 1) * 0.2
    result = model.predict_positions(positions, times, 100.0)
    torch.testing.assert_close(
        result["position_enu_m"], result["relative_position_m"].double() + 20
    )
    assert result["horizon_s"][-1].item() == pytest.approx(15.0)


def test_straight_line_overfit():
    import torch

    from models.gru.v1.model import GRUDirectModel

    torch.manual_seed(17)
    torch.set_num_threads(2)
    model = GRUDirectModel(
        {"hidden_size": 16, "num_layers": 1, "head_hidden_size": 32, "dropout": 0.0}
    )
    x = torch.zeros(4, 16, 4)
    x[:, :, 0] = torch.linspace(-0.3, 0, 16)
    target = torch.zeros(4, 75, 3)
    target[:, :, 0] = torch.linspace(0.02, 1.5, 75)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    initial = model(x, target)["loss"].item()
    for _ in range(100):
        optimizer.zero_grad()
        loss = model(x, target)["loss"]
        loss.backward()
        optimizer.step()
    assert model(x, target)["loss"].item() < initial * 0.02
    assert model(x)["logits"][0, -1, 0].item() > 1.3


def test_invalid_timestamps_rejected(tmp_path):
    from models.gru.v1.data import prepare_data

    make_corpus(tmp_path)
    path = tmp_path / "training/train/observations.csv"
    text = path.read_text()
    text = text.replace("sigma_1m,1,0.2,", "sigma_1m,1,0.0,")
    path.write_text(text)
    with pytest.raises(ValueError, match="timestamps"):
        prepare_data(tmp_path, {}, tmp_path / "run")


def test_window_determinism_and_nonoverlap_of_splits(tmp_path):
    from models.gru.v1.data import build_window_rows

    make_corpus(tmp_path)
    first = build_window_rows(tmp_path, {})
    assert first == build_window_rows(tmp_path, {})
    assert first != build_window_rows(tmp_path, {"training": {"seed": 18}})
    for row in first:
        assert row["end_step"] - row["start_step"] == 90
        assert row["episode_id"] == row["split"]


def test_metric_euclidean_meters():
    from models.gru.v1.model import metrics

    prediction = np.ones((2, 75, 3))
    result = metrics(prediction, np.zeros_like(prediction), 2.0)
    assert result["ade_m"] == pytest.approx(np.sqrt(12))
    assert result["fde_m"] == pytest.approx(np.sqrt(12))
    assert result["axis_rmse_z_m"] == pytest.approx(2.0)


def test_checkpoint_shape_validation(tmp_path):
    from safetensors.torch import save_file

    from models.gru.v1.model import GRUDirectModel
    from models.gru.v1.plugin import validate_checkpoint

    save_file(GRUDirectModel({}).state_dict(), tmp_path / "model.safetensors")
    assert validate_checkpoint(tmp_path)
    with pytest.raises(ValueError, match="shape"):
        validate_checkpoint(tmp_path, {"model": {"hidden_size": 64}})


@pytest.mark.parametrize("corruption", ["nonfinite", "invalid", "missing", "split_overlap"])
def test_observation_corruption_rejected(tmp_path, corruption):
    from models.gru.v1.data import prepare_data

    make_corpus(tmp_path)
    path = tmp_path / "training/train/observations.csv"
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    if corruption == "nonfinite":
        rows[5]["x_m"] = "nan"
    elif corruption == "invalid":
        rows[5]["valid"] = "0"
    elif corruption == "missing":
        rows.pop(5)
    else:
        rows[5]["episode_id"] = "test"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        prepare_data(tmp_path, {}, tmp_path / "run")


def test_prediction_rejects_nonfinite_and_bad_time():
    import torch

    from models.gru.v1.model import GRUDirectModel

    model = GRUDirectModel({})
    positions = torch.zeros(1, 16, 3)
    times = torch.arange(16).unsqueeze(0) * 0.2
    with pytest.raises(ValueError, match="timestamps"):
        model.predict_positions(positions, times * 2, 1.0)
    positions[0, 3, 0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        model.predict_positions(positions, times, 1.0)


def test_prediction_preserves_large_origin_precision():
    import torch

    from models.gru.v1.model import GRUDirectModel

    model = GRUDirectModel({})
    times = np.arange(16, dtype=np.float64)[None, :] * 0.2
    positions = np.repeat(times[..., None], 3, axis=-1)
    original = model.predict_positions(positions, times, 10.0)
    shifted = model.predict_positions(positions + 1e8, times + 1_700_000_000.0, 10.0)
    torch.testing.assert_close(
        original["relative_position_m"], shifted["relative_position_m"], atol=1e-5, rtol=1e-5
    )
    torch.testing.assert_close(
        shifted["position_enu_m"] - 1e8, original["position_enu_m"], atol=1e-7, rtol=1e-7
    )
