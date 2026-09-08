# Telemetry and scenario corpus implementation plan

> 이전 구현·실행 기록이다. 현재 식별 패턴 3종, 분할별 파일, 저장 dataset 목록 복원은 [현재 생성 안내](data_generation_current.md)를 따른다. 아래 dataset ID·프로세스 ID·화면 선택은 당시 기록이며 이전 결과에 식별 컬럼을 소급 추가하지 않았다.

Date: 2026-09-08. Scope: user-approved UI inspection and training-data preparation, not model training.

## Goal and architecture

Keep the existing v1 generator, CSV contract and 8088 viewer. Add a stored-truth telemetry panel synchronized with the player. Add explicit object-specific corpus presets that enumerate the existing full-flight catalog and publish a public-observation-only training index in the same immutable dataset. No model-specific context length is inferred.

## Constraints

- Public inputs never contain truth, commands, seed or future maneuver labels.
- All variants and windows of one episode inherit its existing 70/15/15 split.
- Full-length sequences are the default. Optional fixed windows use explicit sample count, random TRAIN starts and deterministic validation/test starts.
- No new model, port, Git commit or mutation of existing outputs. Existing 60 s presets remain supported.
- Numeric panels use saved ENU velocity and velocity-derived acceleration, not derivatives of noisy observations.

## Tasks

- [x] Telemetry: optional stored kinematics in `figures.py`; `telemetry.py`, player callback and responsive table. Exact velocity/magnitude/time, last/reverse seek and public-only tests passed.
- [x] Corpus: `generate.py` and `GenerationRequest` support `corpus`, catalog-derived counts and independent child seeds. Three UI YAML presets added; writer streams one Episode at a time.
- [x] Training preparation: `training_data.py` writes public sequence/window indexes before manifest publication and exposes a public-only reader. Whole-sequence default and explicit optional window sampling; oversized windows fail.
- [x] Focused regression, complete catalog generation, scoped code review, 8088 restart and real Browser generation/open/playback checks.

## Verification commands

Use `.venv/Scripts/python.exe -m pytest` for `visualization/v1/tests/test_telemetry.py`, `data_generation/v1/tests/test_training_data.py` and `test_corpus_generation.py`, first RED then GREEN. Run existing viewer/contracts/generator focused regressions and Ruff. Test fixtures use small real MotionEpisode datasets, not mocked truth.

## Status

Implementation verified for this scope. Model training/quality and real-flight validation are outside this task.

## Execution evidence

- TDD: telemetry 5 expected failures then 5 passed; corpus/training 8 expected failures then 8 passed.
- Final relevant regression: 148 passed in 70.73 s. Includes viewer, contracts, dataset writer, service, object support, full-flight and new tests.
- Ruff: all changed Python files passed. A broader scan also found pre-existing issues in unrelated Gazebo reference files; those were not changed and whole-project lint cleanliness is not claimed.
- Read-only scoped code review: APPROVE; no important findings. No Git commit/push.
- Actual Browser at 8088: new presets, background generation 64/64, result open, 64-Episode selection, time-synchronized telemetry. At 17.6 s, speed was 18.504 m/s / 66.613 km/h; paused at 33.0 s with matching table time.
- One listener on 8088, PID 31532 after restart. Original viewer PID 10452 was identity-checked and stopped. No alternate port.
- Output manifests, inventory file hashes, current generator code hash, Episode counts, scenario event count and three variants per sequence verified for all four outputs below.

| Object/run | Dataset ID | Episodes | Observation sequences | TRAIN/validation/test episodes |
|---|---|---:|---:|---|
| Fixed-wing CLI | c0de3f8f-e772-4bfd-a9ab-1ea1b10fae49 | 64 | 192 | 44/9/11 |
| Helicopter CLI | ce0ca7fd-4770-4cf3-ab80-9a7ab8016989 | 96 | 288 | 67/14/15 |
| VTOL CLI | fe782810-1cd1-4735-a2da-5d004f09421a | 64 | 192 | 44/9/11 |
| Fixed-wing UI check | d6064279-75ed-4c60-b0b7-3aff2a3a9032 | 64 | 192 | 44/9/11 |

All are under `outputs/data_generation/v1/`. The UI check intentionally repeats the fixed-wing seed17 corpus with a different output ID: it is **not additional independent training data**, and must not be combined as new held-out samples. The currently opened UI result is the UI-check dataset. CLI outputs are not auto-registered in the current server's result dropdown.

The default preparation preserves full-length observed sequences and does not automatically choose a GRU/RED-SDS context, future target or normalization. Optional windows use explicit configuration. Model-specific batching is a subsequent task.
