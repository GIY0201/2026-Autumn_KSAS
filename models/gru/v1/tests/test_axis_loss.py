import pytest
import torch

from models.gru.v1.model import GRUDirectModel


@pytest.mark.parametrize("weights", [[1, 1, 1], [1, 1, 5]])
def test_weighted_loss_and_gradient(weights):
    torch.manual_seed(17)
    model = GRUDirectModel({"model": {"axis_loss_weights": weights}})
    result = model(torch.zeros(2, 16, 4), torch.full((2, 75, 3), 0.3))
    logits = result["logits"]
    target = torch.full_like(logits, 0.3)
    raw = torch.nn.functional.huber_loss(logits, target, reduction="none")
    expected = (raw * torch.tensor(weights)).sum(-1).mean() / sum(weights)
    torch.testing.assert_close(result["loss"], expected)
    actual_grad = torch.autograd.grad(result["loss"], logits, retain_graph=True)[0]
    expected_grad = torch.autograd.grad(expected, logits)[0]
    torch.testing.assert_close(actual_grad, expected_grad)
    assert torch.isfinite(actual_grad).all()
    if weights == [1, 1, 1]:
        torch.testing.assert_close(result["loss"], raw.mean())


@pytest.mark.parametrize("weights", [[1, 1], [1, 1, 0], [1, 1, -1], [1, 1, float("nan")]])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError, match="axis_loss_weights"):
        GRUDirectModel({"model": {"axis_loss_weights": weights}})


def test_weights_do_not_change_initial_parameters_or_checkpoint_keys():
    torch.manual_seed(17)
    control = GRUDirectModel({})
    torch.manual_seed(17)
    weighted = GRUDirectModel({"model": {"axis_loss_weights": [1, 1, 5]}})
    assert control.state_dict().keys() == weighted.state_dict().keys()
    for key in control.state_dict():
        torch.testing.assert_close(control.state_dict()[key], weighted.state_dict()[key])


def test_preset_rejects_invalid_axis_weights(tmp_path):
    from models.runtime.v1.service import TrainingService

    service = TrainingService(tmp_path)
    config = service.plugins()[0]["defaults"]
    config["model"]["axis_loss_weights"] = [1, 1, -5]
    with pytest.raises(ValueError, match="axis_loss_weights"):
        service.save_preset("invalid", config)


def test_ui_parses_axis_weights_as_json_array():
    from visualization.v1.training_ui import merge_parameters

    config = {"model": {"axis_loss_weights": [1, 1, 1]}}
    parsed = merge_parameters(config, [{"path": "model.axis_loss_weights"}], ["[1, 1, 5]"])
    assert parsed["model"]["axis_loss_weights"] == [1, 1, 5]
