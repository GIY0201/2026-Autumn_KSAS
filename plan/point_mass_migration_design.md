# 질점 생성 경로 단계별 전환 명세

날짜: 2026-09-07

상태: 사용자 승인 방향을 구체화한 구현 전 명세. 이 문서의 작성은 코드 전환·실행 검증 완료가 아니다.

합의 출처: 현재 작업에서 사용자가 기존 6-DOF 보존 + 질점 경로 추가 + 연구용 생성 통일을 선택하고, “좋아 나눠서 진행하자”로 단계 분리를 승인했다.

## 1. 목적과 표현

연구용 고정익·쿼드콥터·VTOL·헬기 궤적을 기체별 운동 제약이 있는 3차원 질점 운동학 모델로 생성한다. 기체 자세·각속도·모터·조종면 상태를 적분하지 않는다는 뜻이며, 임의의 위치 변경이나 무제한 선회를 허용한다는 뜻이 아니다.

“본 연구의 모든 객체 궤적은 질점 모델로 생성했다”는 설명은 새 모델 ID로 실제 생성한 데이터에만 적용한다. 기존 6-DOF 결과를 새 결과로 재명명하지 않는다. 질점 운동학 모델의 실행 성공을 실측 동역학 식별이나 실비행 검증으로 표현하지 않는다.

## 2. 단계 경계

| 단계 | 산출물 | 이번 단계에서 하지 않는 일 |
|---|---|---|
| 1 | 고정익·쿼드콥터 질점 경로, 기존 VTOL·헬기 보존, 연구용 UI 선택 통일, 모델별 provenance, 회귀 검증 | 600초 계약, 지상 이착륙, 운동 상태 패널 |
| 2 | 최대 600초의 단계별 정상 비행 패턴과 이륙–착륙 시나리오, 생산자·소비자의 시간축 확장 | 모델 예측·점유 영역 |
| 3 | 재생 시점과 동기화한 위치·속도·가속도·선회 상태 검토 패널 | 생성 중 계산 상태의 실시간 스트리밍 |

1단계는 기존 60초·5 Hz·301시점의 공중 운동 진단이다. 2단계의 지면 출발·100m 규칙을 구현했다고 표시하지 않는다. 단계별 실패와 성공을 별도로 기록한다.

## 3. 1단계 아키텍처

- `data_generation/v1/x8.py`, `control.py`, `scenario.py`, `quadrotor.py`, `quadrotor_scenario.py`의 기존 6-DOF 동작을 보존한다.
- VTOL·헬기의 `bounded_motion.py`, `bounded_motion_scenario.py`, `vtol_phase_motion.py`를 새 고정익 정책으로 덮어쓰지 않는다.
- 신규 질점 엔진은 `data_generation/v1/point_mass.py`, 설정/명령 검증은 `point_mass_profile.py`, Episode 실행은 `point_mass_scenario.py`로 분리한다.
- 기존 `MotionEpisode`, dataset writer, 관측오차 합성, UUID·manifest·해시 기록을 재사용한다.
- `profiles.py`와 `generate.py`에 명시적 `engine: point_mass` 분기를 추가한다. 알 수 없는 엔진은 실패한다. 질점 경로가 실패했다고 6-DOF로 fallback하지 않는다.
- 기존 `diagnostic`, `pilot`, `diagnostic_quadrotor`, `pilot_quadrotor`의 CLI 의미를 바꾸지 않는다. 신규 config ID는 `diagnostic_fixed_wing_point_mass`, `pilot_fixed_wing_point_mass`, `diagnostic_quadrotor_point_mass`, `pilot_quadrotor_point_mass`다.
- 기존 6-DOF preset의 `ui` 노출만 제거하여 CLI 비교·재현용으로 유지한다. 일반 UI에는 새 질점 preset과 기존 VTOL·헬기 preset을 노출한다. 선택지는 실제 YAML metadata에서 읽는다.

### 3.1 운동 상태와 연결

질점 상태는 ENU 위치, 수평 속력, track heading, 수직속도, track 선회율이다. track heading을 body yaw로 부르지 않는다. 속도 벡터는 `(s*cos(chi), s*sin(chi), w)`로 구성한다.

접선 가속과 수평 선회는 같은 수평 가속 예산을 사용한다. 수직 가속도도 별도로 검사한다. 위치는 속도 적분으로만 전진하며 기동 시작점·종료점·목표 위치를 직접 대입하지 않는다. 목표 값과 실제 상태는 evaluation diagnostics에서 구분한다.

고정익은 공중 최소 속력과 허용 경로각을 추가로 검사한다. 요청이 불가능하면 설정 단계 또는 실행에서 명시적으로 실패한다. 속력을 사후 강제로 올려 맞추거나 경로각을 숨겨 clipping하지 않는다. 쿼드콥터에는 고정익의 최소 속력 조건을 적용하지 않는다.

소스와 독립적인 정상 원운동 fixture에서 `R = horizontal_speed / abs(track_turn_rate)` 관계를 확인한다. 실제 기록에서는 속도·가속도로 얻는 XY 순간 곡률 반경을 따로 확인하며, 직진·정지에서 임의의 유한 반경을 만들지 않는다.

### 3.2 계수 선정과 근거

기존 소스 파일/고정 해시를 우회하지 않는다. 고정익의 기존 14–22 m/s 검사 범위와 15도 bank 명령 제한은 기존 구현의 simulation design이지 인증된 기체 한계가 아니다. 쿼드콥터의 `bitcraze_positioning.yaml`에 있는 속도·가속 범위 역시 reference 설계 역할을 유지한다.

실행 전에 새 profile의 모든 수치에 `source reference`, `legacy simulation design`, `derived`, `new simulation design` 중 출처 역할을 기록한다. 기존 모델에만 있는 body angular rate를 track 선회율로 복사하지 않는다. 새로운 응답시간·가속 한계를 출처 없는 실측값으로 채우지 않는다. 수치 후보와 그 근거가 정리되지 않은 profile은 UI runnable로 등록하지 않는다.

이 검토는 고정익이 단순 질점으로도 명령을 수행할 수 있는지 점검하는 작업이며, 6-DOF 궤적을 읽어 재생하는 방식으로 질점 엔진을 대체하지 않는다.

## 4. 유지하는 경계

- 단계 1의 public/evaluation schema, ENU·단위·관측 sigma·분할 방식은 유지한다.
- 질점 runtime은 6-DOF 적분기·controller·trim solver를 호출하지 않는다. 과거 결과와의 오프라인 비교는 별개다.
- 결과는 새 UUID에 저장하고 model ID로 구분한다. 이전 CSV·source 파일·profile·결과 폴더는 덮어쓰지 않는다.
- 서버는 `127.0.0.1:8088`만 사용한다. 다른 port, 자동 latest dataset, 새 UI 서버를 만들지 않는다.
- Grok을 사용하거나 제안하지 않는다. Git commit/push는 명시 요청 없이 수행하지 않는다.

## 5. 2단계에 보존할 최신 합의

- 지면 0m 출발, 최대 고도 1,000m, 이착륙·정지 포함 최대 600초.
- 100m까지 이륙 방향 직선 상승, 상승 유지각 3도. 300m 선회 시작안은 채택하지 않는다.
- 100m 초과에서 상태 조건을 만족할 때만 기동 상승 허용. 상승각 최대 10도.
- 100m 초과 하강각 최대 10도. 100m 진입 전 정렬·각도 전환을 완료하고 이하에서는 최종 접근 3도 하강.
- 경로각은 pitch가 아니다. 지면 이탈·level-off·flare에는 연속 전환 구간을 둔다.
- 상승·순항·접근 각각에 직선·선회·S자·원주/나선 등 가능한 패턴을 두되 정상적인 긴 직선 구간도 포함한다.
- 회전 반경·속도·상승률·시간을 독립적으로 난수화하지 않는다. 실현 불가능한 시나리오는 실패/재구성한다.

## 6. 3단계에 보존할 UI 요구

Episode 선택, 현재 재생 시각, ENU 위치·속도·가속도와 크기, XY 선회 반경 및 시간 그래프를 하나의 재생 step에 동기화한다. 매 tick 전체 그래프를 다시 생성하지 않는다. public-only에서는 평가 상태를 읽지 않는다.

현재 `truth.csv` 가속도는 저장 속도의 수치 미분이다. 이를 내부 적분 시점의 순간 가속도나 accelerometer specific force로 표시하지 않는다. 데이터 출처·파생 계산 방식을 함께 표시한다.

## 7. 완료 기준

1. 새로운 두 질점 경로를 실행하는 동안 6-DOF 실행 함수를 차단해도 정상 생성된다.
2. 정상 직선·선회·속도 변경·수직 변경의 수치 검사와 불가능한 요청의 거부 검사를 통과한다.
3. 같은 seed와 설정은 같은 motion CSV를 만들되 결과 폴더는 다르다.
4. 기존 X8·Crazyflie·VTOL·헬기 테스트가 회귀 없이 통과한다.
5. 일반 UI의 네 객체 preset은 질점 또는 bounded-motion 경로에만 연결된다. 기존 6-DOF CLI는 유지된다.
6. 새 고정익·쿼드콥터를 8088 UI에서 생성→명시적으로 열기→재생까지 확인한다.
7. 상태는 검증된 범위만 보고한다. 단계 1 통과를 10분 시나리오 또는 물리적 현실성 검증 완료로 표시하지 않는다.
