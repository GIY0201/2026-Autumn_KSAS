# 쿼드콥터 운동·생성 설정 점검

- 날짜: 2026-09-07
- 요청: 쿼드콥터의 FPV 여부와 급격한 움직임을 설명하고, 중복처럼 보이는 X8 설정의 용도를 정리한다. 불필요한 일반 사용자 항목은 표시하거나 삭제할 수 있다.
- 범위: 기존 코드·저장 데이터 진단, UI 이름·설명 정리. 운동모델·명령·관측오차·public 계약·기존 결과는 변경하지 않는다.
- 상태: 원인 진단·표시 정리·전체 회귀 테스트·8088 Browser 표시 확인 완료. 운동모델 개선이나 실비행 검증은 이 작업의 범위가 아니다.

## 현재 쿼드콥터의 의미

현재 모델은 Crazyflie 2.1의 공개 식별 계수를 사용하는 연구용 모델 재현이다. [Bitcraze 제품 설명](https://store.bitcraze.io/products/crazyflie-2-1)은 이를 연구·교육용 개발 플랫폼으로 소개한다. 모델의 원문은 Jonas Eschmann, Dario Albani, Giuseppe Loianno, *Data-Driven System Identification of Quadrotors Subject to Motor Delays* (2024), [arXiv v2](https://arxiv.org/abs/2404.07837v2), DOI `10.48550/arXiv.2404.07837`이다. FPV 영상·조종 링크·레이싱 기동을 모델링하지 않는다.

`configs/profiles/crazyflie_eschmann_2024.yaml`의 저속 waypoint 순서는 상승 → 짧은 수평 이동·고도 변경 → 정지 후 다음 목표로 반복한다. 명령 속도 1 m/s, 위치 허용오차 0.15 m, 속도 허용오차 0.15 m/s와 0.4초 settling은 **프로젝트의 점검 설정**이며 기체의 일반 비행 한계나 실측 기동 분포가 아니다.

`quadrotor_scenario.py`는 목표를 바꿔도 위치·속도를 강제로 재설정하지 않는다. 충분히 감속해 목표에 도달한 뒤 다음 방향으로 출발하기 때문에 실제 경로도 이동·정지가 뚜렷하다. seed는 시작 방향과 이동 크기를 바꾸지만 기동 순서를 다양하게 조합하지 않는다. `pilot_quadrotor`로 생성량을 늘려도 이 한계는 그대로다.

## 관측점이 급격하게 튀는 이유

실제 확인한 기존 seed 42 결과: `outputs/data_generation/v1/7cee3913-c9f3-48cb-a72e-4ddaee4a0915`. manifest의 `seed=42`, `object_id=quadrotor`, `model_id=crazyflie_eschmann_2024`를 확인하고 301개 truth 시점과 각 관측 variant를 비교했다. 현재 Browser가 선택한 다른 객체의 수치라고 해석하지 않는다.

- `public/observations.csv` SHA-256: `58248968c436da2a7cd5ba03656086ee5afd3740bee4f2b4cb84bb4989f7c0d1`
- `evaluation/truth.csv` SHA-256: `532985a056cf5bbe88e4133170dd3aa8daf14361bfb11e6856cf3019e06b1e47`

| 0.2초 간격 인접 위치의 거리 | 중앙값 (m) | 최댓값 (m) |
|---|---:|---:|
| 시뮬레이션 정답 | 0.142 | 0.235 |
| sigma 1m 관측 | 2.287 | 5.406 |
| sigma 3m 관측 | 6.020 | 16.549 |
| sigma 5m 관측 | 11.062 | 26.325 |

정답의 XYZ 이동 범위는 약 4.017 × 4.009 × 1.587 m, 최대 속도는 약 1.178 m/s다. 위치 변화량의 차분으로 계산한 최대 가속도는 약 1.680 m/s²다. 이는 해당 Episode의 결과이지 모든 seed의 검증이나 기체 성능 한계가 아니다.

저장 관측에는 매 0.2초마다 독립 Gaussian 위치오차가 추가된다. 현재처럼 수 m 범위를 이동하는 경우 m 단위 오차가 실제 이동량보다 커서 점선 observation과 현재 diamond 표식이 크게 튄다. 실선 truth와 구분해야 한다. 5 Hz 저장점만 재생하고 중간점을 보간하지 않는 표시도 계단식 이동에 영향을 주지만, 이 사례의 큰 튐은 관측오차의 영향이 크다. 관측점을 평활화해 현상을 감추거나 truth를 예측 입력으로 쓰지 않는다.

## X8의 세 항목과 표시 변경

X8은 고정익 UAV **한 종류**이고 세 항목은 실행 목적이 다른 preset이다.

| 설정 | 필요한 이유 | 이번 표시 |
|---|---|---|
| `diagnostic` | 빠르게 60초 궤적 1개를 생성해 확인 | `X8 고정익 UAV · 1개 미리보기` |
| `pilot` | 같은 모델로 100개 생성, 학습/검증/시험 70/15/15 분할 | `X8 고정익 UAV · 100개 생성` |
| `diagnostic_with_motion_check` | 1개 생성 + TRAIN 원본 13개 재적분 비교 보고서 | `[개발자 점검] X8 고정익 UAV · 원본 운동 비교` |

세 번째는 일상적인 생성에는 필요 없지만 개발·원본 비교에 쓰므로 삭제하지 않고 점검용으로 표시했다. 다른 운동모델, 더 좋은 궤적, 자동 합격 판정이 아니다. `REPORT_ONLY`를 유지한다. 다른 세 객체도 일반 설정을 `1개 미리보기` / `100개 생성`으로 통일했다. 네 객체·9개 preset의 ID와 실제 동작은 그대로 유지한다.

수정 파일은 9개 preset YAML의 `ui` metadata, 루트·생성·viewer README, 이 문서다. Python·JavaScript·운동 profile·기존 출력 파일은 수정하지 않는다. UI 디자인 지침은 기존 레이아웃을 유지하면서 이름과 용도를 명확히 구분하는 데만 적용했다.

## 실행 확인 기록

- 변경 전 preset/config 및 README 사본: `temp/preset-label-review-20260907/`.
- `python -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-preset-labels-20260907`: **142 passed in 118.01s**, exit 0.
- `python -m ruff check data_generation/v1 visualization/v1 contracts/v1`: **All checks passed**, exit 0.
- 변경 전 사본과 현재 설정을 OmegaConf로 읽어 비교: **13개 YAML 모두 `ui` 이외의 값 동일**, 변경된 `ui`는 9개 preset뿐. 실제 service 목록은 **4개 객체·9개 preset**이고 개발자 표시는 원본 비교 preset 한 개뿐임을 assert로 확인.
- 독립 read-only review: **APPROVE**, 수정 필요 발견 사항 없음. preset 역할·쿼드콥터 명령 구성·관측과 truth 구분을 현재 코드와 대조했다.
- 8088의 실행 중인 생성 작업이 없음을 확인한 뒤 소유 viewer만 재시작. 같은 VTOL dataset `510402f2-231b-47d5-82f8-54b98b02fde8`을 계속 사용하며 다른 port는 열지 않았다.
- In-app Browser를 새로고침해 9개 새 preset 이름·개발자 표시, 쿼드콥터 선택 후 저속 점검·관측오차 안내, 원본 비교 선택 후 일반 생성에 불필요한 개발용이라는 안내, 기존 네 그래프가 표시됨을 확인. 이 작업에서 새 실사용 dataset을 생성하거나 100개 batch를 재실행하지 않았다.
- 변경 후 generator code/config hash: `f26011a7250a3f17d89b7467d9f2956785ddc081efdc4f40a9aabbee15c21684`. UI 문자열도 설정 hash에 포함되므로 hash는 변경되지만 비-UI 설정과 운동 동작은 바뀌지 않았다. 기존 dataset의 manifest와 hash를 소급 수정하지 않았다.
- 완료 후 이번 테스트 전용 `temp/pytest-preset-labels-20260907/`만 절대 경로·reparse point 유무를 확인해 정리했다. 이 폴더는 테스트로 재생성할 수 있다. Git 저장소가 아니므로 표시 변경 전 사본은 복구용으로 보존했다. 기존 임시 파일과 실제 생성 결과는 삭제하지 않았다.

## 남은 연구상의 개선점

일반 쿼드콥터의 다양한 자연스러운 움직임을 만들려면 waypoint 점검 명령의 구성·분포와 이동 규모에 비해 큰 관측오차를 별도 연구 결정으로 검토해야 한다. 이번 표시 정리를 운동 다양성 개선이나 새로운 모델 검증으로 해석하지 않는다.
