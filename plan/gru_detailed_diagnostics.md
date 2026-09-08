# GRU 상세 진단 로그와 loss 가중치 비교

2026-09-08 사용자 승인: 입력·예측·미래 관측 위치와 행동을 연결해 저장하고,
참조 작업 `GRU 3D 구조 비교`의 여러 seed 재현·축별 trade-off 확인을 수행한다.

## 구현 계약

- 로그의 공통 식별자는 run/checkpoint/epoch/split/dataset/Episode/sequence와 시각이다.
- 입력 16개 관측, 미래 75개 관측·예측·축별 오차를 같은 ENU 미터 좌표로 저장한다.
- 생성기의 commands event 구간은 예측 이후 진단에만 연결한다. 입력·target·loss에
  넣지 않는다. simulation truth는 관측 target과 혼합하지 않는다.
- 관측 기반 XY 이동·고도 변화 분류와 generator event 이름·전환 시각·출처 hash를
  별도 필드로 보존한다. 라벨 부재는 unknown이며 호버링으로 추정하지 않는다.
- 매 epoch: train/validation 전체 집계와 움직임 6종별 최대 2개 고정 window 상세 기록.
  최대 12개/split이며 subset index를 기록한다. best/last: 전체 validation.
- test 상세 기록은 명시적인 test_after_training 평가의 동일 prediction을 재사용한다.
  이번 실험은 validation_only이며 test 관측을 읽거나 평가하지 않는다.
- Dashboard는 명시적 run/checkpoint에서 움직임 유형별 필터, Z RMSE/ADE 내림차순,
  window 선택, XYZ 및 고도-시간 곡선, 행동 전환을 제공한다. 기존 run은 상세 로그가
  없음을 표시하며 원본을 덮어쓰지 않는다.

## 가중치 비교

- XYZ weights: [1,1,1], [1,1,2], [1,1,3], [1,1,5].
- 모델 seed: 17,29,43. 학습 window seed는 17로 고정. 총 12개 새 run을 순차 실행한다.
- 기존 normalization/window/source hash를 유지한다. 같은 seed의 초기 parameter hash가
  가중치 조건 사이 동일해야 한다. 구조·optimizer·schedule은 동일하다.
- 가중치 선택은 seed별 best validation ADE의 산술평균 최소값, 동률은 낮은 Z weight.
  XYZ RMSE 및 움직임별 손익을 함께 보고하고 선택을 최적값 확정으로 표현하지 않는다.
- default weight [1,1,1]은 자동 변경하지 않는다. 선택 결과는 별도 실험 산출물이다.
- 기존 test 평가 이력은 존재한다. 이번 결과는 독립적인 미열람 test 확증이 아니다.

## 검증

좌표·시간·행동 전환 join, 진단 라벨의 모델 입력 분리, 상세 로그 test 접근 차단,
고정 subset과 데이터 seed 분리, UI filter/선택/누락 처리, hash와 실제 Browser 표시를
검증한다. 실행 receipt와 결과 경로는 완료 후 기록한다.

## 실행 중 확인 (2026-09-08)

- 첫 비교 시도 `20260908T121405_b95d800c6941`는 worker의 native Windows
  종료 코드 `0xC0000409`로 실패 처리했다. 기존 산출물은 보존했다.
- 작은 CUDA GRU의 cuDNN train-mode forward만으로 재현했다. cuDNN 비활성화
  GPU child smoke 2회는 exit 0이었다. 정확한 native library 내부 원인은 미확정이다.
- 새 sweep `20260908T122211_0b84830614da`는 전 조건에
  `training.cudnn_enabled=false`를 명시했다. CUDA:0·FP32·모델 수학 구조를 유지하되
  backend 수치 차이를 인정하며 이전 cuDNN 결과와 동일 실행이라고 주장하지 않는다.
- 새 기본 설정에도 이 backend 값을 명시했다. Z weight 기본값은 [1,1,1]을 유지했다.
- 원본 Episode 시간 전수 집계는
  `outputs/visualization/v1/20260908T122357_1bf575ab1cb9/`에 저장했다.
  Train 44개: 상승 6340.051 s, 고도 유지 순항 2578.097 s, 하강 7297.234 s,
  기타 3499.618 s. 겹침·모호함·미분류 시간은 모두 0 s이다.
  이 값은 중복 학습 window의 노출시간이나 관측 속도 임계값 분류가 아니다.

## 완료 검증 (2026-09-08)

- 상세 위치/행동 로그, Dashboard 필터와 궤적 표시, 축별 loss 기여도 기록을 구현했다.
- 전체 suite: 438 passed. GPU child smoke 2회 정상 종료.
- Sweep `20260908T122211_0b84830614da`: 12개 run 정상 종료, validation-only.
  24개 best/last checkpoint의 저장 좌표와 집계 재계산, manifest, 같은 seed 초기값,
  공통 window/normalization/subset hash 검증을 통과했다. test 재평가는 없다.
- 3-seed 평균 best validation ADE로 [1,1,3]을 선택했다.
  ADE 49.413740 → 44.773751 m, Z RMSE 27.018244 → 9.908966 m.
  XY 오차와 일부 고도 유지 구간의 Z 오차는 증가했다. 최적값/일반화 주장은 하지 않는다.
- 별도 preset: `76cb6a7c2f79465b8027793eb32e28f7`,
  `GRU Direct [1,1,3] - 3-seed validation`. 기본 axis weights는 그대로다.
- 수직 이동 3분류 시간 집계는 `20260908T124149_ecdd765655c2`에 저장했다.
  Train: 상승 6340.050835 s (32.16%), 고도 유지 6077.715617 s (30.83%),
  하강 7297.233548 s (37.01%). 동일하지 않다.
  고도 유지는 순항 외 hover/transition/ground를 포함한다. 순항 hold만은 13.08%다.
  이는 generator 명령 시간이며 관측 기반 움직임 분류나 window 노출 비율과 다르다.
- 상세 해석/CSV/그림/검증 receipt는 sweep의 `analysis-report.md`,
  `diagnostics_summary/`, `verification.json` 및 `manifest.csv`에 보존한다.
