# 실시간 비행 시뮬레이션 v1

작성일: 2026-09-08. 사용자 승인된 실시간 모델 검증 계획의 구현 안내다. 기존 Viewer·Training과 같은 앱에서 [Simulation](http://127.0.0.1:8088/simulation)을 연다. 별도 listener나 다른 port를 만들지 않는다. 기존 앱 실행 CLI의 명시적 `--dataset-path` 요구는 그대로 유지된다.

## 사용 순서

1. **설정**에서 수동/시나리오, 기체 종류·대수·초기 위치·방향, 지상/공중 출발, seed, 관측 sigma를 고른다. 초기 대수는 1이며 최대 16대다.
2. 모델을 검증하려면 catalog의 plugin/run/checkpoint를 명시적으로 고른다. 모델 없이 조종도 가능하다. 모델 검증이 끝나기 전에는 Start할 수 없다.
3. **운용**에서 Start한다. 설정 탭 이동·화면 포커스 상실은 Pause하며, 재개는 직접 실행한다. 실행 중 설정과 checkpoint는 고정된다.
4. 종료 후 결과 경로와 오차를 확인한다. Reset은 새 실행 ID를 발급하며 이전 결과를 덮어쓰지 않는다. PNG export는 현재 화면을 새 파일로 저장한다.

한 대를 수동 조종하면 추가 자동 기체는 생성하지 않는다. 여러 대에서는 선택한 한 대만 직접 조종하며 나머지는 시나리오를 수행한다. 조종 대상 변경 시 이전 기체는 현재 상태에서 유지 비행한다. 시나리오 모드의 선택은 관찰 대상만 바꾼다.

| 적용 모드 | 키 | 명령 |
|---|---|---|
| 고정익·CTOL | W/S | 목표 속도 증가/감소 |
| 고정익·CTOL | ←/→, ↑/↓ | 좌우 선회, 상승/하강 |
| 헬기·VTOL | W/S, A/D | 출력 증가/감소, 좌우 yaw |
| 헬기·VTOL | ↑/↓, ←/→ | 아래/위 pitch, 좌우 roll |
| VTOL 기체 | T | 반대 비행 모드로 천이 |

T를 받은 순간 목표 모드의 키 배치가 적용된다. 운동은 연속적으로 천이한다. 지상과 천이 도중의 추가 T 명령은 거부한다. 키를 놓으면 속도·출력 설정은 유지하고 방향·자세 명령은 중립으로 복귀한다. 반대 키 동시 입력은 중립이다. 조작은 기체 방향 기준이다.

고정익은 W로 활주한 뒤 충분한 속도에서 ↑로 이륙한다. 헬기·VTOL은 출력을 올려 수직 이륙한다. 접지 속도 기준을 만족하면 착륙, 초과하면 해당 기체의 충돌 종료로 구분한다. 이 기준과 자세 표현은 **실험용 간소화 운동 설정**이며 실제 기체 운용 한계나 6-DOF 검증 결과가 아니다.

시나리오 기본 카메라는 전체 외부 시점이다. 선택 기체 따라가기는 자유 관찰 추적이며 수동 조종 기본은 기체 뒤쪽·위쪽의 3인칭 추적이다. 카메라는 수평선을 유지하며 운동 상태를 바꾸지 않는다. 작은 XY 보기는 주변 객체 위치 확인용이다.

## 모델 입력과 결과 해석

공개 관측은 5 Hz 위치·timestamp·sigma·valid다. GRU feature에는 위치·상대시간을 사용하며 객체별 16개 연속 유효 관측 이후 75개 미래 위치를 예측한다. truth, 기체 종류, 조종 명령, 시나리오 미래는 feature로 전달하지 않는다. CPU가 기본이며 CUDA 요청 시 자동 CPU fallback하지 않는다.

별도 process의 추론이 늦어도 운동 clock은 기다리지 않는다. 이전 결과는 발행 시각을 유지하고, 대기 입력 갱신으로 건너뛴 횟수·지연·실패를 기록한다. 예측 목표 시각이 도달한 표본만 평가한다. 전체 15초가 확보된 예측만 전체 ADE/FDE에 포함한다. truth와 미래 noisy observation 기준 오차를 분리하며 객체별·발행 당시 phase별 결과와 horizon별 표본 수를 제공한다.

## HTTP 인터페이스

| Method·경로 | 용도 |
|---|---|
| GET `/api/simulation/catalog` | config 기반 기체·시나리오·검증 가능한 모델 목록 |
| GET `/api/simulation/state` | 실행 상태, 객체, 관측 이력, 예측, metrics |
| POST `/api/simulation/create` | 설정으로 새 실행 생성 |
| POST `/api/simulation/start`, `/pause`, `/resume`, `/stop`, `/reset` | 실행 제어 |
| POST `/api/simulation/select` | `object_id` 선택 |
| POST `/api/simulation/keys` | `object_id`, 전체 `keys` 상태 전달 |
| POST `/api/simulation/heartbeat` | 현재 owner의 연결 상태 갱신 |
| POST `/api/simulation/export` | `png` data URL 저장 |

POST는 JSON object와 로컬 Origin을 요구한다. 모든 요청은 브라우저의 UUID `client_id`를 포함하며 create 외에는 현재 `run_id`도 포함한다. keys의 `object_id`는 현재 선택 객체와 일치해야 한다. 다른 client·과거 run·과거 선택 대상은 409, 유효하지 않은 입력은 400, 외부 Origin은 403이다. 이 client ID는 로컬 탭 사이의 실행 격리 수단이며 사용자 인증 credential은 아니다.

상태는 `idle`, `ready`, `running`, `paused`, `stopped`, `completed`다. 추론 상태는 별도 `loading`, `ready`, `disabled`, `failed`로 표시한다. HTTP 또는 렌더링 호출은 simulation clock을 전진시키지 않는다. 현재 physics step은 0.02초이며 heartbeat timeout은 config의 1초다.

## 저장물

`outputs/data_generation/v1/<dataset_id>/`:

- `config.json`: 정규화된 실행·모델 선택. `settings.json`: resolved engine settings snapshot.
- Python replay helper는 `replay_events(config, events, until_step, settings=saved_settings)`로 snapshot을 명시적으로 적용한다. 저장 결과를 불러오는 UI 기능은 없다.
- `public/observations.csv`: object ID, 시간, 관측 XYZ, sigma, valid.
- `evaluation/truth.csv`: truth XYZ와 phase. `events.jsonl`, `predictions.jsonl`: 증분 기록. 정상 종료 시 전체 JSON도 저장한다.
- `manifest.json/csv`, `receipt.json`, `files.csv`: 실행 상태·추론 상태·model provenance·최초 source hashes·종료 결과·파일 SHA-256.

`outputs/visualization/v1/<run_id>/`:

- `metrics.json`, `metrics.csv`, `horizon_metrics.csv`: 평가 결과와 표본 수.
- `manifest.json`, `files.csv`: dataset 연결과 provenance.
- 고유 이름의 PNG와 JSON sidecar: 화면 저장 시점의 simulation state 및 PNG hash.

주기적으로 기록을 저장하며 종료 시 완료 receipt를 쓴다. runtime 오류는 failed로 구분한다. 프로세스 강제 종료로 최종 receipt가 없다면 마지막 증분 저장까지만 복구 근거가 있다. 기본 시뮬레이션은 평지·무풍이며 기체 간 충돌·회피, 지형, 실속·파손, 신규 학습과 occupancy 계산을 포함하지 않는다.

검증 수치와 한계는 [구현·검증 기록](../../plan/realtime_simulation_v1_implementation.md)을 따른다.
