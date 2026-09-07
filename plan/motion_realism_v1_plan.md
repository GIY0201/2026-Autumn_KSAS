# Motion realism v1 Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development, task-scoped TDD and read-only review. No Git operations: this workspace is not a repository; use the baseline copy and file diffs under the task's temp folder.

**Goal:** 실제 비행 범위에 근거한 쿼드콥터 생성과 명확한 재생을 먼저 완료하고 VTOL 운동을 뒤이어 개선한다.

**Architecture:** source 분석과 연속 reference 계획을 생성 모듈에 두고 기존 객체별 운동 적분을 재사용한다. controller·source-fit·observation을 분리한다. viewer에는 evaluation-only 현재 위치 표식을 추가한다.

**Tech Stack:** existing Python 3.12 / uv / NumPy / SciPy / Hydra+OmegaConf / Dash+Plotly.

**Spec:** `plan/motion_realism_v1_spec.md`

## Global Constraints

- 같은 v1, Local ENU, 60초·5 Hz·301개, 단일 객체, 신규 무풍을 유지한다.
- 관측오차 `sigma=1,3,5 m`, public/evaluation 구분과 기존 CSV 계약을 유지한다.
- 기존 source·dataset·manifest를 덮어쓰지 않는다. 기존 X8·헬기 동작은 변경하지 않는다.
- viewer는 `127.0.0.1:8088`만 사용한다. 사용자 요청 없는 Git commit/init, 다른 프로젝트 변경, AGENTS 자동 수정은 하지 않는다.
- 실행마다 달라지는 수치·경로·ID는 config/metadata에서 읽는다. Python에 하드코딩하지 않는다.
- 원본 위치를 재생하거나 조각을 이어 붙인 것을 새 합성 운동으로 표시하지 않는다.

## Task 1: Source-grounded quadrotor motion

**Files:** create `data_generation/v1/quadrotor_reference.py`, `data_generation/v1/motion_reference_analysis.py`, corresponding tests, `configs/motion_reference/bitcraze_positioning.yaml`, `data_sources/bitcraze_positioning/SOURCE.md`; modify `quadrotor_scenario.py`, Crazyflie profile and the two quadrotor preset descriptions. Keep changes out of VTOL/X8/heli engines.

**Interfaces:** preserve `run_quadrotor_episode(seed, profile) -> MotionEpisode`; new reference planner provides time-indexed position/velocity/acceleration, and source analysis reads numeric NPY with `allow_pickle=False`, returning valid segments and summaries. Integration uses explicit experiment selector so old saved profiles remain replayable.

- [x] Tests first: verify normalized 7th-order blend `35*s**4-84*s**5+70*s**6-20*s**7` reaches endpoints with zero first/second/third derivatives; midpoint value is 0.5 and derivative is 2.1875. Verify gap splitting with numeric data `[t=0,.01,.02,.5,.51]` produces two segments rather than interpolating the long gap. Verify nonfinite position never crosses a gap in summaries.
- [x] Run focused tests and record RED before production changes.
- [x] Implement source segmentation and valid-window derivative summaries with exact config values from the spec. Derive and report source geometry and speed/acceleration statistics from 00/01; do not tune on 02/03. Keep original README/scripts untouched.
- [x] Implement seeded new bounded target positions and source-supported nominal mean speeds. Reference duration respects requested mean speed and explicit peak speed/acceleration bounds. Explicit target geometry/limits derive from source/calibration or are labeled engineering margins in config. Integrate reference PVA feedforward with existing feedback and dynamics, preserving legacy mode for old experiment configs.
- [x] Tests demonstrate deterministic repeat seeds, different seeds producing different reference geometry, finite continuous actual state, reference speed/acceleration bounds and no forced actual position assignment. Test the preserved legacy path using an explicit old fixture, not by changing assertions to hide regressions.
- [x] Run focused tests and full suite once, Ruff and source comparison; record before/after distribution results without asserting field validation. Write report to `temp/motion-realism-20260907/task-1-report.md` and return exact files/commands. Initial full suite: 150 passed; two review fixes each passed focused 37-test verification. Final integrated suite remains a later cross-task gate.

## Task 2: Unambiguous evaluation playback

**Files:** `visualization/v1/figures.py`, playback assets only if the existing trace-index mechanism requires it, viewer tests, README.

**Interfaces:** existing `build_current_marker_extensions` must update observation and optional truth current markers for each selected episode in XY/XZ/YZ/3D. Do not alter dataset reader/public-only permission boundary.

- [x] Tests first: with truth allowed, returned figures have distinct observation/current-truth coordinates; with public-only, no truth trace. For sample index 1, require exact stored coordinates for both marker updates and correct indices across two episodes.
- [x] RED, minimal implementation, GREEN; ensure no per-tick full figure redraw and no interpolation. Browser-discovered Plotly wire-format regression fixed; 17 viewer tests passed and scoped review approved.
- [x] Inspect 8088 UI with a new quadrotor diagnostic dataset after Task 1; verify Play/Pause and both marker meanings, run full regression and review. Actual marker movement verified; final UI/CLI bytes match, final 167-test suite and whole-change review pass.

## Task 3: VTOL motion realism

**Files:** bounded_motion scenario/engine/profile tests and explicit VTOL profile only; new source/analysis files only when real references are acquired. Preserve helicopter and X8 results.

- [x] Compare published/source time histories before specifying new reference numeric ranges; keep source status separate from chosen simulation settings. Tal Section VI-E checked; timestamped XYZ was unavailable and is not invented.
- [x] RED test reproduces an infeasible steady-turn command (speed 6 m/s, turn 35 deg/s under 3 m/s²) and premature phase transition with unmet velocity state. Require feasible coupled targets and phase-aware completion with explicit maximum-time failure handling.
- [x] Implement the smallest supported change and test continuity of speed/acceleration during phase changes, seed reproducibility and old helicopter independence. 47 focused tests PASS; legacy helicopter/VTOL hashes match.
- [x] Compare actual/source and new synthetic motion on the same axes where measured source exists. If raw position reference is unavailable, report that limitation and do not invent measured values. Paper/settings/achieved table and before/after finite-difference diagnostics recorded.
- [x] Run full suite, independent review and 8088 diagnostic playback; update final implementation record with dataset IDs, hashes, commands and evidence boundary. Final 167 passed in198.97s, Ruff PASS, whole-change review Approved; no independent real-flight validation claimed.

## Preflight and execution tracking

Ledger: `temp/motion-realism-20260907/progress.md`. Before each task, create its exact standalone brief with the constraints above and preserve the task baseline. Carry dependency results as paths, not full histories. Task 3 source-specific details are resolved by the source comparison step before its implementation dispatch; no unverified aircraft parameters are authorized by this plan.
