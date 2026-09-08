# Model training runtime v1

## Validation-only 진단 (2026-09-08)

`evaluation.mode=validation_only`는 test loader와 test 평가를 건너뜁니다.
완료 receipt와 `evaluation/protocol.json`의 `test_evaluated=false`, `test_passes=0`으로
구별합니다. 생략 시 기존 `test_after_training` 정책을 유지합니다.
`initialization.json`은 resume 복원 전 factory 초기 tensor hash를 기록합니다.
매 epoch `train_eval_*`와 `eval_*`에 가중치 적용 전 XYZ Huber, XYZ RMSE,
ADE/FDE를 기록합니다. train 진단은 eval 모드에서 수행하며 RNG를 복원하고,
early stopping·best 선택은 validation ADE만 사용합니다.

`weighted_huber_x/y/z`는 각 축의 가중 loss 기여량, `loss_share_x/y/z`는 전체에서
차지하는 비율입니다. `diagnostics/epoch_<global_step>/<split>/`에 고정 subset의
위치·행동 상세를, `diagnostics/best/validation/`과 `diagnostics/last/validation/`에
전체 validation 상세를 저장합니다. generator event는 forward 이후에만 연결합니다.
test 상세는 허용된 최종 평가의 prediction을 재사용하며 validation-only에서는 생성하지 않습니다.

프로젝트 내부 plugin registry, 학습 child process, checkpoint·preset과 Dash Training tab의 service 계층입니다. `TrainingService(project_root)`가 실행 생명주기를 소유합니다. 외부 package plugin 자동 발견은 제공하지 않습니다.

## API와 실행

- `registry.get_plugin(id)`, `list_plugins()`: descriptor 조회. 미등록 ID와 중복 등록은 오류입니다.
- `TrainingService.start(config, display_name, parent_checkpoint=None)`: 설정 검증, 새 run 생성과 worker 시작. 한 번에 하나의 학습만 실행합니다.
- `stop(run_id)`, `status(run_id)`, `runs()`, `metrics(run_id)`, `predict(run_id, checkpoint="best")`: 명시적 run 조회·제어. `predict`는 저장한 validation preview를 제공합니다.
- `diagnostic_windows(run_id, checkpoint)`와 `diagnostic_window(run_id, checkpoint, sequence_id)`: 저장한 validation 상세 조회. 원본 관측 재로딩이나 새 추론 없이 움직임 분류·오차·좌표·generator annotation을 반환합니다.
- `save_preset(name, config)`, `presets(plugin_id)`: 새 ID로 preset을 저장합니다. 같은 이름도 기존 파일을 덮어쓰지 않습니다.

CLI argument는 `--config`, `--name`, 선택적인 `--parent-checkpoint`와 OmegaConf dotlist override입니다. `--help` 실행을 확인했습니다. 실제 학습에는 dataset 경로를 명시해야 합니다.

```powershell
.\.venv\Scripts\python.exe -m models.runtime.v1 --help
.\.venv\Scripts\python.exe -m models.runtime.v1 --config models/gru/v1/configs/default.yaml --name "GRU baseline" "data.dataset_path=<dataset 절대경로>"
```

기존 `127.0.0.1:8088` 앱의 Training tab에서도 설정·preset·학습·중지·checkpoint 선택이 가능합니다. CUDA를 선택했는데 사용할 수 없으면 CPU로 자동 전환하지 않습니다. worker는 Web callback과 별도 process에서 실행합니다. 상태 파일과 종료 receipt로 완료를 확인합니다.

## Checkpoint와 resume

새 기본 설정은 `training.cudnn_enabled=false`로 CUDA의 native GRU 경로를 사용합니다.
현재 Windows/PyTorch 환경의 cuDNN GRU train-mode 종료 오류를 피하기 위한 명시적
backend 설정이며 GPU·FP32·모델 구조는 유지합니다. 기존 raw config에서 이 항목이
없으면 worker는 이전 `true` 동작을 유지합니다. 비교 실험의 모든 조건과 checkpoint
replay에는 같은 backend를 적용하고 완료 receipt에도 값을 기록합니다.

`best`는 validation ADE로 선택하며 `last`는 이어서 학습할 상태입니다. resume에는 weights, optimizer, scheduler, RNG, Trainer state가 필요합니다. 원본 run을 보존하고 새 child run의 `parent_run`과 `parent_checkpoint`로 lineage를 기록합니다. 설정은 `training.max_epochs`만 유지·연장할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m models.runtime.v1 --config "outputs/models/gru/v1/<parent_run>/effective_config.yaml" --name "GRU 이어서 학습" --parent-checkpoint "<parent_run>/last" training.max_epochs=250
```

같은 목표 epoch로 재개하면 기존 schedule을 복원합니다. 목표를 늘리면 복원한 LR에서 새 cosine 구간을 연결합니다. 이미 schedule 끝에 도달하여 LR이 0이면 설정 LR로 warm restart하고 `schedule.json`에 정책을 기록합니다. 따라서 연장된 run의 최적화 경로가 처음부터 긴 epoch로 실행한 경우와 같다고 주장하지 않습니다.

## 산출물과 평가 경계

run별로 `effective_config.yaml`, `run.json`, `status.json`, `window_index.csv`, `normalization.json`, `data_sources.json`, `manifest.csv`, `schedule.json`, `logs/`, `tensorboard/`, `checkpoints/best/`, `checkpoints/last/`를 보존합니다. manifest는 artifact SHA-256을 제공하고 source lineage에는 실제 읽은 split observation hash를 기록합니다. preset은 plugin의 `_presets/` 아래에 저장합니다.

train-only normalization 이후 validation으로 checkpoint를 선택합니다. 완료 시 best checkpoint로 test를 한 번 평가하고 같은 window에 Constant Velocity를 비교합니다. CV는 16개 관측 전체의 least-squares velocity와 마지막 관측점 anchor를 사용합니다. 평가 target은 미래 관측 위치입니다. test 평가 시작 receipt가 있는 run은 다시 실행하지 않습니다.

TensorBoard event와 CSV/JSONL metric을 함께 저장합니다. 별도 TensorBoard 서버는 실행하지 않으며 Dash가 event를 읽어 graph를 표시합니다. 실패·중지 log도 보존합니다. 전체 테스트·Browser 결과, 실제 run ID와 baseline metric은 상위 구현 보고서에서 확인해야 합니다. 이 문서는 추론 latency나 실제 환경 일반화 검증을 뜻하지 않습니다.
