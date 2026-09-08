# Full-flight scenario generator implementation plan

> Execute task-by-task with TDD and a scoped code review. Preserve the existing dirty checkout; no commits, pushes, worktree migration, or unrelated cleanup.

**Goal:** Connect the agreed fixed-wing, helicopter and Standard VTOL catalogs to a continuous, at-most-600-second point-mass generator and existing storage/viewer.

**Architecture:** Add an explicit `full_flight` run path and YAML configuration. Compose velocity/turn targets with smooth entry/recovery and integrate position without resets. Keep legacy profiles and 60-second results readable. Metadata owns each episode's time length.

**Tech stack:** Python, NumPy, OmegaConf, existing CSV writer/readers and Dash viewer.

**Spec:** `aircraft_motion_scenarios.md`, `point_mass_transition_rules.md`.

## Global constraints

- Single object, LOCAL ENU, no wind or sensors, 5 Hz saved output, duration <=600 s, z starts/ends at 0, final speed 0. Existing 60 s / 301 samples remain supported.
- Fixed-wing: 50 m initial straight +3 degrees, cruise 300 m, subsequent climb <=10 degrees; descending <=10 degrees above 100 m, straight -3 degrees below 100 m except smooth landing recovery.
- Helicopter/VTOL initial vertical takeoff 80 m then hover, cruise 500 m. Helicopter slow final landing from 10 m.
- VTOL outbound transition 20 s at 80 m; inbound mode transition 10 s at 80 m then residual deceleration/hover before vertical landing. No turns/climbs during transitions.
- One selected maneuver per climb/cruise/descent; F64/H96/V64 catalog. Left/right and seeded parameters are variations, not added behaviors.
- Acceleration/jerk settings are experimental generation settings, not aircraft performance limits. Never increase their caps silently. Lengthen adjustable transitions or reject infeasible combinations with a reason.
- Observations only in public; truth/events/config in evaluation. Immutable UUID outputs and source/config hashes. No training execution or bulk 100-episode run in this task. Port 8088 only.

## Task 1: variable-length CSV contract and writer

Files: contracts/v1/validation.py, contracts/v1/csv_io.py, data_generation/v1/dataset.py, corresponding tests/README.

Interface: MotionEpisode retains existing fields. Validate 2..3001 samples, t=step*0.2, duration=(count-1)*0.2 <=600. No schema columns added. Legacy GeneratedEpisode stays 301.

- [x] RED: write/read a real 120 s MotionEpisode; reject >600 s, mismatched duration/count, truncated variants and nonuniform times.
- [x] GREEN: derive observation/truth lengths from the episode, validate metadata and each variant against its own count; preserve strict public allowlist and split grouping.
- [x] Run focused contract/dataset tests and record commands/results: 47 passed, `scenario_variable_length_report.md`.

## Task 2: full-flight compiler and generation entry

Files: data_generation/v1/full_flight.py, full_flight_config.py, configs/full_flight_settings.yaml, configs/full_flight_{fixed_wing,helicopter,vtol}.yaml, configs/profiles/*_full_flight_v1.yaml, tests/test_full_flight.py, generate.py, profiles.py.

Interface: `run_full_flight_episode(seed, *, settings, object_id, scenario_id=None) -> MotionEpisode`; catalog returns human-readable IDs and labels. `generate_from_config` recognizes explicit `full_flight` configuration without replacing legacy profiles.

- [x] RED: each object's basic flight starts/ends at ground with continuous velocity, reaches correct altitudes, preserves takeoff direction and VTOL transition intervals; invalid combo/budget rejected. Missing module/profile then boundary tests failed before implementation.
- [x] GREEN: YAML-defined motion settings, maneuver choices and labels; quintic target ramps plus integrated positions; point-mass generation and event labeling; one selected phase behavior including S-turn internals or stop/restart.
- [x] Check acceleration/jerk and altitude constraints on the combined fine-step trajectory. Stretch adjustable transitions with finite retries; reject time-infeasible combos, never clip position.
- [x] Connect explicit CLI config and reuse writer with honest local design provenance. Full-flight/service/object-support tests: 43 passed in 6.90 s.

## Task 3: existing viewer metadata-based playback

Files: visualization/v1/app.py, playback/data helpers and their tests, README.

- [x] RED: viewer loads variable-length dataset and seeks last sample without assuming 301/60; mixed episode lengths do not index past the end. Valid-fixture run: 8 failed, 2 legacy passed.
- [x] GREEN: derive max step and duration from dataset metadata; keep same four views and port 8088; no visual redesign. Focused variable playback: 12 passed. Evidence: `scenario_playback_report.md`.

## Task 4: integration evidence

- [x] Generate one immutable diagnostic dataset per object, validate/read via public and evaluation readers, build playback data and verify end frames.
- [x] Enumerate all F/H/V catalog choices with default settings and seed 17: F64/H96/V64 complete, none rejected in this one seed. Other settings/seeds remain subject to rejection.
- [x] Scoped review by code reviewer: two generator findings fixed (opaque public IDs and airborne minimum setting enforcement), scoped rereview approved; UI review approved.

## Progress / decisions

- Approved scope: user requested all agreed scenarios connected to generator. Numeric settings not explicitly fixed remain labeled experimental in effective YAML; no physical-limit claims.
- Existing repository contains unrelated changes; preserve them and work in place. No Git mutation or bulk training authorized.

## 실행 기록 — 2026-09-08

| 객체 / 조합 | dataset_id | 길이 | 샘플 수 |
|---|---|---:|---:|
| 고정익 F05-D | 75b57f3e-8fc0-4aa0-a566-cc11345555dc | 434.6 s | 2174 |
| 헬기 H10-F | 7c4e9c4f-d6cf-4a8b-b957-6b323f3c34cc | 442.6 s | 2214 |
| VTOL V05-D | 0f47f8c2-3e3c-4ee2-8e38-0287632b2f9e | 502.6 s | 2514 |

출력은 `outputs/data_generation/v1/<dataset_id>/`에 있다. master seed 17을 기존 생성 진입점의 child seed로 분리해 사용했다. 각 출력의 inventory 11개 파일 SHA-256과 현재 generator code hash를 대조했고, public/evaluation reader 및 네 projection의 마지막 frame을 확인했다.

전체 catalog 직접 실행(seed 17)의 길이 범위: 고정익 340.6~585.8 s, 헬기 410.8~443.8 s, VTOL 408.6~575.0 s. 이는 특정 실험 설정의 실행 결과이지 모든 속력/seed 조합이나 실기체 성능의 보장이 아니다.

검증: contract/writer 47 tests, full-flight/service/object-support 43 tests, viewer 45 tests 통과. Ruff 해당 변경 범위 통과. Browser를 새로 열어 시각적으로 재검증하지는 않았으며 HTTP callback·실제 마지막 marker payload·기존 PNG 테스트로 연결을 확인했다. 실행 중 서버는 재시작하지 않았다. 포트는 8088 그대로다.

이번 추가는 학습 가능한 형식의 생성 경로와 진단 3개까지다. 대량 학습 corpus·모델 학습은 실행하지 않았다. UI preset은 기본 조합을 사용하고 전체 조합 선택은 CLI scenario_id로 제공한다. 미확정 수치는 full_flight_settings.yaml에 실험용 설정으로 노출하며 출처 성능값으로 표시하지 않는다. 착륙장은 생성 궤적의 종점이며 출발점 귀환을 강제하지 않는다.
