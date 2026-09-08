# Balanced VTOL Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VTOL 160 Episode를 split별 행동·좌우 균형으로 생성하고 GRU가 동일한 핵심 행동 target 노출량을 갖는 dataset-owned window index를 사용하게 한다.

**Architecture:** `generate.py`가 명시적 `CorpusScheduleEntry`를 만들고 `full_flight.py`가 schedule 방향을 실행·기록한다. `dataset.py`는 schedule split을 검증해 보존하며 `training_data.py`가 private event를 사용해 공개 feature와 분리된 균형 index/audit을 만든다. GRU loader는 그 index만 읽는다.

**Tech Stack:** Python 3.12, NumPy, Hydra/OmegaConf, PyTorch, pytest, Ruff

**Spec:** `plan/balanced_vtol_corpus_design.md`

## Global Constraints

- 기존 dataset/run은 덮어쓰지 않는다.
- Local ENU, 5 Hz, public/evaluation 경계와 70/15/15 split을 유지한다.
- 행동·방향 label은 GRU feature/target에 넣지 않는다.
- Train 112 / Validation 24 / Test 24, 핵심 행동 marginal과 좌·우 수는 split별로 정확히 같다.
- Train 핵심 행동마다 256개 91-sample window를 사용한다.
- Validation/Test는 75-sample 고정 stride이며 test observation은 학습 준비에서 읽지 않는다.

---

### Task 1: 균형 schedule과 명시적 방향

**Files:**
- Modify: `data_generation/v1/generate.py`
- Modify: `data_generation/v1/full_flight.py`
- Modify: `data_generation/v1/configs/corpus_vtol.yaml`
- Test: `data_generation/v1/tests/test_corpus_generation.py`
- Test: `data_generation/v1/tests/test_full_flight.py`

**Interfaces:**
- Produces: `CorpusScheduleEntry(scenario_id: str, seed: int, split: str, direction: int)`
- Produces: `run_full_flight_episode(..., direction: int | None = None)`

- [x] Write tests asserting 160 entries, 112/24/24 split counts, equal phase marginals, equal direction per turning behavior, deterministic schedule, and opposite signed turn rates for explicit directions.
- [x] Run the focused tests and confirm failure because balanced entries and explicit direction do not exist.
- [x] Implement config-validated balanced blocks and direction override while retaining legacy schedule behavior for other corpus configs.
- [x] Record one zero-duration `turn_direction:left|right` event without changing public identifiers or motion constraints.
- [x] Run focused tests until they pass.

### Task 2: Explicit split publication

**Files:**
- Modify: `data_generation/v1/dataset.py`
- Modify: `data_generation/v1/generate.py`
- Test: `data_generation/v1/tests/test_multi_object_dataset.py`
- Test: `data_generation/v1/tests/test_corpus_generation.py`

**Interfaces:**
- Consumes: `dict[episode_id, split]` derived from schedule entries.
- Produces: `write_dataset(..., episode_splits: dict[str, str] | None = None)`.

- [x] Write tests rejecting missing/extra Episode IDs, invalid split labels and wrong 70/15/15 counts.
- [x] Run tests and confirm the new argument/validation is absent.
- [x] Implement exact-ID and count validation; preserve existing random assignment when no explicit mapping is supplied.
- [x] Pass the generated mapping from `generate_from_config` and verify parent/variant split isolation.
- [x] Run focused tests until they pass.

### Task 3: Dataset-owned balanced window index and audit

**Files:**
- Modify: `data_generation/v1/training_data.py`
- Modify: `data_generation/v1/dataset.py`
- Modify: `data_generation/v1/configs/corpus_vtol.yaml`
- Test: `data_generation/v1/tests/test_training_data.py`

**Interfaces:**
- Consumes: in-memory `MotionEpisode.event_records`, published Episode metadata and `behavior_balance` training config.
- Produces: `training/balanced_windows.csv` with the existing six index columns.
- Produces: `evaluation/behavior_balance.csv` with split/action/direction/duration/window/target counts.

- [x] Write real MotionEpisode fixtures with contiguous core events and assert 256 windows per core action, 128 per direction for turning actions, deterministic selection and separate transition rows.
- [x] Run tests and confirm the balanced selector is missing.
- [x] Implement event normalization, fully-contained candidate enumeration, deterministic no-replacement selection and fail-closed quota checks.
- [x] Keep behavior/direction columns only in the evaluation audit; keep the model-facing index label-free.
- [x] Run focused tests until they pass.

### Task 4: GRU consumes the frozen dataset index

**Files:**
- Modify: `models/gru/v1/data.py`
- Modify: `models/gru/v1/configs/default.yaml`
- Test: `models/gru/v1/tests/test_direct.py`
- Test: `models/gru/v1/tests/test_window_seed.py`

**Interfaces:**
- Consumes: `training/balanced_windows.csv` and public split observation exports.
- Produces: the existing `PreparedData` and run-local copied `window_index.csv`.

- [x] Write tests proving missing/tampered balanced index fails, train uses exact published starts, validation remains fixed, and no evaluation CSV is read.
- [x] Run tests and confirm current loader rebuilds random windows.
- [x] Implement strict index reading, identity/hash/bounds validation and run-local copy.
- [x] Preserve train-only normalization and explicit test loading boundary.
- [x] Run focused tests until they pass.

### Task 5: Documentation, full verification and real corpus

**Files:**
- Modify: `data_generation/v1/README.md`
- Modify: `models/gru/v1/README.md`
- Modify: `README.md`
- Update: `plan/balanced_vtol_corpus_design.md`

**Interfaces:**
- Produces: one new immutable dataset under `outputs/data_generation/v1/<dataset_id>/`.

- [x] Run focused suites for corpus, full-flight, dataset, training data and GRU data.
- [x] Run the full project pytest suite and scoped Ruff.
- [x] Generate `corpus_vtol` with seed 17 and verify 160 complete Episodes.
- [x] Verify `behavior_balance.csv`, balanced index counts, split isolation, public/evaluation boundary and every manifest SHA-256.
- [x] Record exact commands, counts, dataset ID, hashes and limitations in the design/implementation record.

## Verification record — 2026-09-08

- Focused regression suite: `45 passed`.
- Full project suite: `444 passed`, `8 warnings` from the existing Transformers
  checkpoint ordering fallback.
- Scoped Ruff for all changed Python files: pass. Whole-tree Ruff still reports 67
  pre-existing findings in unrelated Gazebo and transition probe files.
- Dataset ID:
  `VTOL_전체_이착륙_질점_시나리오_corpus_160episodes_seed17_89aa62fc7852`.
- Split: Train 112 / Validation 24 / Test 24.
- Train: 12 core behaviors × 256 windows; turning directions 128/128;
  19,200 target points per behavior; 672 separate transition windows.
- Model-facing `sigma_1m` index: Train 3,744 / Validation 754 / Test 754 windows.
- All 50 `files.csv` entries passed size and SHA-256 verification.
- SHA-256: `balanced_windows.csv`
  `799a44783e06cf43340d5880cde26220ecf378897f5410721d500236f7f04ed2`;
  `behavior_balance.csv`
  `a1ee8a978b2371629db6bdeddf5d0f8e8d7a6e1253c3ab08407be58a494d6d07`;
  `effective_config.yaml`
  `937ac064920cadb76329af1f4fda0ed5187f24352123fc1ffb4735be33b815ef`;
  `manifest.csv`
  `7cbec6df06c14de66925369be4649ca546dcd841e81d86374ef5ca9493014c2f`.
- Raw maneuver durations remain behavior dependent. Equalization applies to Episode
  marginals and model core target exposure; no model retraining or accuracy claim was made.
