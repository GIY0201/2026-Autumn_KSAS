# Gazebo 기반 독립 운동 계산·조합표 구현

날짜: 2026-09-07. 사용자 “그럼 빠르게 진행해봐”에 따른 구현 작업.

## 범위와 모델 경계

고정된 Gazebo SDF·`LiftDrag`·`MulticopterMotorModel` 식을 이용하는 독립 reduced-force 질점 운동 함수와 명령 추종 시험이다. 네 객체의 p/v/a 시계열, 속도·경로각·선회율·반경 조합표를 실제 계산하며 기존 UI 생성기/8088/관측 계약은 변경하지 않는다.

병진 운동은 `F / m - g`를 RK4로 적분한다. 항공기 자세·bank·받음각과 rotor thrust direction은 명시적인 시험용 응답 모델로 제어하며, 6-DOF 회전·joint mechanism은 이식하지 않는다. 헬기는 양력 plugin의 blade-local 유속을 사용한 방위각 평균 로터로 축약한다. 시험용 gains·tilt·회전 응답·명령 격자와 Gazebo source coefficient를 구분하며, 힘 계산으로 달성하지 못한 속도·위치·p/v/a를 강제로 대입하지 않는다.

Motor source semantics도 분리했다. thrust는 source link world pose가 회전한 local `+Z`이고, rotor drag는 joint world pose가 회전한 joint axis의 수직 성분이다. 따라서 RC Cessna와 Standard VTOL puller의 body-forward thrust와 body-up drag axis를 별도로 parse·저장한다. merged `x500` plugin과 `x500_base` geometry도 함께 해석한다.

## 구현 파일

- `data_generation/v1/gazebo_reference.py`: hash-checked source loading, force primitive, SDF frame-axis 해석
- `data_generation/v1/gazebo_trials.py`: prescribed-attitude controller, actual-force integration, trial failure/terminal semantics
- `data_generation/v1/gazebo_reference_run.py`: immutable evaluation-only CSV writer
- `data_generation/v1/configs/motion_reference/gazebo_trials.yaml`: source identity, controller/grid, `dt_s=0.02`
- `data_generation/v1/tests/test_gazebo_reference.py`: force/source/finite integration/failure preservation regression

원본은 `data_sources/gazebo_reference_2026`, 새 결과는 `outputs/data_generation/v1/<run_id>/`의 `evaluation/` 전용이다. `public/`은 만들지 않는다.

## 실제 실행 결과 — 2026-09-07

실행 ID: `519ff440-e158-484e-bcde-e35f57379593`

- 실행: `dt_s=0.02`, 120개 velocity/turn/climb/descent command 조합, 30초 trial, 0.1초 저장
- numerical status: **120/120 complete**, `numerical_failures=0`
- terminal 3초 hold의 `target_met`: **72/120**. 이는 recovery cycle 또는 실제 운용 envelope의 합격 판정이 아니다.
- 시계열: `evaluation/samples.csv` 36,120 rows; 각 행의 position/velocity/acceleration finite 확인
- integrity: source·code·config이 실행 중 변하지 않았고 (`files_unchanged_during_run=True`), `files.csv` 11개 hash 재검증에서 mismatch 0

| 모델/모드 | complete | terminal target_met |
|---|---:|---:|
| RC Cessna wing | 27/27 | 7/27 |
| X500 rotor | 21/21 | 12/21 |
| Standard VTOL wing | 27/27 | 14/27 |
| Standard VTOL rotor | 21/21 | 15/21 |
| Standard VTOL transition | 3/3 | 3/3 |
| helicopter rotor | 21/21 | 21/21 |

대표 terminal-window mean은 `evaluation/combinations.csv`에 저장했다. 예를 들어 Cessna straight 15 m/s는 14.443 m/s, -0.145 m/s vertical, 거의 0 deg/s이고 target을 충족했다. Cessna 15 m/s·15° bank는 14.600 m/s, -0.715 m/s vertical, 10.813 deg/s, 반경 77.269 m로 numerical complete이지만 terminal target은 미충족이다. X500 5 m/s·10 deg/s는 4.587 m/s, 0.026 m/s vertical, 10.012 deg/s, 반경 26.251 m이고 target을 충족했다. Standard VTOL은 wing 15 m/s·15° bank에서 14.620 m/s, -0.027 m/s vertical, 10.358 deg/s, 반경 80.872 m이며 target을 충족했고, rotor 5 m/s·10 deg/s에서는 4.744 m/s, 0.009 m/s vertical, 10.008 deg/s, 반경 27.156 m로 target을 충족했다. 헬기 같은 명령은 5.026 m/s, -0.002 m/s vertical, 10.004 deg/s, 반경 28.787 m이며 target을 충족했다.

이전 `2067bf6e-755e-4f03-9d0c-c5b0e7ae3767` run은 보존한다. 그 결과의 Cessna 27개 `dt_s=0.05` numerical instability는 이 fresh `dt_s=0.02` run으로 대체된 **수치 적분 설정 문제**이며, 기체의 physical failure로 해석하지 않는다.

## 검증 상태와 한계

- **software verification — complete:** source hash 검증, source-axis regression을 포함한 focused test `16 passed`, scoped Ruff PASS, 새 120-case CSV run과 inventory hash 확인.
- **Gazebo source motion check — partial:** pinned source의 식·계수·frame axis만 독립 계산에 사용했다. Gazebo/PX4 runtime은 설치·실행하지 않았다.
- **과학적/운용 일반화 — 미확정:** source coefficients와 reduced prescribed attitude/controller/grid는 실제 비행 envelope, full Gazebo re-simulation, sensor/field validation 또는 safety guarantee가 아니다.

`target_met`은 entry 뒤 terminal 3초 hold에 대해 speed/path-or-vertical/turn-or-bank tolerance를 모두 만족한지의 진단이다. `status`와 `recovery_complete`를 별도 보존하므로, numerical completion·terminal tracking·recovery를 하나의 성공 주장으로 합치지 않는다.
