# GRU v1

## 상세 진단과 여러 seed의 축별 loss 비교

새 run은 `diagnostics/subset_index.csv`에 고정 진단 window를 기록합니다.
관측 기반 XY 이동 여부와 수직 상승/유지/하강의 6개 그룹에서 최대 2개씩 선택하고,
매 epoch train/validation의 해당 window를 저장합니다. `best/last`에는 전체
validation의 `points.csv`, `windows.csv`, `provenance.json`을 보존합니다.
입력 16점과 미래 75점의 관측·예측 XYZ, 시간, 오차를 연결하며 generator event와
전환 시각은 forward 이후 별도 진단 정보로만 연결합니다. 관측 움직임 분류는
15 m 기준이며 generator의 비행 상태 판정과 동일한 의미가 아닙니다.

Training 화면의 `Validation window 상세 진단`에서 명시적인 run/checkpoint와
window를 선택하면 움직임 필터, Z RMSE/ADE 순서, 3D·고도 곡선과 event를 봅니다.
기존 상세 로그가 없는 run은 누락 상태를 표시합니다.

`python -m models.gru.v1.weight_sweep --reference-run <기존 run 절대경로>`는
`configs/weight_sweep.yaml`의 Z weights 1/2/3/5와 모델 seed 17/29/43을 비교합니다.
`data.window_seed=17`로 학습 window를 고정하며, 해당 key가 없는 기존 설정은
`training.seed`를 sampling seed로 사용합니다. 모든 실험은 validation-only이고
선택 기준은 seed별 best validation ADE 평균입니다. default weight는 바꾸지 않습니다.
축별 Huber, 가중 loss 기여량과 기여 비율을 epoch 로그에서 함께 확인할 수 있습니다.

## Z loss 진단 실험 (2026-09-08)

`model.axis_loss_weights`는 양수 XYZ 가중치 배열이며 기본값은 `[1,1,1]`입니다.
원소별 Huber에 가중치를 곱한 후 가중치 합으로 나눕니다. `[1,1,5]`는 Z 오차의
상대 기여도를 늘리는 진단 조건이며 좌표 normalization·모델 구조는 바꾸지 않습니다.
이 값은 config에 저장하며 checkpoint tensor 구조를 변경하지 않습니다.

`python -m models.gru.v1.ablation --reference-run <기존 run 절대경로>`는 기존 config,
window와 normalization을 기준으로 두 조건을 새 run에서 순차 학습합니다.
test는 평가하지 않으며 초기화·window·normalization hash 일치 여부를 검증합니다.
best/last의 validation 지표·고도 변화별 오차·Episode별 지표와 그림은 새
`outputs/visualization/v1/<run_id>/`에 저장합니다. 단일 seed의 진단 결과입니다.

GRU Direct model/data/plugin 및 상세 진단이 구현되어 있습니다. 실제 학습·Browser 검증과
결과 경로는 [상세 진단 실행 기록](../../../plan/gru_detailed_diagnostics.md)과 상위 구현 보고서에서 확인합니다.

[상위 지침](../../AGENTS.md)과 [GRU v1 지침](AGENTS.md)을 먼저 읽습니다. 사용자가 별도로 승인한 GRU Direct baseline이며 기존 점유 회랑·RED-SDS를 포함하지 않습니다.

이 폴더는 GRU의 학습·추론·평가 코드와 해당 버전의 설정·테스트를 보관합니다.

- `configs/default.yaml`: 승인한 모델·학습 parameter의 기본값.
- `tests/test_direct.py`: shape, gradient, 좌표 복원, 큰 원점 정밀도, synthetic 직선 overfit, split 격리, 관측 검증, normalization, checkpoint 검증.
- 학습·평가 결과 위치: `outputs/models/gru/v1/<run_id>/`.

checkpoint, 예측 결과와 평가표는 코드 폴더에 넣지 않습니다. 다른 모델 종류를 이 폴더의 다음 버전으로 관리하지 않습니다.

파일 배치 규칙은 [project_structure.md](../../../plan/project_structure.md)를 따릅니다.

## 입력·출력 API

입력은 `position_enu_m[B,16,3]`, `timestamp_s[B,16]`이며 5 Hz·최근 3초의 유효한 관측입니다. 마지막 관측 위치·시간을 float64로 뺀 후 XYZ를 train scale, 시간을 3초로 나누고 FP32 feature `[B,16,4]`로 변환합니다. Unix timestamp와 큰 좌표 원점의 상대 정밀도를 보존합니다.

단방향 2-layer GRU hidden 128, dropout 0.1과 MLP 128→256→225, GELU로 75개 미래 위치를 동시에 예측합니다. `model.GRUDirectModel(config).forward(input_features, labels=None)`는 Trainer용 `logits`와 선택적인 `loss`를 반환합니다.

`predict_positions(position_enu_m, timestamp_s, scale_m)`는 `relative_position_m[B,75,3]`, `position_enu_m[B,75,3]`, `horizon_s[75]`를 반환합니다. 미래 시간은 0.2~15초이며 절대 위치는 float64로 복원합니다. `metrics(predictions, labels, scale_m)`는 normalized prediction/target을 meter로 복원하여 ADE, FDE, 1·3·5·10·15초 오차와 축별 RMSE를 계산합니다.

## 데이터와 설정

target은 미래 `sigma_1m` 관측 위치입니다. simulation truth, command, identity와 행동·방향 label은 입력·target에 포함하지 않습니다. 91개 연속 sample 중 앞 16개가 입력, 뒤 75개가 target입니다. 행동 균형 dataset은 inventory SHA-256으로 검증한 `training/balanced_windows.csv`의 시작점을 그대로 사용하며, 파일이 없거나 변조되면 random window로 대체하지 않고 실패합니다. 일반 dataset의 train window는 설정된 개수를 seed로 선택합니다. validation/test는 75-sample stride이며 마지막 off-stride window를 추가하지 않습니다.

`data.prepare_data(dataset_path, config, output_dir)`는 train/validation Dataset, metadata 기반 전체 window index와 normalization을 반환하며 test 관측을 열지 않습니다. train window들의 미래 상대위치 L2 거리 P95 하나를 XYZ 공통 scale로 사용하고 clipping하지 않습니다. source·target·window index SHA-256을 저장합니다.

`data.load_split(dataset_path, split, config, normalization)`는 지정한 split만 읽습니다. Dataset `source_sha256`은 실제 읽은 CSV hash이며 `targets_m`은 상대 target, `anchors`는 마지막 관측점, `input_positions_m`과 `timestamps_s`는 원래 입력입니다.

기본값은 Huber delta 1, AdamW LR 0.0003, weight decay 0.0001, batch 64, 최대 200 epochs, validation ADE early stopping patience 20, cosine warmup 5%, clipping 1, FP32입니다. `cuda:0`이 없으면 시작 오류이며 CPU는 명시적으로 선택해야 합니다.

## 실행

프로젝트 루트에서 아래 CLI를 사용합니다. `--help` 실행과 focused pytest 명령을 확인했습니다. 실제 시작에는 dataset 절대경로를 명시해야 합니다.

```powershell
.\.venv\Scripts\python.exe -m models.runtime.v1 --help
.\.venv\Scripts\python.exe -m models.runtime.v1 --config models/gru/v1/configs/default.yaml --name "GRU baseline" "data.dataset_path=<dataset 절대경로>"
.\.venv\Scripts\python.exe -m pytest models/gru/v1/tests/test_direct.py -q --basetemp=temp/pytest-gru-check
```

runtime·resume 설명은 [runtime README](../../runtime/v1/README.md)를 참고하세요. 이 구현은 추론 latency나 실제 환경 일반화 성능 검증을 뜻하지 않습니다.
