"""Strict public live-observation boundary; no evaluation state is accepted."""

import numpy as np

HISTORY_SAMPLES = 16
FUTURE_SAMPLES = 75
DT_S = 0.2


def validate_public_batch(batch):
    required = {"position_enu_m", "timestamp_s", "valid_mask"}
    if (
        not isinstance(batch, dict)
        or not required <= batch.keys()
        or set(batch) - required - {"sigma_m"}
    ):
        raise ValueError("Only public observation fields are allowed")
    positions = np.asarray(batch["position_enu_m"], dtype=np.float64)
    times = np.asarray(batch["timestamp_s"], dtype=np.float64)
    valid = np.asarray(batch["valid_mask"])
    if positions.ndim != 3 or positions.shape[1:] != (HISTORY_SAMPLES, 3) or not len(positions):
        raise ValueError("Expected nonempty [B,16,3] positions")
    if times.shape != positions.shape[:2] or valid.shape != times.shape:
        raise ValueError("History axes do not match")
    if not np.isfinite(positions).all() or not np.isfinite(times).all():
        raise ValueError("Nonfinite public observation")
    if not np.isin(valid, [True, False]).all() or not valid.all():
        raise ValueError("Sixteen consecutive valid observations required")
    if not np.allclose(np.diff(times, axis=1), DT_S, atol=1e-6, rtol=0):
        raise ValueError("Public observations must be at 5 Hz")
    result = dict(
        position_enu_m=positions.copy(), timestamp_s=times.copy(), valid_mask=valid.astype(bool)
    )
    if "sigma_m" in batch:
        sigma = np.asarray(batch["sigma_m"], dtype=float)
        if not np.isfinite(sigma).all() or not np.isin(sigma, [1.0, 3.0, 5.0]).all():
            raise ValueError("Unsupported public sigma")
        result["sigma_m"] = sigma.copy()
    return result
