"""Versioned project-internal GRU plugin descriptor."""

from pathlib import Path

from omegaconf import OmegaConf

from .data import load_split, prepare_data
from .model import GRUDirectModel, metrics


def validate_checkpoint(path, config=None):
    path = Path(path)
    if not path.is_dir() or not (path / "model.safetensors").is_file():
        raise ValueError("GRU checkpoint requires model.safetensors")
    from safetensors import safe_open

    with safe_open(path / "model.safetensors", framework="pt", device="cpu") as handle:
        expected = GRUDirectModel(config or {}).state_dict()
        if set(handle.keys()) != set(expected):
            raise ValueError("checkpoint model keys do not match GRU plugin")
        for key, tensor in expected.items():
            if tuple(handle.get_slice(key).get_shape()) != tuple(tensor.shape):
                raise ValueError(f"checkpoint shape mismatch: {key}")
    return True


def get_plugin():
    defaults = OmegaConf.to_container(
        OmegaConf.load(Path(__file__).parent / "configs/default.yaml"), resolve=True
    )
    schema = {}
    schema["model.axis_loss_weights"] = dict(
        type="array",
        length=3,
        exclusiveMinimum=0,
        default=[1.0, 1.0, 1.0],
        description="XYZ Huber loss weights",
    )
    schema["evaluation.mode"] = dict(
        type="string",
        enum=["test_after_training", "validation_only"],
        default="test_after_training",
        description="Terminal evaluation policy",
    )
    for name, minimum, maximum in [
        ("hidden_size", 1, 1024),
        ("num_layers", 1, 8),
        ("head_hidden_size", 1, 2048),
    ]:
        schema[f"model.{name}"] = dict(
            type="integer", minimum=minimum, maximum=maximum, description=name
        )
    schema["model.dropout"] = dict(
        type="number", minimum=0.0, maximum=0.9, description="GRU and head dropout"
    )
    schema["model.huber_delta"] = dict(
        type="number", minimum=0.0001, maximum=100.0, description="Normalized Huber delta"
    )
    for name, minimum, maximum in [
        ("batch_size", 1, 4096),
        ("max_epochs", 1, 10000),
        ("early_stopping_patience", 1, 1000),
        ("seed", 0, 2147483647),
    ]:
        schema[f"training.{name}"] = dict(
            type="integer", minimum=minimum, maximum=maximum, description=name
        )
    for name, minimum, maximum in [
        ("learning_rate", 1e-8, 1.0),
        ("weight_decay", 0.0, 1.0),
        ("warmup_ratio", 0.0, 0.99),
        ("max_grad_norm", 0.0001, 1000.0),
    ]:
        schema[f"training.{name}"] = dict(
            type="number", minimum=minimum, maximum=maximum, description=name
        )
    schema["training.device"] = dict(
        type="string", enum=["cuda:0", "cpu"], description="Explicit compute device"
    )
    schema["training.precision"] = dict(type="string", enum=["fp32"], description="FP32")
    schema["training.scheduler"] = dict(
        type="string", enum=["cosine"], description="Cosine schedule"
    )
    schema["data.variant_id"] = dict(
        type="string", enum=["sigma_1m"], description="Public observation variant"
    )
    for name in ["train_windows_per_episode", "evaluation_stride_samples"]:
        schema[f"data.{name}"] = dict(type="integer", minimum=1, maximum=3000, description=name)
    return dict(
        id="gru_direct_v1",
        model_name="gru",
        version="v1",
        label="GRU Direct v1",
        defaults=defaults,
        parameter_schema=schema,
        factory=GRUDirectModel,
        prepare_data=prepare_data,
        load_split=load_split,
        validate_checkpoint=validate_checkpoint,
        load_predictor=load_predictor,
        metrics=metrics,
        prediction_contract=dict(
            history_samples=16,
            future_samples=75,
            dt_s=0.2,
            input_features=4,
            output_axes=["x", "y", "z"],
        ),
    )


def load_predictor(project_root, selection):
    """Load a manifest-verified checkpoint for public live inference."""
    from models.runtime.v1.simulation_inference import load_predictor as load

    return load(project_root, selection)
