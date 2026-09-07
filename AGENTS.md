# AGENTS.md — 2026 KSAS 공통 지침

## 작업 시작

- 이 프로젝트는 독립 연구 공간이다. `2026_autumn_KSAS/occupancy_v2`의 코드·계약·설계를 자동 적용하지 않는다. 과거 프로젝트는 사용자가 요청한 비교에만 사용한다.
- 현재는 설계·문서와 빈 기능 폴더에서 시작한다. 설계 정리만으로는 코드·schema·설정 구현, 설치, 데이터 생성, 학습을 시작하지 않는다. 단, 아래 `X8-GEN-V1 실행 목표`는 사용자가 2026-09-07에 명시적으로 구현 승인한 예외다.
- 새 파일 전에 [README.md](README.md), [배치 규칙](plan/project_structure.md)을 읽는다. 이어서 작업 경로상의 상위→하위 `AGENTS.md`와 해당 버전의 `README.md`, 거기서 지정한 설계 절을 읽는다.
- 합의 확인이나 추가 질문 전 [결정 대조표](plan/decision_reconciliation.md)를 읽는다. 문서 누락을 미확정으로 해석하거나 확정값을 새 선택지로 다시 제안하지 않는다.
- 표준 지침 파일명은 `AGENTS.md`이다. `AGENT.md`, `Agent.md`, `AGNET.md`를 따로 만들지 않는다.
- 하위 지침은 해당 폴더와 자손에 적용되는 추가 규칙이다. 공통 규칙·사용자 합의·도메인 불변조건을 덮어쓰지 않는다. 모듈 경계를 바꾸는 작업은 관련 양쪽 지침과 `contracts/AGENTS.md`를 읽는다.

## X8-GEN-V1 실행 목표

- 목표: 2025 X8 nonlinear 6-DOF 근거를 사용해 새 60초 3D Episode를 생성하고, 축별 `sigma=1, 3, 5 m` 관측본을 저장하며, Local Browser에서 XY/XZ/YZ/3D 재생과 PNG export를 제공한다.
- 입력 경계: upstream observer가 이미 수치 XYZ 위치를 준다고 가정한다. GPS 위경도·지도 복원·GPS 오차/융합·카메라 보정·영상 검출·깊이 추정은 구현하지 않는다.
- 범위 제외: RED-SDS/GRU 등 예측 모델 구현, 점유영역 계산, 다른 기체, 다물체 상호작용, 지형·건물·충돌 회피는 이번 v1에 넣지 않는다.
- 진실 경계: 시뮬레이션 truth·명령·내부 상태는 `evaluation/` 전용이다. public observation reader와 예측 입력에는 관측 위치·timestamp·공개 sigma·valid만 제공한다.
- 실행 순서: `plan/x8_v1_implementation_checklist.md`의 ID를 순서대로 갱신한다. production code 전에 해당 기능의 failing test를 실행하고, 구현 후 같은 test와 전체 suite를 다시 실행한다.
- 결과: 생성물은 `outputs/data_generation/v1/<dataset_id>/`, PNG와 viewer 기록은 `outputs/visualization/v1/<run_id>/`에만 쓴다. 코드 폴더·원본 폴더에는 실행 결과를 저장하지 않는다.
- 상태 보고: `complete`, `partial`, `failed`를 구별하며, 소프트웨어 검증·원본 motion check·과학적 일반화 주장을 별도 상태로 기록한다. 테스트 또는 실행 증거 없이는 완료라고 쓰지 않는다.
- 모델 선호: 실행 환경에서 선택할 수 있으면 `gpt-5.6-terra`의 `max` reasoning을 사용한다. 현재 task의 모델 전환 불가 여부는 구현 범위나 검증 기준을 바꾸는 근거가 아니다.

## 설계의 출처와 상태

- 기존 GRU 기준은 [연구 기준 문서](동적_공중_객체_미래_점유영역_설계.md)와 [GRU v1 지침](models/gru/v1/AGENTS.md)에 보존한다.
- 데이터 생성의 합의 범위와 남은 설계는 [X8 생성 설계](plan/data_generation_v1_design.md)에 기록한다.
- 별도 작업에서 전달된 최신 RED-SDS 합의는 [모델 설계 인계](plan/model_design_handoff.md)에 기록한다. 기존 GRU 기준과의 통합·전환은 아직 문서 기준에 적용하지 않았다. 두 설계를 섞거나 GRU 구현 승인이 난 것으로 해석하지 않는다.
- `합의`, `설계 제안`, `미확정`, `이전 점검 기록`, `구현`, `실행 검증`을 구별한다. 기존 문서 의미를 바꾸기 전에 변경 범위·이유를 설명하고 사용자 지시를 확인한다.
- 시뮬레이션 정답은 평가용이다. 관측 입력·학습 target·보조 loss에 몰래 넣지 않는다. 미래 정보·기동 명령·잠재 상태도 예측기 입력으로 누출하지 않는다.
- 경험적 범위를 formal Reachability나 안전 보장으로, 모델 목표 확률량을 보정된 실제 포함률로 표현하지 않는다.

## 담당 폴더로 이동

| 작업 | 먼저 읽을 지침 |
|---|---|
| 생성·운동모델·관측 합성 | [data_generation/AGENTS.md](data_generation/AGENTS.md) |
| 예측기 학습·추론·평가 | [models/AGENTS.md](models/AGENTS.md) |
| 궤적 재생·표시 | [visualization/AGENTS.md](visualization/AGENTS.md) |
| 공통 좌표·단위·시간·형식 | [contracts/AGENTS.md](contracts/AGENTS.md) |
| 외부 원본·출처 | [data_sources/AGENTS.md](data_sources/AGENTS.md) |
| 실행 산출물·추적 | [outputs/AGENTS.md](outputs/AGENTS.md) |
| 설계·결정·인계 문서 | [plan/AGENTS.md](plan/AGENTS.md) |
| 작업용 임시 파일 | [temp/AGENTS.md](temp/AGENTS.md) |

## 공통 작업 규칙

- 한국어로 설명하고 proper nouns는 원문을 유지한다. 불확실성·한계는 직접 설명하고 기계적인 긍정은 하지 않는다.
- 기능별 버전은 독립적이다. 확정한 비교 버전과 기존 결과는 보존하며, 설정만 바뀌면 새 실행 ID를 쓴다. 자동 `latest` 선택, `final2` 같은 이름, 미래 버전·가짜 실행 폴더를 금지한다.
- 코드와 `configs/`, `tests/`, `README.md`는 담당 버전에, 원본은 `data_sources/`, 결과는 `outputs/`, 설계는 `plan/`, 임시는 `temp/`에 둔다. 새 최상위 폴더는 먼저 논의한다.
- 다른 버전의 내부 구현을 직접 가져오지 않고 합의된 공통 형식을 사용한다. 생성 CSV·checkpoint·평가표·내보낸 이미지를 코드 폴더에 두지 않는다.
- **하드코딩 금지:** dataset/run ID, 파일 경로, Episode·물체 수와 ID, 시간축·단위, 기체·모델·시나리오 parameter, UI 선택지와 실행 상태처럼 실행마다 달라질 수 있는 값은 production code에 literal로 넣지 않는다. 명시적 입력, versioned config, contract, 검증된 metadata 중 하나에서 읽는다. 불변 contract 값은 한 곳의 이름 있는 상수 또는 schema에만 정의하고, 테스트 fixture의 예외적 literal은 production 경로로 유입하지 않는다.
- **로컬 viewer port:** Dash trajectory viewer는 `127.0.0.1:8088`만 사용한다. 다른 port를 임의로 선택하거나 fallback listener를 만들지 않는다. `8088`이 사용 중이면 소유 process를 먼저 확인하고, 명시적으로 범위가 확인된 viewer process만 종료한다.
- 사용자의 파일·원본·기존 도식은 보존한다. 요청 없는 Git commit, 외부 전송, 다른 프로젝트 변경을 하지 않는다.
- 향후 Python 환경은 `uv`, 설정은 Hydra + OmegaConf를 기본으로 검토한다. 실제 의존성 결정 전 빈 `pyproject.toml`이나 가짜 `uv.lock`을 만들지 않는다.
- 구현 승인 후에는 실행→검증→수정하고 예제 테스트를 수행한다. 수치 구현에는 sanity assert를 둔다. 지금은 실행 코드·테스트가 없으므로 검증 완료를 주장하지 않는다.
- 작업 종료 시 `Operation Review / Current Status / Next Steps`로 변경 파일, 실제 확인 범위, 남은 일을 간결하게 보고한다.
