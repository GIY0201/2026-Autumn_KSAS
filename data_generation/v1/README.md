# Data generation v1

현재 `corpus_*` 학습데이터 생성은 [데이터 생성 안내](../../plan/data_generation_current.md)를 먼저 읽습니다. 관측오차·식별 정보·분할별 파일과 실행 방법을 한곳에 정리했습니다. 아래의 기존 60초 경로와 참조 계산은 과거 단계 및 별도 기능의 기록입니다.

## 전체 이착륙 질점 생성기

2026-09-08: `full_flight.py`와 `full_flight_config.py`를 추가했다. `configs/full_flight_settings.yaml`에 부드러움 설정·고도·기동 목록을 두고 `configs/profiles/*_full_flight_v1.yaml`에서 객체와 참고 출처를 명시한다. 해당 source는 맥락용이며 실제 Gazebo 힘 모델을 실행하거나 데이터로 계수를 맞춘 경로가 아니다. 기존 integrator/controller를 수정하지 않고 속도·선회율을 연속 연결해 위치를 적분하는 별도 질점 경로다.

- 생성: `python -m data_generation.v1 --config-name full_flight_vtol scenario_id=V05-D seed=17`.
- 고정익 `full_flight_fixed_wing`의 F01-A~F16-D, 헬기 `full_flight_helicopter`의 H01-A~H16-F, VTOL `full_flight_vtol`의 V01-A~V16-D를 지원한다. 각 ID의 의미는 [시나리오 표](../../plan/aircraft_motion_scenarios.md)와 동일하다.
- 기본 UI preset은 기본 조합 한 개를 생성한다. `scenario_id=null` CLI override는 seed로 조합을 선택한다. 조합 변경이나 난수 선택이 시간/제약 실패를 숨기지는 않는다.
- 이륙~착륙 최대 600초·5 Hz, 동적 sample_count. 이전 60초 결과는 그대로 읽는다. 600초 초과/제약 초과는 명시적 실패이며 한도를 자동 상향하지 않는다.
- 가속도·jerk·속력·flare 높이 등 미확정 수치는 YAML에 실험용 설정으로 기록한다. 전체 시나리오에 연결한 것은 물리 성능 인증을 뜻하지 않는다.
- 시작·끝 정지, 고정익 지상 가감속, 헬기 80 m 수직 이륙·10 m 저속 착륙, VTOL 20초 출발 전환·80 m에서 10초 복귀 전환 및 잔여 감속을 포함한다. 착륙 위치는 누적 궤적의 종점으로 정하며 출발점 복귀를 강제하지 않는다.
- 사람이 읽는 시나리오 이름·선택 기동·seed는 evaluation commands에 기록한다. public Episode ID는 opaque UUID이며 미래 기동 정보를 넣지 않는다.
- [통합 구현 및 실행 기록](../../plan/scenario_generator_integration.md). 아래 설명의 60초 제한은 기존 경로에 해당한다.

상태: 연구용 일반 생성은 **고정익·쿼드콥터 제약 질점**과 기존 **VTOL·헬기 bounded-motion**을 사용합니다. v1에서 객체별 calm-air synthetic trajectory와 관측 위치를 만듭니다. 기존 X8·Crazyflie 6-DOF config와 결과는 CLI 비교·재현 경로로 보존하며, 새 point-mass runtime은 그것들의 integrator/controller/trim을 호출하지 않습니다. 실행 증거와 과학적 적용 범위는 [Stage 1 검증 기록](../../plan/point_mass_stage1_verification.md)을 확인하세요. 예측 모델·점유영역·GPS/지도·camera/영상 처리는 포함하지 않습니다.

추가 상태: 최신 사용자 지시로 쿼드콥터·VTOL·일반 헬기를 **같은 v1** 안에 통합합니다. VTOL·헬기는 내부 장치 재현이 아니라 연속 위치·속도의 제한된 운동모델이며, 기존 X8·쿼드콥터 수식·계수는 변경하지 않습니다. [간소화 모델 명세](../../plan/reduced_motion_v1_implementation.md)와 [원문 감사](../../plan/multi_object_source_audit.md)를 참조하세요.

[상위 지침](../AGENTS.md) → [v1 지침](AGENTS.md) → [설계 및 구현 기록](../../plan/data_generation_v1_design.md) → [구현 checklist](../../plan/x8_v1_implementation_checklist.md) 순으로 확인합니다.

## 구현 책임

| 모듈 | 책임 |
|---|---|
| `source.py` | DataverseNO X8 headerless CSV의 motion field reader, TRAIN/VALIDATION split과 MD5 provenance 검증. GPS 열은 읽지 않음 |
| `x8.py` | NED/body-FRD 내부 6-DOF, propulsion, actuator delay, RK4, NED↔ENU, calm trim |
| `control.py`, `scenario.py` | 위치 강제 없이 event-driven target을 controller로 추종하는 연속 60초 scenario |
| `motion_check.py` | TRAIN 13개 원본의 body velocity/rate/Euler/IAS/specific force 재적분 비교. `REPORT_ONLY` |
| `dataset.py` | `sigma_1m`, `sigma_3m`, `sigma_5m` 독립 Gaussian 위치 관측, split, manifest/hash, UUID 결과 보존 |
| `records.py` | 객체 중립 motion record와 source file/event 기록; 물리 모델 자체는 구현하지 않음 |
| `profiles.py`, `configs/profiles/` | 명시 모델/engine 선택, 원본 경로/SHA-256 검증, 출처 근거와 실험 설정 분리 |
| `quadrotor.py`, `quadrotor_scenario.py` | Crazyflie의 로터별 추력/반토크·관성·motor lag·연속 6-DOF 적분과 상태 응답 기반 waypoint 명령 |
| `point_mass.py`, `point_mass_profile.py`, `point_mass_scenario.py` | 고정익·쿼드콥터의 Local ENU 제약 질점 상태, profile/provenance 검증, 60초 MotionEpisode 생성. 6-DOF fallback 없음 |
| `bounded_motion.py`, `bounded_motion_scenario.py` | VTOL·헬기의 제한된 속도·벡터 가속·선회·수직 응답, 설정 기반 기동 순서와 연속 적분 |
| `generate.py` | Hydra entry point. 명시한 config만 실행 |
| `service.py` | UI용 공개 설정 목록·단일 background worker·진행 조회·중단·완료 검사. 별도 HTTP port 없음 |

X8 물리 적분은 2.5 ms, Crazyflie는 profile에 명시한 5 ms입니다. 두 모델 모두 controller는 10 ms, 저장은 0.2 s(5 Hz)이며 각 Episode는 60초·301 samples입니다. 새 Episode는 무풍이지만 source motion check는 원본 명령·초기 조건으로 재적분하므로 원본이 무풍이었다고 가정하지 않습니다.

## 실행

### 별도 Gazebo 기반 기동 범위 계산

2026-09-08 확장 시험은 `configs/motion_reference/gazebo_sweep_fixed_wing.yaml`, `gazebo_sweep_helicopter.yaml`, `gazebo_sweep_vtol_wing.yaml`, `gazebo_sweep_vtol_rotor.yaml`을 사용한다. 기존 force/controller 코드는 변경하지 않으며, 이 설정은 일반 UI 생성 preset이 아니다. 격자 끝점은 물리 성능 한계가 아니다. [범위·기동별 결과 안내](../../plan/gazebo_maneuver_ranges.md)를 따른다.

```powershell
.\.venv\Scripts\python.exe -m data_generation.v1.gazebo_reference_run --config data_generation/v1/configs/motion_reference/gazebo_sweep_fixed_wing.yaml --output-root outputs/data_generation/v1
```

다른 대상은 위 네 설정 중 해당 파일을 명시한다. 실행마다 새 UUID의 evaluation-only 참조 결과가 생성되며, 기존 60초 profile·8088 viewer·관측 데이터는 바꾸지 않는다.

### 명령 추종과 분리한 속력 상단 탐색

`gazebo_envelope.py`는 동일한 Gazebo 유래 힘 함수에서 속력·수직 조건·선회를 유지하는 힘 균형을 구한다. 기존 controller의 추종 오차를 모터/기체 최고 속력으로 취급하지 않는다. `gazebo_envelope_run.py`는 지정 기동의 전체 속력 격자를 확인하고 가장 높은 통과 구간을 좁혀, 경계 양쪽 값과 이전 시험의 미충족 이유를 새 evaluation-only 결과에 저장한다.

```powershell
.\.venv\Scripts\python.exe -m data_generation.v1.gazebo_envelope_run --config data_generation/v1/configs/motion_reference/gazebo_envelope.yaml
```

탐색 끝까지 균형이 있으면 `open_at_search_ceiling`, 균형을 찾지 못하면 `no_trim_found_on_scan`, 경계를 좁히면 `bracketed_trim_boundary`다. 마지막 상태도 수치적·조건부 경계이지 전역/실기체 성능 보장이 아니다. 헬기에는 동체 항력·동력 상한 등이 빠져 있어 높은 속력 결과를 운용 상한으로 바로 쓰지 않는다. [계산 결과와 쉬운 미충족 설명](../../plan/gazebo_speed_boundaries.md)을 참조한다. 기존 UI·질점 profile은 자동 변경하지 않는다.

### 기존 데이터 생성 실행

별도 평가용 전환 진단은 `python -m data_generation.v1.transition_probe --config data_generation/v1/configs/motion_reference/transition_probe.yaml`로 실행한다. 기존 질점 integrator에 점진적인 목표를 넣어 위치·속도·가속도와 jerk, 시간 간격 민감도를 검사한다. 출력은 새 UUID의 evaluation CSV이며 학습데이터나 일반 생성 preset이 아니다. 조건부 결과와 아직 미확정인 한도는 [전환 규칙 기록](../../plan/point_mass_transition_rules.md)에 있다.

```powershell
$env:UV_CACHE_DIR = "$PWD\temp\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\temp\uv-python"

# 고정익 제약 질점 진단 Episode 1개
uv run python -m data_generation.v1 --config-name diagnostic_fixed_wing_point_mass

# 쿼드콥터 제약 질점 진단 Episode 1개
# uv run python -m data_generation.v1 --config-name diagnostic_quadrotor_point_mass

# legacy X8 source motion check를 evaluation 영역에 함께 기록하는 진단 Episode 1개
uv run python -m data_generation.v1 --config-name diagnostic_with_motion_check

# 100 Episode fixed-wing point-mass pilot. 의도적으로 별도 실행을 요청할 때만 사용
uv run python -m data_generation.v1 --config-name pilot_fixed_wing_point_mass
```

`configs/diagnostic_fixed_wing_point_mass.yaml`, `pilot_fixed_wing_point_mass.yaml`, `diagnostic_quadrotor_point_mass.yaml`, `pilot_quadrotor_point_mass.yaml`은 연구용 일반 생성 설정입니다. 모든 설정은 `hydra.run.dir: .`로 설정되어 있어 코드 폴더에 Hydra output을 만들지 않습니다. 기존 `diagnostic.yaml`, `pilot.yaml`, `diagnostic_with_motion_check.yaml`, `diagnostic_quadrotor.yaml`, `pilot_quadrotor.yaml`은 CLI 비교·재현용 6-DOF config로 보존되지만 `ui` metadata가 없으므로 일반 Browser 목록에는 나타나지 않습니다.

legacy X8 config의 `identity_label`은 `X8 고정익 UAV`다. 이는 사람이 읽는 객체 식별용 public metadata이며, 각 실행의 UUID 기반 `episode_id`를 대체하지 않는다.

## Browser에서 생성

8088 viewer의 `데이터 생성` 영역에서 설정과 seed를 고르고 `데이터 생성`을 누릅니다. 일반 목록은 고정익·쿼드콥터의 명시 `point_mass` profile과 VTOL·헬기의 `bounded_motion` profile만 사용합니다. 각 객체의 `1개 미리보기`와 `100개 생성`은 같은 profile의 생성량·결과 분할을 구분합니다. 완료 후 `생성 결과 열기`를 누르면 새 dataset의 처음 시각으로 이동합니다. 생성 시작 자체는 현재 재생 데이터를 변경하지 않습니다.

고정익·쿼드콥터의 일반 Browser 항목은 다음과 같이 구분합니다. `diagnostic`/`pilot` 및 Crazyflie legacy CLI config ID와 실제 생성 동작은 변경하지 않았습니다.

| 화면의 연구용 설정 | 용도 | config ID |
|---|---|---|
| `고정익 UAV · 제약 질점 모델 · 1개 미리보기` | 60초 궤적 1개를 생성해 확인 | `diagnostic_fixed_wing_point_mass` |
| `고정익 UAV · 제약 질점 모델 · 100개 생성` | 같은 point-mass profile로 100개 생성 | `pilot_fixed_wing_point_mass` |
| `쿼드콥터 · 제약 질점 모델 · 1개 미리보기` | 60초 궤적 1개를 생성해 확인 | `diagnostic_quadrotor_point_mass` |
| `쿼드콥터 · 제약 질점 모델 · 100개 생성` | 같은 point-mass profile로 100개 생성 | `pilot_quadrotor_point_mass` |

원본 운동 비교와 기존 6-DOF CLI는 일상적인 연구용 생성에는 필요 없습니다. 삭제하지 않고 보존하지만 UI에 새 연구용 결과로 표시하지 않습니다. 독립 X8 참조 수치 추출은 [별도 `REFERENCE_SIMULATION` 기록](../../plan/x8_characterization.md)이며, point-mass profile을 자동으로 보정하지 않습니다.

기존 `.venv`가 준비된 PowerShell에서는 uv 명령을 거치지 않아도 됩니다.

```powershell
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name diagnostic_fixed_wing_point_mass seed=42

# 쿼드콥터 제약 질점 진단 1개
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name diagnostic_quadrotor_point_mass seed=42

# 쿼드콥터 제약 질점 100개가 필요할 때만 실행 (오래 걸릴 수 있음)
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name pilot_quadrotor_point_mass seed=42

# VTOL 또는 헬기의 진단 1개
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name diagnostic_vtol seed=42
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name diagnostic_helicopter seed=42

# VTOL 또는 헬기의 100개 pilot
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name pilot_vtol seed=42
& .\.venv\Scripts\python.exe -m data_generation.v1 --config-name pilot_helicopter seed=42
```

### 보존된 Crazyflie 6-DOF CLI 참조

Crazyflie legacy CLI는 `configs/profiles/crazyflie_eschmann_2024.yaml`의 공개 식별 계수를 재현합니다. 기본 reference는 `configs/motion_reference/bitcraze_positioning.yaml`의 Bitcraze `mocap00`/`mocap01` 분석에서 고정한 저속 geometry·nominal-speed 범위 안에서 seed별 새 3D rest-to-rest target을 만듭니다. 일곱 차 blend의 position/velocity/acceleration/jerk reference를 controller가 추종하지만 실제 위치·속도·자세는 계속 physical integration에서만 옵니다. reference 끝에서 실제 상태가 position/velocity tolerance를 충족하지 않으면 endpoint를 hold하며, 위치를 강제로 대입하거나 원본 track 조각을 복사하지 않습니다.

`mocap02`/`mocap03`은 held-out comparison 전용이며 geometry·threshold·gain을 고르는 데 쓰지 않습니다. marker-centroid analysis는 reference scale과 추종 context를 보일 뿐 raw-control replay, Eschmann 계수 재식별, 독립 실비행 검증이 아닙니다. 오래된 profile dictionary에 `motion_reference`가 없거나 명시적으로 `legacy_waypoints`를 선택하면 이전 waypoint experiment가 호환 경로로 유지됩니다.

원 notebook의 accelerometer 단위 변환 불일치와 Izz 추론을 포함한 제한은 [source README](../../data_sources/eschmann_crazyflie_2024/README.md)를 참조합니다. 현재 의미는 **공개 모델 재현**이지 독립 실비행 검증 완료가 아닙니다. 쿼드콥터의 진단 운동 범위는 수 m이므로 `sigma 3m`·`sigma 5m` 관측은 실제 이동보다 잡음이 크게 보일 수 있습니다. 보기에서 `sigma 1m`을 선택할 수 있으며 저장 계약의 오차 크기를 임의로 변경하지 않습니다.

새 reference target은 fixed polygon의 회전/scale 복사가 아니라 configured 3D bounds에서 seed별로 뽑고, 매 leg의 duration은 nominal mean speed와 analytic peak speed/acceleration limit을 함께 만족합니다. 이는 FPV 레이싱이나 일반 쿼드콥터 비행의 전체 분포를 대표하지 않습니다. 각 0.2초 관측에 독립 위치오차가 더해지므로, 실제 운동과 관측점의 튐을 구분해야 합니다. Viewer의 observation/current는 diamond, evaluation truth/current는 circle입니다. 실선 truth와 circle은 평가용 정답이며 관측 입력의 대체물이 아니고, public-only에서는 표시되지 않습니다.

### VTOL·헬기의 운동 수준 모델

VTOL의 새 Tal profile은 `experiment.execution_policy.id: vtol_phase_aware_v1`을 명시적으로 선택합니다. 이 opt-in 경로는 hover → 수직 climb → level-off → 전진 transition → cruise → seeded left **또는** right broad turn → cruise → back transition → hover → optional descent → final hover의 한 번짜리 순서를 사용합니다. 각 phase는 sampled minimum/reference time 이후에도 실제 speed·vertical speed·track turn-rate가 tolerance 안에서 configured dwell 동안 유지되어야 다음 phase로 넘어갑니다. timeout은 `failed`, 60초 관측창에서 아직 진행 중인 phase는 `episode_window_cut_off`으로 구분합니다. 완료 뒤에는 final hover reference를 유지하며 두 번째 takeoff를 시작하지 않습니다.

`reference_blend_s`, `settling_dwell_s`, `max_extension_s`와 mode duration/envelope은 모두 profile의 **engineering simulation settings**입니다. reference는 endpoint-flat blend로 speed·vertical speed·track-turn target을 연결하지만 위치·실제 speed·heading을 강제로 대입하지 않습니다. sampled turn target은 target speed와 함께 `abs(speed × track_turn_rate)`가 configured horizontal acceleration budget 안에 드는 교집합에서만 선택합니다. 불가능한 교집합은 clipping이나 unit 재해석 대신 simulation 전에 실패합니다. 수평 속도 변화와 선회는 계속 하나의 벡터 가속 한도를 공유하고, 수직 가속은 별도로 제한합니다.

헬기와 `execution_policy` selector가 없는 저장된 bounded-motion profile은 기존 time-window schedule 경로를 그대로 사용합니다. 헬기에 fixed-wing minimum-speed 조건을 적용하지 않습니다. VTOL final hover label은 작은 horizontal·vertical speed뿐 아니라 작은 track turn rate도 만족할 때만 붙습니다. 이동 방향은 track heading이며 기체 자세나 body yaw가 아닙니다.

새 모델의 적분 간격은 profile의 0.02초, 저장 간격은 기존 0.2초입니다. `experiment.modes`의 각 `mode_class`는 기동 종류이고, `mode_index_map`은 evaluation의 숫자 표기를 해석하는 대응표입니다. `target_tolerance_speed_mps`, `target_tolerance_vertical_speed_mps`, `target_tolerance_turn_rate_deg_s`는 source coefficient가 아닌 simulation event-label tolerance입니다. Tal Section VI-E의 8 m/s·3 s·2.7 m/s²·3.5 m 사례는 maneuver context일 뿐, 이 profile이 track transition dynamics를 fit하거나 해당 circle을 3 m/s² 한도에서 재현한다는 뜻이 아닙니다. body angular rate도 ground-track turn rate로 사용하지 않으며, `EVIDENCE_INFORMED_KINEMATIC` 상태는 independent real-flight validation으로 승격되지 않습니다.

[Tal–Karaman의 VTOL 비행 사례](../../data_sources/tal_tailsitter_2022/README.md)와 [NASA UH-60 공개 모델의 정상비행 자료](../../data_sources/nasa_uh60_1984/README.md)는 참고 근거입니다. 새 profile의 운동 한도·응답 시간·명령 분포는 모두 **명시적 시뮬레이션 설계값**입니다. `EVIDENCE_INFORMED_KINEMATIC` 표시는 원본 로그에서 계수를 fit했다거나 실기체를 검증 재현했다는 뜻이 아닙니다. 내부 장치 자료가 없다는 이유로 이 운동 수준 모델을 차단하지 않습니다.

계산은 서버와 같은 Python interpreter의 별도 process 하나에서 수행합니다. 같은 서버의 중복 실행을 거부하고, 실제 처리한 Episode 수 → source 점검(선택) → CSV 저장 → 형식 확인 순서로 상태를 표시합니다. 실패는 성공으로 표시하지 않으며 `생성 중단`은 해당 worker만 종료하고 기존 결과를 삭제하지 않습니다.

공개 orchestration API는 `GenerationService.submit(preset_id, seed)`, `snapshot()`, `results`, `cancel()`, `close()`입니다. UI는 이 인터페이스만 호출하고 X8 dynamics 내부를 호출하지 않습니다. preset 목록과 설명은 `configs/*.yaml`의 `ui`에서 읽고, UI seed는 0~4,294,967,295 정수만 받습니다.

`temp/generation-jobs/<job_id>/`에는 요청 설정, 상태 CSV와 worker 로그가 남습니다. Browser를 새로고침해도 실행 중인 같은 서버의 상태는 유지됩니다. 서버를 정상 종료하면 소유 worker도 종료되며 서버 재시작을 넘는 작업 재개는 지원하지 않습니다. 결과 CSV는 계속 `outputs/data_generation/v1/<dataset_id>/`에 보존됩니다.

## 출력 계약

매 실행은 `outputs/data_generation/v1/<dataset_id>/`의 새 UUID 폴더에만 기록합니다.

```text
public/episodes.csv
public/observations.csv
evaluation/truth.csv
evaluation/commands.csv
evaluation/triggers.csv
evaluation/diagnostics.csv
evaluation/source_motion_check.csv     # REPORT_ONLY config일 때만
manifest.csv
files.csv
effective_config.yaml
environment.txt
provenance.csv                       # 사용한 원본의 위치·SHA-256·URL
effective_model_config.yaml          # 객체별 물리/실험 설정 snapshot
```

`public/`는 정확히 `episodes.csv`, `observations.csv`만 허용합니다. truth·command·diagnostic은 evaluation 전용이며 public reader가 읽지 않습니다. `files.csv`는 자기 자신을 제외한 산출물의 SHA-256을 기록합니다.

새 객체는 `manifest.csv`의 `object_id`, `model_id`, `source_id`, `input_id`와 개별 source hash를 기록합니다. X8 record와 다른 객체의 request를 섞으면 저장 전에 거부합니다. UI도 요청한 객체/모델/출처와 결과를 대조합니다. 명시 profile은 source hash 검증을 통과해야 하며 X8 motion check를 다른 객체의 검증으로 재사용하지 않습니다.

## 확인된 실행과 한계

2026-09-07의 **142 passed in 97.47s**, scoped Ruff PASS, 독립 Python/통합 review APPROVE는 phase-aware VTOL 변경 **이전** reduced-motion 단계의 기록입니다. VTOL·헬기는 당시 각각 100개 pilot을 완료해 서로 다른 궤적100개와 70/15/15 split을 확인했습니다. 각각 UI/CLI 진단의 같은 seed 결과가 byte 단위로 일치하며 source·inventory·code hash와 수치 한계를 통과했습니다. 그 단계의 dataset ID와 정확한 범위는 [간소화 모델 실행 기록](../../plan/reduced_motion_v1_implementation.md)에 있습니다. 이번 phase-aware VTOL 실행·검증 범위는 [motion realism v1 실행 기록](../../plan/motion_realism_v1_implementation.md)을 확인합니다. Pilot 완료는 CLI 검사이며 Browser100개 완료를 별도로 주장하지 않습니다.

이전 쿼드콥터 단계에서 8088 Browser 진단 seed42를 두 번 생성한 `82819fa0-7f34-4539-9d5e-358a4f2f8afb`, `7cee3913-c9f3-48cb-a72e-4ddaee4a0915`는 서로 다른 폴더에 보존되며 public observations와 truth의 SHA-256이 각각 일치합니다. 각 결과는 903 observation rows·301 truth rows와 11개 inventory file hash 검사를 통과했습니다. 네 그래프 표시·Play/Pause·pilot worker 중단을 Browser에서 확인했습니다. 쿼드콥터 100개 전체 batch 완료는 이번 검증 범위가 아닙니다.

정식 생성 entry point는 시작 시 code hash를 캡처하고 저장 폴더 할당 전에 다시 대조합니다. 실행 중 생성/계약 코드나 설정 파일이 바뀌면 기존 결과를 덮어쓰지 않고 실패합니다. 기존 direct writer 호출의 `code_hash_at_start=None` 호환 경로와 구 결과물은 이 보장을 소급해 갖지 않습니다.

`ccc2b760-0b3e-46c9-a565-ff224e74f8df`는 실제 `diagnostic_with_motion_check` 실행 결과입니다. public contract, manifest, 903 public observation rows(301×3 variants), 301 truth rows, 140 motion-check rows를 확인했습니다.

X8 TRAIN 13개에 대한 motion check는 학습되지 않은 고정 모델의 비교 보고입니다. 현재 합격 기준은 없고 VALIDATION은 tuning에 사용하지 않았으므로, 이 수치를 field validity, safety guarantee, 다른 기체 일반화로 해석하지 않습니다.

테스트는 `tests/`에 있으며 UI 추가의 최신 검증 기록은 [생성 UI 실행 기록](../../plan/data_generation_ui_v1.md)을 참조합니다. 생성 CSV·원본 CSV·checkpoint·image는 이 코드 폴더에 저장하지 않습니다.
# 전체 시나리오와 학습 데이터 함께 생성 (2026-09-08)

8088 UI에서 `고정익 / 헬기 / VTOL · 모든 시나리오 + 학습데이터`를 선택하고
`데이터 생성`을 누른다. 현재 설정 기준 고정익 64 / 헬기 96 / VTOL 160 Episode를 생성한다.
고정익·헬기는 `repeats_per_scenario`와 catalog에서 생성량을 계산한다. VTOL은
`phase_direction_marginal_v1` schedule을 사용해 Train 112 / Validation 24 / Test 24로
고정하고, 각 split의 상승·순항·하강 행동과 선회 좌·우 수를 같게 만든다.
기존 `전체 이착륙 · 1개 미리보기`는 한 시나리오만 생성하는 별도 설정이다.

```powershell
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_fixed_wing
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_helicopter
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_vtol
```

각 새 dataset에는 다음을 함께 보관한다.

- `public/observations.csv`: 기존 5 Hz 관측 위치·timestamp·sigma·valid. 관측오차 3종.
- `public/episodes.csv`: 부모 Episode 단위 split. 일반 corpus는 70% TRAIN, 15%
  validation을 내림하고 나머지를 test로 둔다. 균형 VTOL corpus는 112/24/24를
  schedule에서 명시하고 writer가 전체 ID와 비율을 검증한다.
- `training/sequences.csv`: 공개 관측을 참조하는 학습용 구간 목록. 시작·끝 step은 양끝 포함.
- `training/balanced_windows.csv`: VTOL 행동 균형 corpus에서 GRU가 읽는 label-free 고정 index.
- `training/summary.csv`: split별 Episode·시퀀스·행 수.
- `evaluation/`: 실제 위치·속도·가속도, 기동 명령과 `behavior_balance.csv`. 학습 feature나 target에서 읽지 않는다.
- 기존 `manifest.csv`, `files.csv`, 설정·환경·출처 기록에는 학습 목록도 함께 추적한다.

일반 corpus의 `training.window_samples=null`은 **긴 관측 비행 전체**를 한 시퀀스로
보존한다. 균형 VTOL corpus는 GRU Direct v1 계약에 맞춘 91 sample window를 쓴다.
Train core는 12개 행동마다 256개이며 선회성 행동은 좌·우 128개씩이다. 행동 시작과
종료를 가로지르는 transition window는 별도 pool로 보존한다. Validation/Test는 기존
75-sample 고정 stride를 유지한다.

일반 corpus에서 길이를 명시하면 TRAIN 시작점은 seed로 무작위 추출하고
validation/test는 고정 stride로 선택한다. 균형 VTOL corpus의 TRAIN 시작점은
`balanced_windows.csv`로 고정한다. 동일 Episode의 모든 variant와 window는 같은 split에 남는다.
길이가 Episode를 초과하면 오류이며 조용히 건너뛰거나 패딩하지 않는다.

```powershell
# 101 samples is an explicit example (20 seconds), not a model contract.
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_vtol training.window_samples=101
```

학습 코드에서는 `iter_training_sequences(dataset_path, split="train")`를 사용하면
`(index_row, tuple[Observation])`를 얻는다. 이 reader는 위치 전용이며 식별 컬럼을 읽지 않는다. evaluation 파일은 읽지 않는다. 입력 timestamp는
원래 Episode 시각을 유지하며 어떤 상대좌표 변환/평활화도 자동 적용하지 않는다.
다른 실행의 동일 seed 복제본을 합쳐 split을 다시 섞지 않는다. 같은 시나리오 유형의 다른
비행은 여러 split에 존재할 수 있으므로 미관측 기동 유형 일반화 시험이라고 부르지 않는다.
# 생성 폴더 이름

고정익·헬기·VTOL `corpus_*` 생성 설정에는 식별 입력 3종이 포함됩니다. `training/train/acquired/observations.csv`처럼 각 분할 아래 `acquired`, `unknown`, `lost` 폴더를 확인하세요. 추가 컬럼은 `identity_known`, `observed_object_type`이며 미식별 시 실제 종류 대신 `unknown`을 저장합니다. 위치 전용 목록과 식별 확장 목록을 중복 합산하지 않습니다. 상세 규칙은 [식별 학습 입력](../../plan/identity_training_inputs.md)에 정리했습니다.

학습데이터를 요청한 새 생성 결과는 `training/train/`, `training/valid/`, `training/test/`에 각각 `observations.csv`와 `sequences.csv`를 저장합니다. 내부 split 이름 `validation`을 폴더명에서만 `valid`로 표시합니다. 진단 데이터는 별도 `training/diagnostic/`에 두며 빈 분할에는 CSV 헤더만 저장합니다. 같은 Episode와 관측오차 파생본은 기존 분할을 유지합니다. 전체 관측을 분할별로 저장하고, 학습 구간은 각 폴더의 시퀀스 목록으로 선택합니다. 이전 결과는 자동 수정하지 않습니다.

새 결과는 `객체설명_모드_개수episodes_seed값_고유접미사` 형식으로 저장합니다.
설명의 경로 금지 문자는 `_`로 정리하고 길이를 제한합니다. 같은 설정도 새 폴더에 저장하며 기존 결과는 덮어쓰지 않습니다.
과거 UUID 폴더는 계속 읽을 수 있습니다. `outputs/data_generation/v1`에는 진단·분석 결과도 있으므로 전체 삭제 대신 대상 dataset을 확인해야 합니다.
