"""Atomic run records and streamed artifact fingerprints."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import os
import time
import uuid
from pathlib import Path


def write_provenance(run_dir: Path, project_root: Path, dataset: Path) -> None:
    sources = {}
    for relative in ("models", "visualization/v1"):
        for path in sorted((project_root / relative).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yaml", ".css"}:
                sources[path.relative_to(project_root).as_posix()] = sha256(path)
    for name in ("pyproject.toml", "uv.lock"):
        if (project_root / name).is_file():
            sources[name] = sha256(project_root / name)
    dataset_metadata = {}
    for name in ("files.csv", "manifest.csv", "public/episodes.csv", "training/sequences.csv"):
        if (dataset / name).is_file():
            dataset_metadata[name] = sha256(dataset / name)
    atomic_json(
        run_dir / "provenance.json",
        {
            "source_sha256": sources,
            "dataset_path": str(dataset),
            "dataset_metadata_sha256": dataset_metadata,
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        },
    )


def atomic_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def atomic_text(path: Path, value: str) -> None:
    """Replace a complete file, retrying bounded Windows reader sharing conflicts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8", newline="")
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.02 * 2**attempt, 0.25))
    finally:
        temporary.unlink(missing_ok=True)


def atomic_csv(path: Path, rows: list[dict], fieldnames) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, buffer.getvalue())


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    metadata = read_json(run_dir / "run.json")
    rows = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in {"manifest.csv", "worker.log"}:
            continue
        if path.name.endswith(".tmp"):
            continue
        rows.append(
            {
                "run_id": metadata["run_id"],
                "parent_run": metadata.get("parent_run") or "",
                "path": path.relative_to(run_dir).as_posix(),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    atomic_csv(run_dir / "manifest.csv", rows,
               ("run_id", "parent_run", "path", "sha256", "bytes"))
