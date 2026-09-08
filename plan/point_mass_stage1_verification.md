# 제약 질점 Stage 1 실행·검증 기록

날짜: 2026-09-07  
상태: **complete** — 구현·실행·최종 통합 regression·독립 code/Python review 완료.

## 범위와 판정 경계

이 기록은 [Stage 1 계획](point_mass_stage1_plan.md)과 [전환 명세](point_mass_migration_design.md)의 고정익·쿼드콥터 제약 질점 경로만 다룬다. 두 경로는 Local ENU, 60초, 5 Hz, 시작점을 포함한 301 samples의 `MotionEpisode`를 만든다. 기존 X8·Crazyflie 6-DOF CLI와 VTOL·헬기 bounded-motion 코드·config·결과는 보존 대상이다.

| 항목 | 상태 | 해석 |
|---|---|---|
| 제약 질점 kernel/profile/runner | 확인됨 | 고정익·쿼드콥터는 명시 `engine: point_mass`로만 연결되며 6-DOF fallback이 없다. |
| public/evaluation 경계와 새 UUID 저장 | 확인됨 | 새 결과가 public observations 903행, evaluation truth 301행을 갖는지 실제 생성으로 확인했다. |
| 8088 Browser 생성·선택·재생 | 확인됨 | 두 새 preset에서 실제 생성 후 XY/XZ/YZ/3D와 Play/Pause를 확인했다. |
| legacy 코드/결과 보존 | 확인됨 | 기준 SHA-256 8개가 모두 byte-identical임을 최종 재대조했다. |
| 전체 회귀·lint | 확인됨 | historical Stage 1 run 209 passed와, source 동결 뒤의 최종 통합 run 226 passed가 모두 exit 0이다. Ruff와 `git diff --check`도 exit 0이다. |
| 독립 code/Python review | 확인됨 | 두 독립 reviewer 모두 `APPROVE`, unresolved finding 0건이다. 리뷰에서 재현된 경계 결함은 TDD로 수정하고 재검토를 받았다. |

## 구현 범위

- `point_mass.py`: ENU 위치, 수평 속력, track heading, 수직 속도, track turn-rate 상태와 공유 수평 가속 예산을 적분한다. 모든 수치 경로에 finite/boundary sanity assertion이 있다.
- `point_mass_profile.py`: object/model/engine/provenance/명령/적분격자/제약을 검증한다. profile 수치 역할은 `source_reference`, `legacy_simulation_design`, `derived`, `new_simulation_design`으로 명시한다.
- `point_mass_scenario.py`: 명시 command schedule을 0.02초로 적분하고 0.2초 grid로 저장한다. finite command의 실제 완료와 마지막 60초 window 종료를 별도 event로 기록한다.
- 새 config: `diagnostic_fixed_wing_point_mass`, `pilot_fixed_wing_point_mass`, `diagnostic_quadrotor_point_mass`, `pilot_quadrotor_point_mass`.
- 기존 X8/Crazyflie 6-DOF config에서는 `ui` metadata만 제거했다. CLI config ID, profile ID, mode, episode count, source-motion-check 의미는 test로 보존한다.

수치 근거·hash·해석 제한은 [profile 근거표](point_mass_profile_basis.md)에 있다. 14–22 m/s와 bank 기반 계산은 legacy simulation design/derived convention일 뿐 실기체 한계가 아니다. `mocap02`/`mocap03` 또는 X8 TRAIN/VALIDATION을 profile tuning에 사용하지 않았다.

## 실행 증거

### 변경 전 기준선

작업 시작 시 사용자 제공 untracked 계획 파일은 `point_mass_migration_design.md`, `point_mass_stage1_plan.md` 두 개뿐이었다. 다음 명령은 변경 전 성공했다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-point-mass-baseline
# 167 passed in 102.14s, exit 0
```

### focused test

아래는 구현 중 실제 통과한 focused 실행이다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_point_mass.py
# 10 passed in 0.14s, exit 0

.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_point_mass_generation.py
# 11 passed in 1.75s, exit 0

.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_service.py data_generation/v1/tests/test_object_support.py visualization/v1/tests/test_generation_ui.py
# 42 passed in 7.69s, exit 0
```

### CLI diagnostic artifact

각 실행은 이전 output을 덮어쓰지 않는 새 UUID이다. 명령 뒤 `validate_public_dataset(Path(...))`로 public contract를 재확인했다.

| config / seed | dataset ID / model | public observations SHA-256 | evaluation truth SHA-256 | 상태 |
|---|---|---|---|---|
| `diagnostic_fixed_wing_point_mass`, `42` | `b174d46e-7b1f-4a60-87f9-131094a7bcd9` / `fixed_wing_point_mass_v1` | `04F472C10607709BEC5B3A59281DD1096EA50D74EED59BA596551CB7E601C87C` | `9C6F66EC5CE3C2512FBC728FAC9C4BDA50FD767328678D10B512A95E2EB693F9` | 보존됨. ENU 좌·우 sign 수정 전 결과이므로 fixed-wing 방향 증거로는 superseded. |
| `diagnostic_fixed_wing_point_mass`, `17` | `9625c33d-5324-4596-a2fb-c75a48c3ff10` / `fixed_wing_point_mass_v1` | `ADCB72246B9A77BEB88D18D5CAFDC46C98469FD68BEF1A5CF3C543FF1DE9124C` | `F307F474719296154079DD91742C19DDECE7C2B010DBCB52D9D6EF5BAA8EAB07` | sign 수정 후 fresh CLI 결과. public contract `PASS`, observations 903행, truth 301행. |
| `diagnostic_quadrotor_point_mass`, `42` | `8a836101-5bf2-4bff-9473-14fecc0753ca` / `quadrotor_point_mass_v1` | `06FD3235B4CF8A391AAC695CD58FA549454E01A056077A588F5A85BEEE4D458B` | `E014F2D0D13A176F86128F12CB5523EE22930B17142544A3DDAADED00EFD87F1` | 유효. |

각 artifact는 public observations 903행(301 time points × 3 sigma), evaluation truth 301행이다. fixed-wing은 commands 7행, quadrotor는 commands 6행이다. sign 수정 후 fixed-wing seed 17은 command index 1에서 heading 변화 `+0.51875309560935 rad`, lateral displacement `+24.65107197297705 m` (left), command index 4에서 `-0.499540017994184 rad`, `-24.16836198152837 m` (right)를 실제 적분으로 확인했다.

### Browser, 8088 한정

기존 8088 listener는 먼저 command/dataset path로 프로젝트의 scope-confirmed viewer임을 대조했다. 기존 dataset `9f5bd82d-20c7-47d2-9d01-688dea9c5ead`를 수정하지 않은 채 해당 listener만 종료했고, 새 viewer 하나를 `127.0.0.1:8088`로 시작했다. 다른 listener 또는 port는 만들지 않았다.

Browser에서 다음을 실제 확인했다.

| preset / seed | Browser-generated dataset | 선택 Episode | 재생 확인 | 상태 |
|---|---|---|---|---|
| `diagnostic_fixed_wing_point_mass`, `17` | `554ff1e0-8e4b-4a80-bb33-97e63157e581` | `fixed_wing_point_mass_v1-3302413169` | XY/XZ/YZ/3D 표시, Play step 0→20, Pause step 44 | 보존됨. ENU 좌·우 sign 수정 전 Browser evidence이므로 fixed-wing 방향 검증에는 superseded. |
| `diagnostic_fixed_wing_point_mass`, `17` | `178c8a7a-8006-43db-ac4d-81d8d81e51fe` | `fixed_wing_point_mass_v1-3302413169` | XY/XZ/YZ/3D 표시, Play step 0→31 (6.2초), Pause step 56 (11.2초) | sign 수정 후 fresh Browser 결과. public contract `PASS`, observations 903행, truth 301행; CLI seed 17과 public/truth byte-equivalent. |
| `diagnostic_quadrotor_point_mass`, `17` | `56b3a89a-26fb-4ac4-9080-b918f74d7a9a` | `quadrotor_point_mass_v1-3302413169` | XY/XZ/YZ/3D 표시, Play step 0→30, Pause step 51 | 유효. |

sign 수정 전 테스트 viewer와 해당 임시 log만 종료·정리했다. Browser-generated dataset은 output root에 보존한다.

### 기존 8088 viewer 복원

Browser 검증을 마친 뒤 원래 프로젝트 dataset `9f5bd82d-20c7-47d2-9d01-688dea9c5ead`를 명시적으로 여는 viewer를 다음 인자로 Hidden 방식으로 복원했다. 다른 listener나 port는 생성하지 않았다.

```powershell
.\.venv\Scripts\python.exe -m visualization.v1 --dataset-path outputs\data_generation\v1\9f5bd82d-20c7-47d2-9d01-688dea9c5ead --port 8088
```

- launcher PID: `37696`; 실제 `127.0.0.1:8088` listener child PID: `42792`.
- `netstat` 확인에서 `127.0.0.1:8088 LISTENING`은 정확히 1개였고, `GET /`는 HTTP `200`이었다.
- 실행 중 viewer log는 [stdout](../temp/restored-8088-viewer.stdout.log) 및 [stderr](../temp/restored-8088-viewer.stderr.log)에 보존한다. 이 실행 중 log는 temporary cleanup 대상에서 제외한다.

### 최종 technical verification 및 독립 review

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-point-mass-final
# 209 passed in 107.35s (0:01:47), exit 0

.\.venv\Scripts\python.exe -m ruff check contracts data_generation visualization
# All checks passed!, exit 0

git diff --check
# output 없음, exit 0
```

위 `209 passed`는 review 수정 및 공유 X8 characterization 변경을 모두 합치기 전의 historical Stage 1 실행 기록으로 보존한다. 최종 완료 판정은 source 동결 후 root가 실행한 다음 통합 결과에 둔다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-x8-characterization-final-suite
# 226 passed in 118.98s (0:01:58), exit 0
```

리뷰 finding 수정 뒤 구현자가 실행한 point-mass focused run은 `30 passed in 1.80s`였고, 같은 수정 파일 Ruff는 `All checks passed!`였다. 독립 code reviewer와 independent Python reviewer는 각각 read-only focused `27 passed, 3 deselected`를 재실행한 뒤 모두 `APPROVE` / unresolved finding 0건을 판정했다. code reviewer는 `git diff --check` `PASS`를, Python reviewer는 scoped Ruff `PASS`를 별도로 확인했다. 두 reviewer가 확인한 수정 범위는 ENU fixed-wing 좌·우 sign, 현재·다음 state의 path-angle guard, current/transition shared horizontal budget, fixed-wing positive minimum speed, initial range budget, integration epsilon guard다. Python review는 legacy runner를 차단한 두 diagnostic config가 각각 complete 301행을 만들고 같은 seed 반복의 state sequence가 완전히 일치함을 확인했다. 재검토 snapshot 및 문서 갱신 뒤 현재 SHA-256은 `point_mass.py` `ADAD1D5CE8F7DF25C3F6C6E3EA260F5017C9655D744EDCA9B4D0253311190A3F`, `point_mass_profile.py` `965662A986CE13854335AD3392F303378BBF5A0CA0D2B34B51C546E43290417F`, `fixed_wing_point_mass_v1.yaml` `8C0E776333FD0B204031F740D26D07D3F28585C3ECA1A59F1D1B44B79B1AC898`로 일치한다.

보존 SHA-256 재대조는 `x8.py`, `control.py`, `scenario.py`, `quadrotor.py`, `quadrotor_scenario.py`, `bounded_motion.py`, `bounded_motion_scenario.py`, `vtol_phase_motion.py`에 대해 기준값과 비교했고 `legacy-sha256-preservation=PASS`였다. 최종 `git status --short`에는 이 Stage 1 변경 외에 별도 X8 characterization/decision 작업의 공유 working-tree 변경도 있었으며, 그 파일은 이 기록의 point-mass 변경으로 취급하지 않는다.

## 보존·제외 사항

- 비교 SHA-256 대상은 `x8.py`, `control.py`, `scenario.py`, `quadrotor.py`, `quadrotor_scenario.py`, `bounded_motion.py`, `bounded_motion_scenario.py`, `vtol_phase_motion.py`이며 기준 값은 [profile 근거표](point_mass_profile_basis.md)에 있다.
- `PX4`, `Gazebo`, WSL, 새 runtime dependency를 설치하거나 Stage 1 실행에 추가하지 않았다. Gazebo reference-data 조사/설계는 별도 작업이고 Stage 1의 validated evidence가 아니다.
- [독립 X8 참조 운동 수치 추출](x8_characterization.md)은 `REFERENCE_SIMULATION`/`REPORT_ONLY` 후속 작업이다. 이를 실행 전 수치 근거나 자동 profile overwrite로 쓰지 않는다.
- Stage 2의 600초·지면 이착륙·100 m/3도/10도 규칙, 선택 기동의 smooth entry/exit, Stage 3 inspector는 구현하지 않았다.
- 지정된 read-only OpenAI guidance 경로 `C:\Users\USER\workspace\vaults\openai-guidance\wiki\current-guidance.md`는 작업 시점에 존재하지 않았다. 누락 파일을 만들거나 변경하지 않았다.

## 최종 판정과 제한

Stage 1은 `complete`다. 이 판정은 최종 통합 suite 226 passed, Ruff 및 `git diff --check` 통과, legacy SHA-256 보존, sign 수정 후 fresh CLI/Browser artifact, 그리고 두 독립 reviewer의 `APPROVE` / unresolved 0건에 한정된다.

이는 6-DOF 실기체 특성화, PX4/Gazebo/WSL 검증, Gazebo reference-data 도입, profile 자동 tuning 또는 물리적 일반화 검증을 의미하지 않는다. 해당 항목은 별도 후속 범위다.
