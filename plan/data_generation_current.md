# 현재 데이터 생성 방식

작성일: 2026-09-08  
범위: 고정익·헬기·VTOL의 `corpus_*` 학습데이터 생성 설정  
기준: 이 작업의 사용자 합의와 현재 생성 코드. 과거 X8·60초 실험 기록과 구분한다.

## 한 번 생성하면 무엇이 나오는가

하나의 비행 궤적에 관측오차 3종을 더하고, 각 관측 데이터에 식별 패턴 3종을 적용한다. 원본 비행 1개에서 학습용 입력 9개가 나온다. 독립적인 비행이 9개 생기는 것은 아니다.

```text
이륙부터 착륙까지 원본 비행 1개
  → 관측오차 3종: sigma 1m / 3m / 5m
  → 각각 식별 패턴 3종: 식별 획득 / 항상 미식별 / 식별 상실
  → 같은 split에 속하는 입력 9개
```

고정익·헬기·VTOL은 질점 모델로 연속 위치를 생성한다. 최대 비행시간은 600초이며 저장 간격은 0.2초(5 Hz)다. XY·XZ·YZ·3D 화면은 같은 위치 데이터를 다른 방향에서 보여준다. 실제 카메라 영상, GPS 처리, 바람은 이번 생성 범위에 포함하지 않는다.

현재 전체 비행 경로는 설정된 속도와 선회율을 부드럽게 연결해 위치를 적분한다. Gazebo 참고 출처가 있다고 해서 이 생성기가 Gazebo의 힘 모델을 실행하거나 실기체 성능을 검증한 것은 아니다. 기동 구성은 [객체별 시나리오](aircraft_motion_scenarios.md), 구현 경계는 [통합 기록](scenario_generator_integration.md)을 따른다.

## 생성량과 seed

기본 반복 횟수는 시나리오당 1회다. 전체 Episode를 저장하는 기본 설정의 생성량은 다음과 같다.

| 객체 | 원본 비행 | 관측오차 적용 시퀀스 | 식별 패턴까지 적용한 입력 |
|---|---:|---:|---:|
| 고정익 | 64 | 192 | 576 |
| 헬기 | 96 | 288 | 864 |
| VTOL | 64 | 192 | 576 |

seed를 바꿔도 전체 시나리오 목록은 유지된다. 다만 시작 방향, 선회 방향, 허용 범위의 속도 배율, 관측 잡음, 식별 전환 시점은 달라질 수 있으며, split 배정에도 seed를 사용한다.

같은 코드·설정·seed로 생성한 결과는 저장 폴더가 달라도 내용이 같은 복제본이다. 이를 새로운 독립 데이터로 합산하거나 학습·평가에 나누어 넣지 않는다.

## 관측오차는 어디에 있는가

`x_m, y_m, z_m`은 관측오차가 들어간 위치다. 각 축과 시점에 평균 0인 독립 Gaussian 잡음을 더한다.

| 관측 버전 | 각 축의 표준편차 |
|---|---:|
| `sigma_1m` | 1 m |
| `sigma_3m` | 3 m |
| `sigma_5m` | 5 m |

각 행의 `sigma_x_m, sigma_y_m, sigma_z_m`에 표준편차를 기록한다. 3 m는 오차가 항상 ±3 m 안에 있다는 뜻이 아니다. 실제 정답 위치와의 차이를 학습 입력으로 제공하지 않는다.

## 객체 종류를 알거나 모르는 경우

위치는 계속 관측한다. 달라지는 것은 객체 종류를 알고 있는지 여부다. 같은 관측오차 버전 안에서는 세 패턴의 위치·시간·잡음이 모두 같다.

| 폴더 | 처음 상태 | 전환 이후 |
|---|---|---|
| `acquired` | 종류를 모름 | 종류를 알고 유지 |
| `unknown` | 종류를 모름 | 계속 모름 |
| `lost` | 종류를 앎 | 종류를 모르고 유지 |

예를 들어 헬기 데이터가 미식별 상태라면 `identity_known=0`, `observed_object_type=unknown`이다. 식별 상태라면 `identity_known=1`, `observed_object_type=helicopter`가 된다. 위치의 유효 여부를 나타내는 `valid`는 현재 항상 1이며, 객체 식별 여부와는 별개다.

전환형은 비행 중 한 번만 바뀐다. seed로 저장 시점 하나를 선택하되, 시작과 종료에서 각각 최소 3초를 남긴다. 같은 비행의 두 전환형과 모든 관측오차 버전은 이 시점을 공유한다. 상승·선회 같은 기동 이벤트를 보고 전환 시점을 정하지 않는다.

반복 전환과 오분류는 제외했다. 이는 이번 연구의 가정이며, 실제 카메라에서 그런 일이 없다는 주장은 아니다.

## train / valid / test는 어떻게 나누는가

원본 Episode에 split을 배정하고, 모든 관측오차·식별 패턴·학습 구간이 이를 그대로 따른다. train 70%, validation 15%는 내림하고 나머지를 test에 둔다.

| 원본 비행 수 | train | valid | test |
|---|---:|---:|---:|
| 64 | 44 | 9 | 11 |
| 96 | 67 | 14 | 15 |

CSV의 내부 이름은 `validation`, 폴더 이름은 `valid`다. 진단용 생성은 `diagnostic`으로 따로 보관한다. 같은 기동 유형이 여러 split에 들어갈 수 있으므로 이 분할을 미관측 기동 유형의 일반화 시험이라고 부르지 않는다.

## 어떤 파일을 열면 되는가

```text
outputs/data_generation/v1/<사람이 읽는 dataset 폴더>/
├─ public/
│  ├─ episodes.csv             # 원본 Episode와 split
│  └─ observations.csv         # 위치·시간·관측오차 3종
├─ training/
│  ├─ settings.yaml
│  ├─ sequences.csv            # 위치 전용 구간 목록
│  ├─ summary.csv              # 위치 전용 개수
│  ├─ identity_schedule.csv    # 전환 시점 기록, 모델 입력 아님
│  ├─ identity_summary.csv     # 식별 패턴별 개수
│  ├─ train/
│  │  ├─ observations.csv      # 위치 전용
│  │  ├─ sequences.csv
│  │  ├─ acquired/             # observations.csv + sequences.csv
│  │  ├─ unknown/              # observations.csv + sequences.csv
│  │  └─ lost/                 # observations.csv + sequences.csv
│  ├─ valid/                   # train과 같은 구조
│  └─ test/                    # train과 같은 구조
├─ evaluation/                 # 정답 위치·운동 상태·기동 기록
├─ manifest.csv
└─ files.csv                   # 파일별 hash
```

`training/diagnostic/`도 같은 구조로 만든다. 비어 있는 분할은 CSV 헤더만 저장한다. 식별 정보를 보려면 패턴 폴더 안의 파일을 연다. 예를 들면 `training/train/acquired/observations.csv`다.

위치 전용 파일과 식별 확장 파일을 모두 합쳐 학습하지 않는다. 위치 전용 파일은 기존 사용 방식을 유지하려고 보존한 것이다. 식별 정보를 사용하는 학습에서는 세 패턴의 파일을 선택한다.

`episode_id`, `variant_id`, sequence ID, split, 패턴 폴더명은 관리용이다. 미래 전환 시점, 실제 객체 설명, seed, 기동 명령도 모델 입력으로 넣지 않는다. 모델에 전달할 위치·시간·관측 정보와 현재 식별 컬럼을 명시적으로 선택해야 한다.

## 생성 방법

8088 UI의 ‘객체 · 생성 설정’에서 ‘고정익 / 헬기 / VTOL · 모든 시나리오 + 학습데이터’를 선택하고 seed를 입력한 뒤 생성한다. CLI에서는 프로젝트 루트에서 아래 명령을 사용한다.

```powershell
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_fixed_wing
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_helicopter
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name corpus_vtol
```

새 폴더명은 `객체설명_모드_개수episodes_seed값_고유접미사` 형식이다. 이전 UUID 폴더는 그대로 읽을 수 있지만 새 식별 컬럼을 자동으로 추가하지는 않는다. 서버는 시작 시 저장된 완료 dataset 목록을 복원한다. 분석·실패 결과는 제외하며 전체 내용 검사는 선택할 때 수행한다.

## 구현된 부분과 남은 작업

전체 비행 생성, 관측오차 3종, 식별 패턴 3종, 분할별 CSV 저장은 구현했다. 앞선 구현 작업에서 관련 테스트 41개와 헬기 전체 비행 1개 생성으로 9개 입력 저장을 확인했다. 이 문서 갱신에서는 생성이나 테스트를 다시 실행하지 않았다.

기본 `training.window_samples=null`은 전체 비행을 저장한다. 구간 길이를 지정하면 train은 seed로 시작점을 뽑고 validation/test는 일정 간격으로 뽑는다. 식별 옵션이 있으면 전환을 포함한 구간도 추가한다.

기존 `iter_training_sequences`는 위치 전용 reader다. 식별 컬럼을 소비하는 모델별 loader, 16개 context와 75개 미래 target 구성, 정규화, 모델 학습과 성능 평가는 아직 별도 작업이다. 학습 구간을 구성할 때 전환이 최근 3초 context 안에 들어가는 사례를 유지해야 한다.

자세한 식별 입력 경계는 [식별 정보 명세](identity_training_inputs.md), 초기 전체 시나리오 실행 결과는 [당시 실행 기록](telemetry_training_generation.md)에 남겨 두었다.
