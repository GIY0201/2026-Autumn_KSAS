# VTOL 행동·방향 균형 corpus 설계

날짜: 2026-09-08  
상태: 구현·실행 검증 완료  
대상: `data_generation/v1`의 `corpus_vtol`, `models/gru/v1`의 window index 소비

## 목적

기존 VTOL 64-Episode corpus는 64개 상승×순항×하강 조합을 한 번씩 포함하지만,
Episode split과 좌·우 방향이 난수로 정해져 Train 안의 행동 수와 실제 GRU target
노출량이 달라졌다. 새 corpus는 기존 결과를 보존하면서 각 split의 핵심 행동과
좌·우 방향을 균형화하고, 모델이 실제로 읽는 Train window도 같은 원칙으로 고정한다.

## 생성 계약

- 새 `corpus_vtol`은 160 Episode다: Train 112, Validation 24, Test 24.
- 각 split에서 상승 4종, 순항 4종, 하강 4종의 Episode 포함 횟수는 정확히 같다.
- `turn`, `spiral`, `orbit`은 좌·우가 정확히 같다. `s_turn`의 좌·우는 첫 선회
  방향을 뜻한다. ENU에서 양의 yaw rate를 left, 음의 yaw rate를 right로 기록한다.
- 직선 행동에는 가짜 방향 label을 부여하지 않는다.
- Train은 전체 64개 상승×순항×하강 조합을 적어도 한 번 포함한다. 추가 조합은
  seed 고정 균형 block으로 배치한다. Validation/Test는 각 marginal과 방향을
  균형화하되 전체 64개 조합을 모두 포함한다고 주장하지 않는다.
- heading과 speed scale은 기존 seed 기반 난수를 유지한다. 방향은 schedule의
  명시적 입력으로 바꾸고 같은 seed에서 schedule과 궤적 bytes가 재현되어야 한다.
- 자연스러운 `spiral`/`orbit` 지속시간과 속도·가속도 제약은 바꾸지 않는다.

## Split과 공개 경계

- 균형 schedule이 Episode별 split을 명시하고 dataset writer가 ID 집합·허용 label·
  112/24/24 수를 검증한다. 기존 diagnostic/pilot과 구 corpus는 기존 난수 split을
  유지할 수 있다.
- 방향과 행동은 `evaluation/commands.csv` 및 `evaluation/behavior_balance.csv`에만
  기록한다. `public/observations.csv`, GRU feature, target에는 추가하지 않는다.
- Episode split 뒤에 window를 만들며 한 Episode의 파생 관측은 다른 split으로
  이동하지 않는다.

## GRU window 균형

- generator는 91 sample이 하나의 핵심 행동에 완전히 포함되는 Train 후보만
  핵심 균형 pool에 넣는다. 16개 입력과 75개 target이 같은 행동에 속하므로
  핵심 행동별 실제 target point 수가 정확히 같아진다.
- 12개 핵심 행동마다 256개 Train window를 선택한다. 선회성 행동은 좌·우
  128개씩이다. 선택은 seed 17과 Episode ID로 재현한다.
- 행동 전환 학습을 잃지 않도록 각 핵심 행동의 시작·종료 경계마다 별도 transition window를
  추가하고 `window_role=transition`으로 audit한다. transition pool은 핵심 행동의
  균형 통계와 분리한다.
- Validation/Test는 기존 75-sample 고정 stride를 유지하고 균형 재표집하지 않는다.
- 모델은 dataset이 제공한 versioned 91-sample index를 명시적으로 사용한다.
  index가 없거나 길이·split·variant·hash가 다르면 자동 random fallback 없이 오류다.

## 산출물과 완료 조건

- 새 dataset ID를 사용하며 기존 64-Episode corpus와 학습 run을 수정하지 않는다.
- `evaluation/behavior_balance.csv`에는 split, phase, behavior, direction, episodes,
  duration_s, core_windows, target_points를 기록한다.
- Train/Validation/Test 행동 marginal과 방향 수, Train 핵심 window 및 target point
  수가 계약과 다르면 publication을 실패시킨다.
- config, source, schedule, index, balance audit와 manifest SHA-256을 보존한다.
- 완료는 focused/full tests, Ruff, 실제 160-Episode 생성, balance audit와 manifest
  hash 검증을 모두 통과했을 때만 보고한다.

## 해석 경계

균형 corpus는 합성 학습량 편향을 줄이지만 모델 정확도 향상을 보장하지 않는다.
단일 대표 경로 GRU의 다중 미래 평균화, 3초→15초 장기 외삽, 모델 용량과 최적화는
별도 원인으로 남는다. 행동·방향 label은 sampling과 평가용이며 inference 입력이 아니다.
