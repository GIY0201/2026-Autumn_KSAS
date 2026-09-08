# Point-Mass Stage 1 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking. Execute inline; do not launch implementation agents without authorization. Required code review follows the applicable project instructions.

**Goal:** 기존 6-DOF를 보존하면서 네 객체의 연구용 생성 선택을 질점 수준 모델로 통일한다.

**Architecture:** 새 고정익·쿼드콥터 경로는 공통 MotionEpisode와 저장 계층에 연결한다. VTOL·헬기의 기존 운동 코드는 유지하고, 기존 CLI의 의미와 저장 결과를 바꾸지 않는다.

**Tech Stack:** Python, NumPy, Hydra/OmegaConf, pytest, Ruff, 기존 Dash/Plotly UI.

**Spec:** [질점 전환 명세](point_mass_migration_design.md).

상태: **complete**. 수치 profile 근거표는 Task 1 산출물이며, 최종 통합 suite 226 passed와 두 독립 reviewer의 `APPROVE` / unresolved 0건을 [Stage 1 검증 기록](point_mass_stage1_verification.md)에 보존한다.

## Global Constraints

- 단계 1: 60초, 5 Hz, 301개 시점, Local ENU.
- 네 객체의 기존 모델·원본·출력 보존. 기존 CLI config ID의 의미 불변.
- public/evaluation 분리, 새 UUID, 기존 관측 sigma 유지.
- `127.0.0.1:8088` 외 listener 금지. 자동 latest 선택 금지.
- 새 production parameter는 versioned config와 출처 역할을 통해 전달.
- 단계 2의 100m/3도/10도/1,000m/600초 규칙과 단계 3 패널은 이번 완료 주장에 포함하지 않음.
- Git commit/push 없음. 기존 파일 변경은 scoped diff로 확인.

## Task 1: 기준선과 profile 수치 근거 확정

**Files:**
- Read: `data_generation/v1/control.py`, `scenario.py`, `quadrotor_reference.py`.
- Read: `data_generation/v1/configs/motion_reference/bitcraze_positioning.yaml`.
- Create: `plan/point_mass_profile_basis.md`.

- [x] `git status --short`를 기록하고 기존 6-DOF·VTOL·헬기 코드의 SHA-256을 캡처한다. 상태가 dirty면 사용자 변경을 별도로 구분한다.
- [x] 기존 suite를 실행해 실패를 신규 변경과 구분한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-point-mass-baseline
```

- [x] 근거표에 parameter 이름, 단위, 값 또는 계산식, 실제 source/config 위치, source hash, 역할, 제한을 기록한다.
- [x] X8의 검사 범위를 실제 성능 한계로 승격하지 않는다. 6-DOF 비교값을 사용할 때는 참조 dataset ID와 계산 구간을 적고 held-out 데이터를 tuning에 쓰지 않는다.
- [x] 쿼드콥터는 기존 Bitcraze calibration/reference 범위를 사용하며 비교 전용 mocap02/03으로 값을 고르지 않는다.
- [x] 신규 응답시간 등 근거 없는 항목은 실행자의 임의 숫자로 채우지 않고 설계값으로 명시하여 검토한다. 이것은 데이터 수집을 핑계로 무기한 중단하는 절차가 아니라, 수치표를 먼저 공개하는 한 번의 검토다.

## Task 2: 독립 질점 수치 커널

**Files:**
- Create: `data_generation/v1/point_mass.py`.
- Test: `data_generation/v1/tests/test_point_mass.py`.

**Interfaces:**
- `PointMassState`: position_enu_m, horizontal_speed_mps, track_heading_rad, vertical_speed_mps, track_turn_rate_rad_s.
- `PointMassTarget`: horizontal_speed_mps, vertical_speed_mps, track_turn_rate_rad_s.
- `PointMassLimits`: 명세 3.1과 Task 1에서 확정한 운동·적분 제한.
- `advance_point_mass(state: PointMassState, target: PointMassTarget, limits: PointMassLimits, dt_s: float) -> PointMassState`.

- [x] 먼저 정속 직선의 analytic solution, 일정 선회의 analytic solution, 좌우 대칭, 비유한값·음수 dt·불가능한 속도 요청 거부 테스트를 작성한다.
- [x] 실패 실행을 확인한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_point_mass.py
```

- [x] 위치는 속도로 적분한다. 같은 초기 속도와 목표 속도에서는 가속을 만들지 않는다. 선회·가속은 공유 가속 예산을 넘지 않는다. config 경계에 들어오게 위치를 사후 수정하지 않는다.
- [x] 고정익의 airborne 최소 속력을 검사하되 hover 가능한 객체에는 적용하지 않는다. 기체 자세·motor·actuator·trim을 새 상태나 runtime 의존성에 넣지 않는다.
- [x] 위 테스트에 가속+선회 동시 요청, 좌→우 반전, 상승→수평 전환, dt/2 비교 수렴 검사를 추가하여 통과시킨다.

단순 fixture의 수학적 기준은 다음과 같다. 이 숫자는 테스트용이며 production config로 사용하지 않는다.

```python
import math
import pytest

def test_turn_reference_geometry():
    speed = 10.0
    omega = 0.1
    elapsed = 2.0
    radius = speed / omega
    x = radius * math.sin(omega * elapsed)
    y = radius * (1.0 - math.cos(omega * elapsed))
    assert radius == pytest.approx(100.0)
    assert math.hypot(x, y - radius) == pytest.approx(radius)
```

실제 커널 테스트는 이 analytic 기준과 적분 상태를 비교해야 하며, 위 식 자체의 통과만으로 커널 완료를 선언하지 않는다.

## Task 3: profile·Episode·저장 연결

**Files:**
- Create: `data_generation/v1/point_mass_profile.py`, `point_mass_scenario.py`.
- Create: `data_generation/v1/configs/profiles/fixed_wing_point_mass_v1.yaml`, `quadrotor_point_mass_v1.yaml`.
- Modify: `data_generation/v1/profiles.py`, `generate.py`.
- Test: `data_generation/v1/tests/test_point_mass_generation.py`.

**Interfaces:**
- `validate_point_mass_profile(profile: dict) -> None`: object/model/engine、출처、설정 범위 검증.
- `run_point_mass_episode(seed: int, profile: dict) -> MotionEpisode`: 새 커널만 사용한 공중 운동 진단.
- `engine: point_mass`는 위 runner에 명시적으로 연결한다. 반환형은 기존 `records.MotionEpisode`다.

- [x] 테스트 fixture로 신규 engine의 routing 실패, metadata 불일치, 누락 source/hash 변경 거부를 먼저 확인한다.
- [x] Task 1의 근거표를 profile에 적용하고 명령 순서·지속·초기조건을 설정으로 전달한다. 자동 fallback과 숨은 default를 넣지 않는다.
- [x] 생성 상태를 적분해 MotionEpisode로 반환한다. 이벤트는 실제 완료와 관측창 종료를 구분한다.
- [x] X8·Crazyflie 적분기/runner를 호출하면 예외를 내는 patch를 걸고도 새 질점 경로가 정상 실행되는지 검증한다.
- [x] 같은 seed 두 번의 위치·속도 및 저장 CSV 일치, 다른 실행 UUID, 모델 ID 분리, public-only 경계, 기존 reader 호환을 검사한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_point_mass_generation.py contracts/v1/tests
```

## Task 4: 연구용 preset 전환

**Files:**
- Create: `data_generation/v1/configs/diagnostic_fixed_wing_point_mass.yaml`, `pilot_fixed_wing_point_mass.yaml`, `diagnostic_quadrotor_point_mass.yaml`, `pilot_quadrotor_point_mass.yaml`.
- Modify UI metadata only: 기존 `diagnostic.yaml`, `pilot.yaml`, `diagnostic_with_motion_check.yaml`, `diagnostic_quadrotor.yaml`, `pilot_quadrotor.yaml`.
- Test: `data_generation/v1/tests/test_service.py`, `test_object_support.py`, `visualization/v1/tests/test_generation_ui.py`.

- [x] 신규 config는 명시 profile ID를 사용하고 일반 preset의 기존 사용자 선택 방식을 유지한다.
- [x] 기존 6-DOF config에서 `ui` 블록만 제거한다. 나머지 CLI 생성 동작을 보존한다.
- [x] 일반 preset이 `point_mass` 또는 기존 `bounded_motion`으로만 연결되는지 검사한다. 이전 config의 파일 존재와 원래 6-DOF routing을 함께 검사한다.
- [x] 단위 테스트 통과 후 소량 진단 생성과 8088 Browser 생성→결과 선택→XY/XZ/YZ/3D 재생을 확인한다. 이번에는 100개 pilot을 자동 실행하지 않는다.
- [x] 연구용 dataset을 고르는 UI와 구 6-DOF dataset의 재생 호환을 구분한다. 구 결과를 열 수 있다고 연구용 신규 결과로 표시하지 않는다.

## Task 5: 회귀·기록·완료 판정

**Files:**
- Modify: 루트 `README.md`, `data_generation/v1/README.md`, `visualization/v1/README.md`.
- Create: `plan/point_mass_stage1_verification.md`.

- [x] 요구된 독립 code/Python review를 수행하고 발견된 문제를 수정한다. ENU fixed-wing 좌·우 sign와 state/transition constraint guard를 TDD로 보완했고 두 reviewer가 재검토 `APPROVE` / unresolved 0건을 판정했다.
- [x] 전체 suite와 lint를 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-point-mass-final
.\.venv\Scripts\python.exe -m ruff check contracts data_generation visualization
git diff --check
git status --short
```

- [x] 테스트 개수·exit code, 실행 config·seed·dataset ID·모델 ID·hash, Browser 확인 범위를 기록한다.
- [x] 보존 대상으로 정한 코드 SHA-256과 기존 결과 불변을 대조한다.
- [x] README는 확인된 새 engine/preset만 설명하고 실비행 검증·10분 시나리오 완료를 주장하지 않는다.
- [x] 독립 review를 포함한 Task 1–5가 실제 통과한 뒤에만 1단계 완료로 표시하고 2단계 명세로 넘어간다.

## 현재 진행 상태

- [x] 단계별 범위와 보존 경계를 문서화했다.
- [x] 저장·profile·UI 연결 지점을 read-only로 확인했다.
- [x] 수치 profile 근거표 검토.
- [x] production code·테스트 작성 및 실행.
- [x] 8088 Browser 검증.
- [x] final full-suite·Ruff·diff/legacy SHA-256 검증.
- [x] 독립 code/Python review와 Stage 1 최종 완료 판정.

완성된 실행 코드나 통과한 테스트를 이 계획 문서가 대신하지 않는다.
