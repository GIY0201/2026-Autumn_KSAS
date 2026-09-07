# X8 기반 3D 데이터 생성·재생 설계

작성일: 2026-09-07  
상태: 대화 합의의 문서화 + 상세 설계 초안이며, 2026-09-07에 X8-GEN-V1 구현·실행 검증을 완료했다. 아래 초기 초안의 미구현 서술은 당시 상태이며 9절 구현 기록이 현재 상태를 우선한다.  
합의 출처: 이 데이터 생성 작업의 원본 대화와 사용자 선택 기록. 초반 합의와 후속 변경은 [결정 대조표](decision_reconciliation.md), 예측기 요구사항은 [모델 설계 인계](model_design_handoff.md)로 관리한다. 2026-09-07 누락 정정으로 이미 선택한 값을 복원했으며 새 수치 제안이 아니다.

## 1. 무엇을 만들 것인가

실제 비행자료를 근거로 한 X8 운동모델에 새로운 기동 명령을 넣어 60초짜리 연속 3D 궤적을 생성하고, 로컬 Browser에서 평면 투영과 3D로 재생·비교한다. 5 Hz의 시작점 포함 301개 시점을 저장한다.

예를 들어 직진→좌선회→상승이라는 명령을 주면, 기체 상태를 연속으로 계산해 실제로 나타난 움직임을 저장한다. 원래 비행을 복사하거나 이상적인 경로에 위치를 강제로 맞추는 방식이 아니다. 이 예시는 기능 설명이며 기동 크기·시간·추종 성능을 확정한 설정이 아니다.

```text
실측 원본 + 논문 모델
          │
          ▼
출처·좌표·단위·전처리 확인
          │
          ▼
X8 운동모델 ── 원래 입력/바람/초기조건 ──> 실측 기동 재현·오차 확인
          │
          └── calm trim + 새 기동 명령 + 제어기
                              │
                              ▼
                    연속 3D 상태·정답 궤적
                        │             │
                        │             └── 관측오차 합성 ──> 관측 + 불확실성
                        ▼                                      │
                평가 전용 데이터                         학습/예측 입력
                        └──────────────┬───────────────────────┘
                                       ▼
                          공통 형식 + 실행 기록
                                       │
                                       ▼
                    같은 데이터·시각의 XY / XZ / YZ / 3D
```

위 그림에서 viewer는 명시적으로 선택한 데이터 종류만 표시한다. 정답 파일이 재생 가능하다는 이유로 학습 입력에 포함되는 것은 아니다.

### 합의된 첫 범위

- X8 전체 흐름부터 완성한다. quadrotor·VTOL·조류는 각각 별도 근거와 운동모델로 후속 추가한다.
- 운동과 생성 정답은 3D다. 공통 좌표는 Local ENU(x=East, y=North, z=Up)이며 XY·XZ·YZ는 같은 데이터의 투영이지 별도 2D 생성기가 아니다.
- 기본 Episode는 60초·5 Hz·301개 시점이다. 빈 공간에서 한 물체를 생성하며 지형·건물·충돌회피·군집행동은 첫 범위에서 제외한다.
- 신규 생성에는 독립 ambient wind를 추가하지 않는다.
- 실측 재현에서는 원자료의 바람·초기조건·입력·전압·정렬을 고려한다. 실측 자료가 무풍이었다고 가정하지 않는다.
- 신규 무풍 비행의 평형 상태인 calm trim은 별도로 계산한다. 바람이 있는 조건의 평형 입력을 그대로 가져와 바람만 0으로 두지 않는다.
- 처음에는 근거가 있는 정상 비행의 직진·속도 변화·좌우 선회·상승·하강과 동시 복합 기동을 다룬다.
- 기동 다양성을 우선한다. 기동 명령·순서·지속시간·초기조건으로 다양성을 만들되 자연 운항 빈도를 재현했다고 주장하지 않는다. 초기부터 공력 계수를 독립적으로 무작위 변경하지 않는다.
- 기동 전환은 기체별 동역학 반응을 따른다. 전환 시 위치·속도·자세를 초기화하지 않는다. 근거가 없어 철회한 유지 5~15초·전환 1~3초 및 뒤에 재제안한 유지 10~20초를 기본값으로 쓰지 않는다.
- 초기 관측오차는 매 0.2초 새로 추출하는 단순 방식이며 시간 drift는 후속이다. 특정 관측 장치를 재현하는 것은 아니고, 분포·크기·축 간 상관·공개 불확실성 표현은 아직 정해야 한다.

초기 4종 균등 pilot 총 400개 계획은 확대 목표로 보존하되, 후속 X8 우선 합의를 되돌리지 않는다. X8 첫 배치와 전체 pilot의 관계는 [대조표 2절](decision_reconciliation.md)에 구분한다.

## 2. 모듈 책임과 연결

다음은 책임 단위의 설계다. 2026-09-07 구현 후에는 표의 `data_generation/v1/`, `contracts/v1/`, `visualization/v1/`에 해당 코드가 존재하며, 현재 파일·설정·테스트의 실제 상태는 9절과 각 v1 README가 우선한다.

| 책임 단위 | 소유 위치 | 입력 → 출력 | 경계 |
|---|---|---|---|
| 원본·출처 보관 | `data_sources/<source_id>/` | 외부 자료 → 원본과 출처 설명 | 가공본으로 원본 덮어쓰기 금지 |
| 원본 reader·재현 준비 | `data_generation/v1/` | 원본 → 단위/좌표/출처가 명시된 재현 입력 | 추정치를 실측 정답으로 승격하지 않음 |
| X8 dynamics·actuator·propulsion | `data_generation/v1/` | 상태·조작 입력·환경 → 상태 변화 | 예측기 아님; 논문 버전 혼합 금지 |
| 재현 평가 | `data_generation/v1/` | 원래 입력의 simulation + 측정/추정 상태 → 오차 보고 | 원본 시간축 보존 |
| trim·명령 생성·추종 제어 | `data_generation/v1/` | 지원 조건과 새 명령 → 연속 조작 입력 | 위치를 이상 경로로 직접 덮어쓰지 않음 |
| episode 생성·관측 합성 | `data_generation/v1/` | 운동모델 실행 → 정답/관측/불확실성/명령 기록 | 평가 정답과 학습 입력 분리 |
| 형식·호환성 규칙 | `contracts/v1/` | 생산자/소비자의 공통 의미 | 기체 계수나 UI 로직을 넣지 않음 |
| 예측 학습·추론·평가 | `models/<model_name>/<version>/` | 관측 + 불확실성 → 예측 분포/평가 | 최신 모델 인계와 기준 전환 확인 후 구현 |
| 궤적 reader·재생·내보내기 | `visualization/v1/` | 공통 데이터 → 평면/3D 재생·비교·PNG | 데이터 재생성 금지 |

모델과 viewer는 데이터 생성기의 내부 함수를 가져다 쓰지 않고 공통 형식으로 연결한다. 첫 궤적 생성·재생은 예측기 구현 완료에 의존하지 않게 한다.

## 3. 원본 근거와 확인 범위

### 선택한 운동모델 근거

Bogdan Løw-Hansen, Richard Hann, Kristoffer Gryte, Tor Arne Johansen, Christoph Deiler (2025), *Modeling and identification of a small fixed-wing UAV using estimated aerodynamic angles*, CEAS Aeronautical Journal 16, 501–523. DOI: [10.1007/s13272-025-00816-3](https://link.springer.com/article/10.1007/s13272-025-00816-3).

이 논문은 X8 실측 기반 nonlinear 6-DOF 모델을 제시하며, 바람과 추정 공력각을 다룬다. 특정 기체·시험 조건에서의 검증이며 모든 고정익 기체나 장시간·전 비행영역의 검증으로 확대하지 않는다. 2018/2024 모델과 계수를 섞지 않는다. 구현 시 공력·추진·actuator 식과 모든 계수의 표 번호·단위·부호를 원문과 대조해야 한다.

원본 데이터: [DataverseNO, DOI 10.18710/U4TLYV](https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/U4TLYV).

### 이전 점검 기록 — 이번 문서 작업에서 원본 재감사하지 않음

아래 수치는 이 작업에서 앞서 수행한 2026-09-07 메모리 내 점검 기록이다. 원본 CSV를 프로젝트에 저장한 결과나 재현 실험 결과가 아니다. 구현 단계에서는 실제 사용 원본과 checksum을 다시 연결해 감사 기록을 남겨야 한다.

| 항목 | 이전 확인 결과 | 의미 |
|---|---|---|
| X8 TRAIN | CSV 13개, 41열, 총 5,198행 | 기동별 개별 자료 |
| 시간축 | 40 Hz, 기동별 9.05~11.55초 | 하나의 연속 장시간 비행이 아님 |
| 무결성 | 제공 checksum 일치, NaN/Inf 없음 | dynamics 재현 성공을 뜻하지 않음 |
| X8 VALIDATION | 4개 미열람 | 설정 고정 전 튜닝에 사용하지 않음 |
| 18초 실측 평가창 | 해당 단편 자료로 확보되지 않음 | 3초 입력 + 15초 미래의 실측 평가 근거가 아님 |

관측·처리 해석 시 다시 확인할 주의사항:

- IMU는 CG/body 기준으로 변환된 자료다. 같은 변환을 중복 적용하지 않는다.
- IMU 가속도는 specific force와 중력 포함 운동학적 가속도를 구분해서 비교한다.
- EKF 상태·공력각·바람은 추정치다. GPS·배터리 등의 보간된 40 Hz 열을 원래 센서 40 Hz 측정으로 해석하지 않는다.
- `IndicatedSpeed`의 실제 산출 정의, 바람의 좌표/보정, 전압, 입력 지연·시간 정렬을 원본 README와 대조한다.
- 지리각의 degree/radian 표기와 GPS XYZ의 원점·좌표는 미해결이다. 확인 전 위치 재현 성공이나 좌표 변환 완료를 주장하지 않는다.
- TRAIN에서 본 값의 최솟값·최댓값은 기체의 물리적 한계가 아니다. 서로 다른 시점의 극값을 합쳐 가능 기동으로 만들지 않는다.
- 실속 모델 부재와 제한된 식별 조건은 이전 원문 점검의 한계 기록이다. 신규 기동의 지원 범위를 별도로 정해야 한다.

사용자 제공 `Holybro Pixhawk.zip`은 프로젝트 루트에 있으며 이번 작업에서 이동·수정하지 않는다. X8과 다른 출처/기체 자료로 관리한다. 다른 후보 데이터의 적합성을 첫 X8 구현 완료로 간주하지 않는다.

## 4. 공통 데이터 의미와 미확정 사항

### 이미 합의한 의미

- 공통 위치는 Local ENU의 3D이며 x=East, y=North, z=Up, 위치 m·시간 s를 사용한다. 이 프로젝트에서 직접 선택한 좌표계이며 다른 프로젝트의 계약을 가져온 것이 아니다.
- 기본 생성 Episode는 60초다. 관측 주기 5 Hz에서 양 끝을 포함한 301개 시점을 보존한다.
- 온라인 예측에는 최근 3초의 관측 16개를 제공하고, 미래 0.2~15.0초의 75개 시점을 평가한다. 60초 저장 단위와 RED-SDS의 긴 학습 context 길이는 서로 다른 결정이다.
- 물리 적분 시계, 관측 시계, viewer의 wall-clock 재생 시계를 분리한다. 5 Hz 출력이 물리 적분 간격을 정한 것은 아니다.
- 원본/생성 Episode를 먼저 분할한 뒤 window를 만든다. 합성 pilot은 물체 종류별 Episode 단위 train/validation/test 70/15/15이며 같은 Episode의 중첩 구간·파생 관측은 다른 split으로 보내지 않는다.
- 학습 구간은 무작위로 추출하고 validation/test 구간은 고정·기록한다. 긴 관측 시퀀스 학습의 context 길이와 추출 상세는 최신 모델 인계에 맞춰 정하며, 16개 관측 학습으로 자동 축소하지 않는다.
- 합성 pilot 분할은 공개 X8 원본 TRAIN 13개/VALIDATION 4개와 별개다. 공개 평가 자료를 합성 데이터 비율에 맞춰 재분류하지 않는다.
- 짧은 실측을 이어서 긴 실제 비행처럼 만들지 않는다. 긴 학습·평가 궤적은 연속 dynamics로 신규 합성하고 합성임을 표시한다.
- 물체 식별은 정확히 알려진 경우와 Unknown을 구분하고 오식별은 후속으로 둔다. 생성 profile 정답과 공개 식별 정보를 분리하며, 첫 RED-SDS의 입력 feature로 자동 추가하지 않는다.

### 합의된 저장 방식과 남은 상세 계약

생성 표형 데이터는 모두 CSV로 보관한다. 이는 원본 ZIP/ULog, 모델 checkpoint, PNG까지 CSV로 바꾼다는 뜻이 아니다. 다음은 초반에 논의한 저장 책임이며 최종 파일명·열을 확정한 schema는 아니다.

| 정보군 | 필요한 의미 |
|---|---|
| 궤적·관측 | Episode/시간별 위치와 공개 관측 불확실성; 정답은 평가 전용 접근으로 분리 |
| Episode 목록 | ID·생성 profile·공개 식별 정보·split·기간과 연결 관계 |
| 기동 구간 | 명령·구간·복합 기동 및 전환 기록; 예측 입력에서 제외 |
| 운동 profile·출처 | 사용 모델·계수 근거·원본 provenance |
| 평가 window | 고정 시작점·입력/미래 구간·원본 Episode 연결 |
| 실행 manifest | 명시 버전·ID·hash·seed·설정·환경 기록 위치 |

정답·관측·명령은 ID와 시간으로 연결하되 학습 인터페이스에 평가 전용 정보가 노출되지 않게 한다. 초반에 제안한 정답/관측 동시 열 배치를 그대로 확정하지 않으며, 안전한 파일/접근 경계는 상세 계약에서 정한다. 실행 기록 이름은 현재 배치 규칙의 `manifest.csv`를 따른다. 예전 파일명 예시인 `dataset_manifest.csv`와 별도 표준을 병립시키지 않는다. 빈 CSV나 가짜 기록은 만들지 않는다.

### 실제로 상세화할 항목

| 항목 | 이미 합의한 것 | 남은 상세 |
|---|---|---|
| 공통 XYZ | Local ENU 축·위치 m | 원점·원본 NED/GPS 변환·모델 상대좌표/회전 |
| timestamp | 시간 s, 60초·5 Hz·301개 | 원본 시간/상대시간 표현·정렬·중복·결측 정책 |
| 관측 불확실성 | 예측기에 공개, 실제 순간 오차와 구분 | 종류·단위·축 간 상관·유효성·누락 처리 |
| 관측오차 합성 | 초기에는 시점별 새 표본, 시간 drift 제외 | 분포·크기·축 간 상관·공개 불확실성과의 정합성 |
| 물리 적분 | 관측 시계와 분리 | 알고리즘·간격·수렴 검사·actuator delay |
| 기동 명령·제어기 | 정상·동시 복합 기동, 기체별 전환 | 근거 있는 속도/선회/상승·지속 범위·제어기·실패 판정 |
| Episode·분할 | 60초·70/15/15·학습 무작위/평가 고정 | seed 계층·고정 구간·X8 첫 배치와 확대 발행 계획 |
| 저장 형식 | 생성 표형 데이터 모두 CSV | 파일/접근 분리·열·자료형·단위·버전·유효성 규칙 |

관측 불확실성이 없거나 유효하지 않다고 0 오차를 조용히 가정하지 않는다. ENU 선택만으로 원본의 GPS 단위·원점 문제가 해결된 것은 아니다. `manifest.csv`의 추적 의미는 [배치 규칙 5절](project_structure.md)을 따른다.

## 5. 평면·3D 재생 설계

확정된 기능 방향:

- 로컬 Browser interactive viewer를 사용하고 PNG export를 제공한다.
- 단일 Episode 검사와 여러 Episode 비교를 지원한다. 여러 Episode overlay는 서로 상호작용하는 다중 물체 생성과 다르다.
- 초반 범위의 3D·XY·XZ·YZ, true/observed 궤적, 속도·가속도·기동 구간 표시를 보존한다. 이후 XZ·3D 추가 요청을 이유로 YZ를 제거하지 않는다.
- 재생·일시정지·시간 탐색은 같은 데이터 시각을 공유한다. 서로 다른 Episode의 비교 시간 기준은 상세화한다.
- 3D 카메라 회전·확대는 원본 데이터를 바꾸지 않는다.

상세 UI/표시 제안 — 라이브러리·배치 확정 아님:

- 경로와 현재 위치, 시간, ENU 축·단위, 데이터 ID·출처 및 관측/정답 구분을 표시한다. 정답을 보여주는 viewer 기능이 학습 정답 접근을 허용하지 않는다.
- 평면은 동일 길이를 동일 척도로 보이게 하고, 3D도 비등방 확대를 숨기지 않는다.
- Browser 실행 방식은 정해졌지만 구현 라이브러리, 동시 패널/보기 전환, 배속 범위·trail 길이는 미확정이다. 초반 4개 보기 설명을 고정 4-panel 배치 합의로 승격하지 않는다.
- 속도·가속도 표시의 계산 출처와 평활화 여부를 밝힌다. 표시용 처리가 저장된 정답을 바꾸지 않게 한다.
- 결측이나 재생용 보간이 있으면 드러낸다. 렌더링 보간값을 새 물리 정답으로 저장하지 않는다.
- 읽기 실패·미지원 버전·차원·좌표 불명확 상태를 사용자에게 알린다.
- PNG는 `outputs/visualization/v1/<run_id>/`에 저장한다. 예측 점유 영역 연결은 별도 후속 범위다.

## 6. 당시 구현 전 결정과 진행 순서 (historical)

이 문서의 초안 당시에는 사용자가 설계·지침 정리만 요청했다. 아래는 당시의 후속 구현 계획이며, 2026-09-07 실행 결과는 9절에 기록한다.

1. [결정 대조표](decision_reconciliation.md)에서 합의값과 후속 변경을 확인하고, 위 표의 실제 남은 상세만 구체화한다. 60초·ENU·CSV·Browser 등을 다시 선택하도록 묻지 않는다. 생성·재생 착수와 예측 모델 기준 전환/착수는 구분한다.
2. 원문 계수/식·원본 필드·좌표·시간 처리 명세를 완성한다. 특히 추진 계수·초기 상태·trim·지연 처리를 미확정 상수로 대체하지 않는다.
3. 합의된 ENU·60초·CSV·합성 70/15/15를 바탕으로 상세 계약·공개/평가 경계·관측 불확실성·정량 검사 기준을 기록하고 구현 범위를 승인받는다.
4. 승인 후 `uv` 환경과 유효한 설정/lock을 준비하고, 원본 reader·좌표/단위·수치 구성요소의 테스트부터 구현한다.
5. X8 실측 기동 재현을 실행한다. 원본 추정치와 비교한 오차, 입력 정렬, 설정, source hash를 남긴다. 적합성 평가에 앞서 기준을 고정한다.
6. calm trim·제어기·새 명령으로 정상 연속 궤적을 생성한다. 추종 실패나 지원 범위 이탈은 그대로 기록한다.
7. 관측/정답을 분리 저장하고 로컬 Browser의 평면/3D 재생·단일/다중 Episode 비교·PNG export를 확인한다. 같은 버전 2회 실행의 결과 보존과 추적을 확인한다.
8. 합의된 예측 모델은 별도 단계로 연결한다. 생성·재생이 된 것과 실측 일반화·예측 성능 검증은 별도 완료 조건이다.

## 7. 초기 확인 기준 (historical; 현재 증거는 9절)

| 범주 | 확인할 것 | 증거 |
|---|---|---|
| 수치 | 유한값·시간 증가·좌표 round-trip·자세/힘 단위·적분 수렴 | 단위/수치 테스트와 실행 기록 |
| 원본 재현 | 원래 입력의 상태·속도·각속도·specific force 오차 | 기동별 지표/비교 그림/원본 provenance |
| 신규 생성 | 연속성·trim 유지·정상 기동 추종·지원 범위 이탈 처리 | 신규 episode와 명령/실패 기록 |
| 데이터 경계 | 관측/평가 정답 분리·episode split·형식 거부 | 계약·누출 방지 테스트 |
| 재생 | 같은 시각/데이터 ID, XY/XZ/YZ/3D 위치 일치, 시작·끝·역탐색·비교·PNG | 실제 viewer 조작/비교 결과 |
| 재현성 | 명시 버전·seed·설정·환경·source/code hash | 실행 manifest와 연결 파일 |
| 결과 보존 | 두 번 실행해도 다른 ID, 코드 폴더에 결과 없음 | 실제 실행 경로 점검 |

합격 오차·기동 제한·처리시간 합격선은 아직 정하지 않았다. 논문의 개별 기동 오차를 전 시나리오 합격선으로 복사하지 않는다.

## 8. 초기 문서 정리의 완료 범위 (historical)

- X8 생성·재생의 범위, 책임, 데이터 흐름, 남은 결정과 검증 계획을 파일로 남긴다.
- 폴더별 지침은 [project_structure.md 8절](project_structure.md)에 따라 유지한다.
- 기존 연구 기준 문서·GRU 도식·원본 ZIP은 보존한다.
- 설계 초안 정리는 완료할 수 있지만, 세부 수치 명세 확정·코드 구현·실행 검증은 별도의 미완료 작업이다.

## 9. 2026-09-07 구현·검증 기록 (current)

이 절은 초기 설계의 구현 결과를 기록한다. 여기서 `VERIFIED`는 코드·schema·실행 경로의 검증 상태이며, 실비행 안전성이나 field validation을 뜻하지 않는다.

### 9.1 고정한 실행 범위

- Local ENU의 단일 X8, empty space, calm air, 60 s, 5 Hz, 301 stored samples를 구현했다.
- 내부 dynamics는 NED/body-FRD 6-DOF, actuator delay, propulsion, calm trim, RK4 2.5 ms를 사용하고 output에서만 ENU로 변환한다.
- event-driven controller는 straight, left/right turn, climb/descent, speed recovery를 하나의 연속 state로 실행한다. 위치·속도·자세를 event 전환 때 강제로 덮어쓰지 않는다.
- 실제 diagnostic scenario는 60초 종료까지 complete 상태를 기록했다. controller gain은 이 scenario를 위한 implementation-tuned candidate이며, 논문으로 검증된 universal parameter라는 주장을 하지 않는다.
- GPS/위경도·지도, 영상/camera, sensor fusion, prediction model, occupancy computation은 구현하지 않았다. X8 source reader도 GPS 열을 읽지 않는다.

### 9.2 원본과 source motion check

- DataverseNO `doi:10.18710/U4TLYV`의 headerless CSV 17개를 source 전용으로 보관하고, repository metadata MD5·13 TRAIN/4 VALIDATION split을 확인했다.
- source motion check는 TRAIN 13개만 사용했다. body velocity, angular rate, wrapped Euler, indicated airspeed, specific force를 원본 시간축에서 재적분해 full 및 first-1-second-after residual로 기록한다.
- 전체 TRAIN의 full RMSE는 각각 body velocity `0.977449 m/s`, angular rate `0.147480 rad/s`, Euler `0.0871933 rad`, indicated speed `0.841801 m/s`, specific force `1.36256 m/s²`였다.
- 이 결과는 `REPORT_ONLY`다. acceptance threshold가 없고 VALIDATION tuning이나 holdout performance claim을 하지 않았으므로, 모델 적합성·안전성·다른 조건의 일반화를 결론내리지 않는다.

### 9.3 실제 contract와 dataset

- `contracts/v1/`은 public directory allowlist, 정확한 header/order, `step=0..300`, `t_s=step×0.2`, sigma·finite value·row completeness를 strict하게 검증한다.
- public observation은 `episode_id, variant_id, step, t_s, x_m, y_m, z_m, sigma_x_m, sigma_y_m, sigma_z_m, valid`만 포함한다.
- evaluation truth·command·trigger·diagnostic은 `evaluation/`으로 분리한다. `PublicDataset` type에는 truth/command field가 없다.
- deterministic `SeedSequence` stream으로 `sigma_1m`, `sigma_3m`, `sigma_5m`의 독립 zero-mean Gaussian coordinate noise를 생성한다. 새 dataset은 UUID folder를 만들고 manifest/files/config/environment를 함께 기록한다.
- 확인된 진단 결과는 `outputs/data_generation/v1/ccc2b760-0b3e-46c9-a565-ff224e74f8df/`이며, public observations 903행, truth 301행, source motion check 140행을 가진다.

### 9.4 viewer와 export

- `visualization/v1/`은 Dash/Plotly로 XY/XZ/YZ/3D를 2×2 desktop grid, narrow viewport stack으로 표시한다. 모든 panel은 동일 stored integer step을 공유한다.
- true는 solid, observed는 dashed, current observed point는 diamond로 표시한다. 2D axes는 equal scale, 3D는 `aspectmode=data`다.
- Play/Pause, slider seek, 0.25×/0.5×/1×/2×/4×를 제공하며 interpolation을 저장하거나 생성하지 않는다. Pause/tick ordering은 local single worker로 보존한다.
- `--public-only`는 evaluation truth와 diagnostic을 읽지 않고 truth toggle도 표시하지 않는다.
- Browser에서 실제 dataset으로 Play/Pause와 shared step을 확인하고 `Export four-view PNG`를 실행했다. 결과 `outputs/visualization/v1/090b520c-ecfd-4124-85ee-14d3d8ed82d5/`에는 XY/XZ/YZ/3D PNG, view_state, manifest, file hashes, environment가 있다.

### 9.5 최신 검증 증거

| 범주 | 최신 증거 | 해석 |
|---|---|---|
| TDD | feature별 expected failure 후 구현, `uv run pytest -q --basetemp temp\pytest-final` → `25 passed in 63.22s` | 구현 회귀 검증 |
| 정적 검사 | `uv run ruff check contracts data_generation visualization` → `All checks passed` | Python style/static error 검증 |
| Browser | local `127.0.0.1`에서 dataset, four panels, Play/Pause, shared step, PNG export 확인 | UI 경로 검증 |
| 결과 보존 | dataset/export의 UUID folder, manifest, files SHA-256 확인 | lineage·non-overwrite 검증 |
| source motion | TRAIN 13개 fixed-model residual report | report-only; field/holdout safety claim 아님 |

초기 설계의 남은 연구 항목(실제 합격 오차 기준, wider operating envelope, 관측 센서 realism, 모델 설계·학습·평가)은 자동으로 해결된 것이 아니다. 필요해질 때 별도 version과 명시적 실험 계획으로 다룬다.
