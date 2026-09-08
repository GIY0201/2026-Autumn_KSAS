# 실시간 비행 시뮬레이션 v1 구현·검증 기록

작성일: 2026-09-08. 합의 출처: 현재 작업의 사용자 승인 계획 `PLEASE IMPLEMENT THIS PLAN: 학습 모델 검증용 실시간 비행 시뮬레이션 v1`와 앞선 조종·천이·카메라·접지 논의. 이 문서는 구현과 실행 검증 기록이며 연구용 motion 설정을 실제 기체 안전성으로 승격하지 않는다.

## 구현 범위

`관측 생성 → 객체별 16개 이력 → 저장 checkpoint 추론 → 미래 시각 도달 후 평가`를 기존 8088 앱의 `/simulation`에 연결했다. 화면은 설정/운용 두 탭이며 기존 Viewer·Training 페이지는 유지한다.

- 운동과 clock: `data_generation/v1/simulation_engine.py`, 서비스·증분 저장: `simulation_service.py`.
- 모델 계약: `contracts/v1/simulation.py`, 별도 process 추론과 평가: `models/runtime/v1/simulation_inference.py`.
- 페이지·HTTP: `visualization/v1/simulation_routes.py`, 로컬 Three.js 자산을 포함한 `simulation_web/`.
- 설정: `data_generation/v1/configs/simulation_settings.yaml`. 기존 full-flight 설정을 resolved snapshot에 포함한다.

수동 단일/다중 기체, 시나리오, 지상·공중 출발, VTOL 즉시 조작 모드 전환과 연속 천이, 정상 접지·충돌 구분, 카메라 세 모드, checkpoint 고정 및 입력 경계가 구현되었다. 자세 표시는 간소화 조종 cue다. 자세·rotor·실속을 포함한 고충실도 동역학이나 회피 기능은 범위 밖이다.

사용법·API·artifact schema는 [Simulation 안내](../visualization/v1/SIMULATION.md)에 기록한다.

## 확인된 실행 증거

독립 offline verification artifact:

`outputs/visualization/v1/dc83a3ae-f6da-404f-8ef6-426c70933765/`

여기에 `verification.json`, `benchmark.csv`, `event_replay.json`이 있다. 각 benchmark 실행은 별도 dataset/run ID와 public/evaluation 결과를 남겼다. 실제 checkpoint는 `20260908T111823_97a15ae85379`의 명시적 `best`, device는 CPU다. 신규 학습·TRAIN/VALIDATION/TEST split 평가는 수행하지 않았다.

| 대수 | 모델 | simulation 길이 | engine tick 평균 / p95 | 추론 batch 평균 | 예측 수 / 전체 horizon 평가 수 |
|---:|---|---:|---:|---:|---:|
| 1 | 없음 | 20초 | 0.074 / 0.114 ms | 해당 없음 | 0 / 0 |
| 1 | GRU | 20초 | 0.131 / 0.205 ms | 5.650 ms | 86 / 11 |
| 16 | 없음 | 20초 | 0.759 / 1.153 ms | 해당 없음 | 0 / 0 |
| 16 | GRU | 20초 | 1.097 / 1.495 ms | 6.556 ms | 1376 / 176 |

20초 simulation을 고정 step으로 전진시키면서 실제 AsyncPredictor process를 사용했다. tick 수치는 engine.advance 구간이며 화면·HTTP·저장·전체 loop 지연을 포함하지 않는다. 추론 평균은 결과에 기록된 batch latency를 객체별 결과에서 집계했다. 네 실행에서 runtime error와 skipped 요청은 0이었다. 출력 shape `(75,3)`와 finite 검사 및 성숙한 horizon 평가를 확인했다. 이 수치는 단일 환경의 관측값으로 다른 CPU/GPU에서 동일 성능을 보장하지 않는다. Browser FPS 또는 wall-clock 실시간 deadline 보장으로 해석하지 않는다.

각 기체의 기본 시나리오는 다음처럼 완료됐다.

| 기체 | 완료 simulation 시각 | 마지막 phase |
|---|---:|---|
| 고정익 | 341.8초 | landed |
| 헬기 | 410.8초 | landed |
| VTOL | 409.8초 | landed |

세 실행 모두 600초 이내 종료하고 마지막 truth 고도 절댓값은 `1e-8 m` 미만이었다. 기록된 입력 이벤트의 적용 step을 재생한 500-step VTOL 실험에서 snapshot과 공개 관측 배열이 정확히 일치했다. 이 확인은 기본 시나리오 세 개와 지정 입력 시퀀스에 대한 결과이며 모든 시나리오 조합의 검증을 의미하지 않는다.

서비스·라우트 focused tests 7개가 통과했고 해당 파일의 Ruff 검사가 통과했다. heartbeat로 clock 정지, 자동 종료 receipt, public/evaluation 분리, reset ID, Origin·client·run·선택 대상 격리가 포함된다. 전체 suite는 첫 통합에서 412 passed였고, 재현성 보완 후 동시 Training 작업의 신규 진단 테스트가 작성 중인 시점에 430 passed / 3 failed를 기록했다. 해당 파일 완성 후 실패한 3개는 모두 재실행 통과했다. 최종 통합 재실행 결과는 아래에 별도로 기록한다.

브라우저에서 1·16대, 모델 연결 유무, 고정익·VTOL 지상 이륙, 양방향 천이, 헬기 착륙·충돌, PNG·종료 receipt를 확인했다. 상세 실행 ID와 측정값은 [Browser 확인 기록](realtime_simulation_v1_browser_checks.md)을 따른다.

## provenance와 재현성

새 실행은 resolved engine settings를 `settings.json`에 저장하고 실행 시작 시 source hashes를 고정한다. 실행 config에는 checkpoint SHA-256이 고정되고 normalization·effective model config의 hash를 별도로 기록한다. observation noise seed와 입력의 적용 simulation step을 보관한다. 최종 receipt는 inference 상태·error·skipped·latency를 포함한다.

Benchmark의 `verification.json`에는 당시 source hashes가 있다. 이후 추가된 settings snapshot·horizon CSV·PNG provenance 보완은 해당 benchmark 당시 결과와 분리해서 해석한다. 재현은 동일 source/dependency 상태와 settings를 명시적으로 사용해야 한다. `replay_events(config, events, until_step, settings=saved_settings)`로 저장한 resolved settings를 전달할 수 있다. 기본 설정이 변경된 경우에도 저장 settings를 사용한 replay가 같은 snapshot을 복원하는 회귀 검사를 추가했다. 사용자용 replay 버튼/CLI는 이번 인터페이스에 포함되지 않았다.

추가 provenance 보완으로 full-flight 생성·config 코드, GRU model·plugin, runtime registry도 최초 source hash 목록에 포함했다. 이 보완 후 engine·service·route focused tests **30개가 통과**했다. 전체 suite 상태와 혼동하지 않는다.

## 상태 구분과 한계

최종 통합 명령 `.venv/Scripts/python.exe -m pytest -q --basetemp=temp/sim-final-integrated` 결과: **435 passed, 8 warnings, 165.75초, exit 0**. 경고는 Transformers checkpoint mtime 대신 numerical ordering을 사용하는 기존 경고다. 이번 시뮬레이션 Python 코드·테스트·app 통합 파일의 Ruff도 통과했다. 최종 read-only Python/UI 검토에서 추가 blocker는 발견되지 않았다.

- **소프트웨어:** 최종 전체 suite 435개·focused tests·offline 통합·Browser 조작 검증 통과. 이전 전체 suite의 동시 작업 영향은 위 기록에서 구별한다.
- **실제 checkpoint 연결:** 지정 CPU checkpoint를 로드하여 1·16대의 75-step 예측과 시각 정렬 평가를 확인했다. 모든 checkpoint·CUDA 검증을 뜻하지 않는다.
- **모델 성능:** 오차가 계산·저장되는 기능을 검증했다. 이 실험은 합성 조종 궤적이며 held-out 일반화 평가가 아니다. VTOL 기반 학습 모델의 고정익·헬기 결과는 학습 분포 변화 조건으로 구분해야 한다.
- **물리·안전:** 실험용 질점·bounded kinematics이며 실측 식별, formal Reachability, 실제 항공기 안전 한계 검증이 아니다.
