"""Direct deterministic 3D trajectory prediction from public observations."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from omegaconf import OmegaConf
from torch import nn

HISTORY_SAMPLES = 16
FUTURE_SAMPLES = 75
DT_S = 0.2
HISTORY_S = (HISTORY_SAMPLES - 1) * DT_S


def resolve_config(config):
    """Use the versioned YAML as the only source of experiment defaults."""
    defaults = OmegaConf.load(Path(__file__).parent / "configs/default.yaml")
    return OmegaConf.to_container(OmegaConf.merge(defaults, config), resolve=True)


class GRUDirectModel(nn.Module):
    """Encode history once and predict all future relative positions directly."""

    def __init__(self, config: dict):
        super().__init__()
        cfg = resolve_config(config if "model" in config else {"model": config})["model"]
        weights = np.asarray(cfg["axis_loss_weights"], dtype=np.float64)
        if weights.shape != (3,) or not np.isfinite(weights).all() or np.any(weights <= 0):
            raise ValueError("axis_loss_weights must contain three finite positive values")
        self.register_buffer(
            "axis_loss_weights", torch.tensor(weights, dtype=torch.float32), persistent=False
        )
        hidden = cfg["hidden_size"]
        layers = cfg["num_layers"]
        dropout = cfg["dropout"]
        head = cfg["head_hidden_size"]
        self.gru = nn.GRU(
            4, hidden, layers, batch_first=True, dropout=dropout if layers > 1 else 0.0
        )
        self.head = nn.Sequential(
            nn.Linear(hidden, head),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(head, FUTURE_SAMPLES * 3),
        )
        self.loss_function = nn.HuberLoss(delta=cfg["huber_delta"], reduction="none")

    def forward(self, input_features, labels=None):
        if input_features.ndim != 3 or tuple(input_features.shape[1:]) != (HISTORY_SAMPLES, 4):
            raise ValueError("input_features must have shape [B,16,4]")
        if not torch.isfinite(input_features).all():
            raise ValueError("nonfinite model input")
        _, hidden = self.gru(input_features)
        logits = self.head(hidden[-1]).reshape(-1, FUTURE_SAMPLES, 3)
        result = {"logits": logits}
        if labels is not None:
            if labels.shape != logits.shape or not torch.isfinite(labels).all():
                raise ValueError("invalid labels")
            raw_loss = self.loss_function(logits, labels)
            result["loss"] = (raw_loss * self.axis_loss_weights).sum(
                -1
            ).mean() / self.axis_loss_weights.sum()
        return result

    @torch.no_grad()
    def predict_positions(self, position_enu_m, timestamp_s, scale_m):
        device = next(self.parameters()).device
        # Anchor in float64 before conversion; Unix time loses subsecond steps in FP32.
        positions = torch.as_tensor(position_enu_m, dtype=torch.float64, device=device)
        times = torch.as_tensor(timestamp_s, dtype=torch.float64, device=device)
        if positions.ndim != 3 or tuple(positions.shape[1:]) != (HISTORY_SAMPLES, 3):
            raise ValueError("position shape must be [B,16,3]")
        if times.shape != positions.shape[:2] or not np.isfinite(scale_m) or scale_m <= 0:
            raise ValueError("invalid time shape or scale")
        if not torch.isfinite(times).all() or not torch.allclose(
            times[:, 1:] - times[:, :-1], torch.full_like(times[:, 1:], DT_S), atol=1e-4
        ):
            raise ValueError("timestamps must be finite and spaced at 0.2 seconds")
        features = torch.cat(
            (
                (positions - positions[:, -1:]) / scale_m,
                ((times - times[:, -1:]) / HISTORY_S).unsqueeze(-1),
            ),
            dim=-1,
        )
        was_training = self.training
        self.eval()
        try:
            relative = self(features.to(dtype=torch.float32))["logits"] * scale_m
        finally:
            self.train(was_training)
        return {
            "relative_position_m": relative,
            "position_enu_m": relative + positions[:, -1:],
            "horizon_s": torch.arange(1, FUTURE_SAMPLES + 1, device=device) * DT_S,
        }


def metrics(predictions, labels, scale_m):
    """Aggregate Euclidean and axis errors after restoring physical meters."""
    difference = (np.asarray(predictions, dtype=np.float64) - np.asarray(labels)) * scale_m
    if difference.ndim != 3 or difference.shape[1:] != (FUTURE_SAMPLES, 3):
        raise ValueError("metric arrays must have shape [B,75,3]")
    if not np.isfinite(difference).all() or not len(difference):
        raise ValueError("metric arrays must be finite and nonempty")
    error = np.linalg.norm(difference, axis=-1)
    result = {"ade_m": float(error.mean()), "fde_m": float(error[:, -1].mean())}
    result.update(
        {f"error_{h}s_m": float(error[:, round(h / DT_S) - 1].mean()) for h in (1, 3, 5, 10, 15)}
    )
    result.update(
        {
            f"axis_rmse_{axis}_m": float(np.sqrt(np.mean(difference[..., i] ** 2)))
            for i, axis in enumerate("xyz")
        }
    )
    assert all(np.isfinite(value) and value >= 0 for value in result.values())
    return result
