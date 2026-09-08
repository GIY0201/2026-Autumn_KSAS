"""Isolated Transformers Trainer process and immutable run lifecycle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import shutil
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from models.runtime.v1.io import atomic_csv
from models.runtime.v1.io import atomic_json as _json


def initial_state_hash(model) -> str:
    """Hash named tensor metadata and raw bytes, independent of serialization headers."""
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        value = tensor.detach().cpu().contiguous()
        header = json.dumps([name, list(value.shape), str(value.dtype)]).encode("utf-8")
        digest.update(len(header).to_bytes(8, "big"))
        digest.update(header)
        digest.update(value.numpy().tobytes())
    return digest.hexdigest()


def validate_resume_sources(parent: dict, current: dict) -> None:
    """Compare observed train/validation bytes without opening the test split."""
    for split in ("train", "validation"):
        if not parent.get(split) or parent[split] != current.get(split):
            raise ValueError(f"Resume data mismatch: {split} observations")


def constant_velocity(positions, timestamps, horizon):
    """Fit velocity by least squares across all context points; anchor at last observation."""
    positions = np.asarray(positions, dtype=np.float64)
    timestamps = np.asarray(timestamps, dtype=np.float64)
    centered = timestamps - timestamps.mean(axis=1, keepdims=True)
    denominator = np.sum(centered**2, axis=1)
    if np.any(denominator <= 0):
        raise ValueError("Constant Velocity requires increasing timestamps")
    velocity = np.sum(centered[..., None] * positions, axis=1) / denominator[:, None]
    result = velocity[:, None, :] * np.asarray(horizon)[None, :, None]
    assert np.isfinite(result).all()
    return result


def publish_checkpoint(source: Path, destination: Path) -> None:
    """Publish only a complete resumable checkpoint within the current run."""
    required = ("optimizer.pt", "scheduler.pt", "rng_state.pth", "trainer_state.json")
    if not all((source / name).is_file() for name in required):
        raise ValueError(f"Incomplete resume checkpoint: {source}")
    if not any((source / name).is_file() for name in ("model.safetensors", "pytorch_model.bin")):
        raise ValueError("Incomplete resume checkpoint: model weights absent")
    pending = destination.with_name(destination.name + ".pending")
    backup = destination.with_name(destination.name + ".previous")
    for path in (pending, backup):
        if path.resolve().parent != destination.parent.resolve():
            raise ValueError("Checkpoint staging path escaped destination parent")
        if path.exists():
            shutil.rmtree(path)
    shutil.copytree(source, pending)
    if destination.exists():
        destination.rename(backup)
    pending.rename(destination)
    if backup.exists():
        shutil.rmtree(backup)


def _preview(trainer, dataset, scale, run_dir, name):
    prediction = trainer.predict(
        dataset.select_first(1)
        if hasattr(dataset, "select_first")
        else __import__("torch").utils.data.Subset(dataset, [0])
    )
    relative = np.asarray(prediction.predictions)[0] * scale
    horizon = (dataset.timestamps_s[0, 1] - dataset.timestamps_s[0, 0]) * np.arange(
        1, relative.shape[0] + 1
    )
    _json(
        run_dir / "evaluation" / f"preview_{name}.json",
        {
            "split": "validation",
            "checkpoint": name,
            "history": dataset.input_positions_m[0].tolist(),
            "observed": (dataset.targets_m[0] + dataset.anchors[0]).tolist(),
            "predicted": (relative + dataset.anchors[0]).tolist(),
            "relative_position_m": relative.tolist(),
            "position_enu_m": (relative + dataset.anchors[0]).tolist(),
            "horizon_s": horizon.tolist(),
        },
    )


def train(run_dir: Path, config: dict, metadata: dict) -> None:
    """Run train/validation; unseal the test loader only after model selection."""
    import torch
    from omegaconf import OmegaConf
    from safetensors.torch import load_file
    from transformers import (
        EarlyStoppingCallback,
        Trainer,
        TrainerCallback,
        TrainingArguments,
        set_seed,
    )

    from models.runtime.v1.registry import get_plugin

    settings = config["training"]
    evaluation_mode = config.get("evaluation", {}).get("mode", "test_after_training")
    if evaluation_mode not in ("validation_only", "test_after_training"):
        raise ValueError("Unknown evaluation mode")
    device = settings["device"]
    if device not in ("cpu", "cuda:0"):
        raise ValueError("Only explicit cpu or cuda:0 device is supported")
    if device == "cuda:0" and not torch.cuda.is_available():
        raise RuntimeError("cuda:0 requested but CUDA is unavailable; select cpu explicitly")
    if settings["precision"] != "fp32":
        raise ValueError("GRU Direct v1 requires FP32")
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    cudnn_enabled = settings.get("cudnn_enabled", True)
    if not isinstance(cudnn_enabled, bool):
        raise ValueError("training.cudnn_enabled must be a boolean")
    torch.backends.cudnn.enabled = cudnn_enabled
    set_seed(settings["seed"])
    plugin = get_plugin(config["plugin_id"])
    dataset_path = Path(metadata.get("dataset_path") or config["data"]["dataset_path"])
    bundle = plugin["prepare_data"](dataset_path, config, run_dir)
    _json(
        run_dir / "data_sources.json",
        {
            "train": bundle.train.source_sha256,
            "validation": bundle.validation.source_sha256,
        },
    )
    scale = float(bundle.normalization["scale_m"])
    model = plugin["factory"](config)
    from models.gru.v1.diagnostics import (
        MOVEMENT_THRESHOLD_M,
        SUBSET_PER_GROUP,
        select_indices,
        write_diagnostics,
    )

    diagnostics_enabled = config.get("diagnostics", {}).get("enabled", True)
    diagnostic_indices = {}
    if diagnostics_enabled:
        subset_rows = []
        for split, dataset in (("train", bundle.train), ("validation", bundle.validation)):
            if not hasattr(dataset, "window_rows"):
                _json(
                    run_dir / "diagnostics" / split / "provenance.json",
                    {
                        "status": "unsupported",
                        "reason": "dataset lacks window lineage",
                    },
                )
                continue
            diagnostic_indices[split] = select_indices(dataset)
            for index in diagnostic_indices[split]:
                target = dataset.targets_m[index]
                excursion = float(np.linalg.norm(target[:, :2], axis=-1).max())
                dz = float(target[-1, 2])
                subset_rows.append(
                    {
                        **dataset.window_rows[index],
                        "dataset_index": index,
                        "xy_max_excursion_m": excursion,
                        "net_dz_m": dz,
                        "observed_xy_movement": "moving"
                        if excursion > MOVEMENT_THRESHOLD_M
                        else "small",
                        "observed_vertical": "up"
                        if dz > MOVEMENT_THRESHOLD_M
                        else "down"
                        if dz < -MOVEMENT_THRESHOLD_M
                        else "level",
                        "movement_threshold_m": MOVEMENT_THRESHOLD_M,
                        "subset_per_group": SUBSET_PER_GROUP,
                        "selection_rule": "first two sequence_ids per observed XY/vertical group",
                    }
                )
        if subset_rows:
            atomic_csv(run_dir / "diagnostics/subset_index.csv", subset_rows, list(subset_rows[0]))
    _json(
        run_dir / "initialization.json",
        {
            "state_sha256": initial_state_hash(model),
            "seed": settings["seed"],
            "stage": "factory_before_resume_restore",
            "algorithm": "sha256 sorted named tensor shape dtype and contiguous CPU bytes",
        },
    )
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)
    start = time.monotonic()
    resume_path = None
    inherited_best = None
    schedule_path = run_dir / "schedule.json"
    total_steps = math.ceil(len(bundle.train) / settings["batch_size"]) * settings["max_epochs"]
    schedule = {"total_steps": total_steps, "resume_policy": "initial cosine schedule"}
    parent_checkpoint = metadata.get("parent_checkpoint")
    if parent_checkpoint:
        source = Path(parent_checkpoint).resolve()
        parent_dir = source.parents[1]
        parent_config = OmegaConf.to_container(OmegaConf.load(parent_dir / "effective_config.yaml"))
        expected = json.loads(json.dumps(parent_config))
        expected["training"]["max_epochs"] = config["training"]["max_epochs"]
        if expected != config:
            raise ValueError("Resume permits only max_epochs changes")
        if settings["max_epochs"] < parent_config["training"]["max_epochs"]:
            raise ValueError("Resume cannot reduce max_epochs")
        for name in ("window_index.csv", "normalization.json"):
            if (parent_dir / name).read_bytes() != (run_dir / name).read_bytes():
                raise ValueError(f"Resume data mismatch: {name}")
        validate_resume_sources(
            json.loads((parent_dir / "data_sources.json").read_text(encoding="utf-8")),
            json.loads((run_dir / "data_sources.json").read_text(encoding="utf-8")),
        )
        resume_path = checkpoint_dir / "resume_source"
        publish_checkpoint(source, resume_path)
        schedule = json.loads((parent_dir / "schedule.json").read_text())
        if settings["max_epochs"] > parent_config["training"]["max_epochs"]:
            saved_state = json.loads((source / "trainer_state.json").read_text())
            saved_scheduler = torch.load(source / "scheduler.pt", weights_only=True)
            current_lrs = saved_scheduler["_last_lr"]
            restart = all(value <= 0 for value in current_lrs)
            schedule = {
                "total_steps": total_steps,
                "segment_start": saved_state["global_step"],
                "start_factors": [
                    1.0 if restart else value / settings["learning_rate"] for value in current_lrs
                ],
                "warm_restart": restart,
                "resume_policy": "explicit configured-LR warm restart after exhausted horizon"
                if restart
                else "continuous cosine extension from restored LR",
            }
        total_steps = schedule["total_steps"]
        parent_best = parent_dir / "checkpoints" / "best"
        if parent_best.exists():
            publish_checkpoint(parent_best, checkpoint_dir / "best")
            inherited_best = str(checkpoint_dir / "best")
        state_path = resume_path / "trainer_state.json"
        state = json.loads(state_path.read_text())
        state["best_model_checkpoint"] = inherited_best
        _json(state_path, state)
    _json(schedule_path, schedule)

    class FixedHorizonTrainer(Trainer):
        def create_scheduler(self, num_training_steps, optimizer=None):
            if "segment_start" in schedule and self.lr_scheduler is None:

                def decay(step, factor):
                    fraction = max(
                        0.0,
                        min(
                            1.0,
                            (step - schedule["segment_start"])
                            / (total_steps - schedule["segment_start"]),
                        ),
                    )
                    return factor * (1 + math.cos(math.pi * fraction)) / 2

                self.lr_scheduler = torch.optim.lr_scheduler.LambdaLR(
                    optimizer or self.optimizer,
                    [
                        lambda step, factor=factor: decay(step, factor)
                        for factor in schedule["start_factors"]
                    ],
                )
                self._created_lr_scheduler = True
                return self.lr_scheduler
            return super().create_scheduler(total_steps, optimizer)

        def evaluate(self, *args, **kwargs):
            # Evaluation must not perturb dropout/sampler RNG, including stop-time validation.
            python_rng, numpy_rng = random.getstate(), np.random.get_state()
            cpu_rng = torch.get_rng_state()
            cuda_rng = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
            was_training = self.model.training
            try:
                # predict does not call on_evaluate: early stopping remains validation-only.
                diagnostic = super().predict(self.train_dataset, metric_key_prefix="train_eval")
                self.log(diagnostic.metrics)
                result = super().evaluate(*args, **kwargs)
                for split, dataset in (("train", bundle.train), ("validation", bundle.validation)):
                    if split not in diagnostic_indices:
                        continue
                    indices = diagnostic_indices[split]
                    predictions = (
                        diagnostic.predictions[indices]
                        if split == "train"
                        else super()
                        .predict(
                            torch.utils.data.Subset(dataset, indices),
                            metric_key_prefix="diagnostic",
                        )
                        .predictions
                    )
                    write_diagnostics(
                        run_dir / "diagnostics" / f"epoch_{self.state.global_step}" / split,
                        dataset,
                        predictions,
                        scale,
                        run_id=metadata.get("run_id", run_dir.name),
                        checkpoint="epoch",
                        epoch=self.state.epoch,
                        indices=indices,
                        dataset_path=dataset_path,
                    )
                return result
            finally:
                random.setstate(python_rng)
                np.random.set_state(numpy_rng)
                torch.set_rng_state(cpu_rng)
                if cuda_rng is not None:
                    torch.cuda.set_rng_state_all(cuda_rng)
                self.model.train(was_training)

    class Lifecycle(TrainerCallback):
        def __init__(self):
            self.last_step_time = time.monotonic()
            self.last_step = 0

        def on_train_begin(self, args, state, control, optimizer=None, **kwargs):
            if schedule.get("warm_restart") and state.global_step == schedule["segment_start"]:
                for group in optimizer.param_groups:
                    group["lr"] = settings["learning_rate"]

        def on_step_end(self, args, state, control, **kwargs):
            if (run_dir / "stop.request").exists():
                control.should_training_stop = True
                control.should_save = True
                control.should_evaluate = True
            return control

        def on_log(self, args, state, control, logs=None, **kwargs):
            now = time.monotonic()
            values = {
                key: float(value)
                for key, value in (logs or {}).items()
                if isinstance(value, (int, float)) and math.isfinite(value)
            }
            row = {
                "timestamp": datetime.now(UTC).isoformat(),
                "global_step": state.global_step,
                "epoch": state.epoch or 0,
                "split": "validation"
                if any(key.startswith("eval_") for key in values)
                else "train",
                **values,
            }
            row["elapsed_s"] = now - start
            row["samples_per_second"] = (
                (state.global_step - self.last_step)
                * settings["batch_size"]
                / max(now - self.last_step_time, 1e-9)
            )
            row["gpu_memory_mb"] = torch.cuda.memory_allocated() / 2**20 if device != "cpu" else 0.0
            self.last_step_time, self.last_step = now, state.global_step
            with (logs_dir / "metrics.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
            rows = [
                json.loads(line) for line in (logs_dir / "metrics.jsonl").read_text().splitlines()
            ]
            keys = list(dict.fromkeys(key for item in rows for key in item))
            atomic_csv(logs_dir / "metrics.csv", rows, keys)
            _json(
                run_dir / "status.json",
                {
                    "status": "running",
                    "epoch": state.epoch,
                    "global_step": state.global_step,
                    "best_ade_m": state.best_metric,
                    "elapsed_s": now - start,
                    "eta_s": (
                        (settings["max_epochs"] - (state.epoch or 0))
                        * (now - start)
                        / max(state.epoch or 0, 1e-9)
                    ),
                    "metrics": row,
                },
            )

        def on_save(self, args, state, control, **kwargs):
            source = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            publish_checkpoint(source, checkpoint_dir / "last")
            if (
                state.best_model_checkpoint
                and Path(state.best_model_checkpoint).resolve() == source.resolve()
            ):
                publish_checkpoint(source, checkpoint_dir / "best")

    arguments = TrainingArguments(
        output_dir=str(checkpoint_dir / "trainer"),
        logging_dir=str(run_dir / "tensorboard"),
        learning_rate=settings["learning_rate"],
        weight_decay=settings["weight_decay"],
        per_device_train_batch_size=settings["batch_size"],
        per_device_eval_batch_size=settings["batch_size"],
        num_train_epochs=settings["max_epochs"],
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_strategy="steps",
        logging_steps=max(1, math.ceil(len(bundle.train) / settings["batch_size"])),
        save_total_limit=2,
        save_safetensors=True,
        load_best_model_at_end=True,
        metric_for_best_model="ade_m",
        greater_is_better=False,
        lr_scheduler_type=settings["scheduler"],
        warmup_ratio=settings["warmup_ratio"],
        max_grad_norm=settings["max_grad_norm"],
        seed=settings["seed"],
        data_seed=settings["seed"],
        fp16=False,
        bf16=False,
        use_cpu=device == "cpu",
        report_to=["tensorboard"],
        dataloader_num_workers=0,
        dataloader_pin_memory=device != "cpu",
        remove_unused_columns=False,
        disable_tqdm=True,
        optim="adamw_torch",
        restore_callback_states_from_checkpoint=True,
    )

    def diagnostic_metrics(result):
        values = plugin["metrics"](result.predictions, result.label_ids, scale)
        error = np.abs(np.asarray(result.predictions) - np.asarray(result.label_ids))
        delta = float(config.get("model", {}).get("huber_delta", 1.0))
        huber = np.where(error <= delta, 0.5 * error**2, delta * (error - 0.5 * delta))
        assert np.isfinite(huber).all()
        values.update(
            {f"huber_{axis}": float(huber[..., index].mean()) for index, axis in enumerate("xyz")}
        )
        weights = np.asarray(config.get("model", {}).get("axis_loss_weights", [1, 1, 1]))
        contributions = huber.mean(axis=(0, 1)) * weights / weights.sum()
        total = float(contributions.sum())
        values.update(
            {
                f"weighted_huber_{axis}": float(contributions[index])
                for index, axis in enumerate("xyz")
            }
        )
        values.update(
            {
                f"loss_share_{axis}": float(contributions[index] / total) if total else 0.0
                for index, axis in enumerate("xyz")
            }
        )
        return values

    trainer = FixedHorizonTrainer(
        model=model,
        args=arguments,
        train_dataset=bundle.train,
        eval_dataset=bundle.validation,
        compute_metrics=diagnostic_metrics,
        callbacks=[
            EarlyStoppingCallback(early_stopping_patience=settings["early_stopping_patience"]),
            Lifecycle(),
        ],
    )
    trainer.train(resume_from_checkpoint=str(resume_path) if resume_path else None)
    stopped = (run_dir / "stop.request").exists()
    if not (checkpoint_dir / "last").exists():
        raise RuntimeError("Trainer exited without a resumable checkpoint")
    if not (checkpoint_dir / "best").exists():
        raise RuntimeError("Trainer exited without a validation-selected checkpoint")
    for name in ("last", "best"):
        trainer.model.load_state_dict(load_file(str(checkpoint_dir / name / "model.safetensors")))
        _preview(trainer, bundle.validation, scale, run_dir, name)
        if diagnostics_enabled and hasattr(bundle.validation, "window_rows"):
            detail_prediction = trainer.predict(bundle.validation)
            write_diagnostics(
                run_dir / "diagnostics" / name / "validation",
                bundle.validation,
                detail_prediction.predictions,
                scale,
                run_id=metadata.get("run_id", run_dir.name),
                checkpoint=name,
                epoch=json.loads(
                    (checkpoint_dir / name / "trainer_state.json").read_text(encoding="utf-8")
                )["epoch"],
                dataset_path=dataset_path,
            )
    test_evaluated = not stopped and evaluation_mode == "test_after_training"
    if test_evaluated:
        evaluation = run_dir / "evaluation"
        evaluation.mkdir(exist_ok=True)
        marker = evaluation / "test_evaluation_started.json"
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"checkpoint": "best", "timestamp": datetime.now(UTC).isoformat()}, stream)
        test = plugin["load_split"](dataset_path, "test", config, bundle.normalization)
        _json(
            run_dir / "data_sources.json",
            {
                "train": bundle.train.source_sha256,
                "validation": bundle.validation.source_sha256,
                "test": test.source_sha256,
            },
        )
        prediction = trainer.predict(test)
        if diagnostics_enabled and hasattr(test, "window_rows"):
            write_diagnostics(
                run_dir / "diagnostics" / "best" / "test",
                test,
                prediction.predictions,
                scale,
                run_id=metadata.get("run_id", run_dir.name),
                checkpoint="best",
                epoch=json.loads(
                    (checkpoint_dir / "best/trainer_state.json").read_text(encoding="utf-8")
                )["epoch"],
                dataset_path=dataset_path,
            )
        horizon = (test.timestamps_s[0, 1] - test.timestamps_s[0, 0]) * np.arange(
            1, test.targets_m.shape[1] + 1
        )
        cv = constant_velocity(test.input_positions_m, test.timestamps_s, horizon)
        rows = [
            {
                "model": "gru_direct",
                **plugin["metrics"](prediction.predictions, prediction.label_ids, scale),
            },
            {"model": "constant_velocity", **plugin["metrics"](cv / scale, test.labels, scale)},
        ]
        with (evaluation / "test_metrics.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        _json(
            evaluation / "protocol.json",
            {
                "test_passes": 1,
                "test_evaluated": True,
                "evaluation_mode": evaluation_mode,
                "checkpoint": "best",
                "target": "future sigma_1m observations",
                "constant_velocity": (
                    "least squares velocity from all 16 observations; last-point anchor"
                ),
                "window_count": len(test),
            },
        )
    else:
        _json(
            run_dir / "evaluation" / "protocol.json",
            {
                "test_passes": 0,
                "test_evaluated": False,
                "evaluation_mode": evaluation_mode,
                "reason": "stopped" if stopped else "validation_only",
            },
        )
    terminal = {
        "status": "stopped" if stopped else "completed",
        "epoch": trainer.state.epoch,
        "global_step": trainer.state.global_step,
        "best_ade_m": trainer.state.best_metric,
        "elapsed_s": time.monotonic() - start,
        "evaluation_mode": evaluation_mode,
        "test_evaluated": test_evaluated,
        "cudnn_enabled": cudnn_enabled,
    }
    from models.runtime.v1.io import write_manifest

    write_manifest(run_dir)
    _json(run_dir / ("stopped.json" if stopped else "success.json"), terminal)
    _json(run_dir / "status.json", terminal)
    write_manifest(run_dir)


def run_worker(run_dir: Path) -> int:
    run_dir = Path(run_dir).resolve()
    try:
        from omegaconf import OmegaConf

        metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        config = OmegaConf.to_container(
            OmegaConf.load(run_dir / "effective_config.yaml"), resolve=True
        )
        if (run_dir / "success.json").exists() or (
            run_dir / "evaluation" / "test_evaluation_started.json"
        ).exists():
            raise ValueError(
                "Existing terminal/test run cannot be executed again; create a child run"
            )
        _json(run_dir / "status.json", {"status": "running", "pid": os.getpid()})
        train(run_dir, config, metadata)
        return 0
    except Exception as error:
        traceback.print_exc()
        _json(
            run_dir / "status.json",
            {"status": "failed", "error": str(error), "traceback": traceback.format_exc()},
        )
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    raise SystemExit(run_worker(parser.parse_args().run_dir))


if __name__ == "__main__":
    main()
