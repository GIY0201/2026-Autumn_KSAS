# GRU Direct v1 구현 기록

2026-09-08 사용자 승인: 3초 16개 관측 위치·시간으로 15초 75개 위치를 직접
예측하는 GRU baseline과 8088 Training tab, 전체 VTOL baseline 실행.
이 승인은 기존 GRU 폴더의 미구현 상태를 변경한다. 기존 회랑/RED-SDS 연구
문서의 의미는 변경하지 않으며 이번 구현에 점유영역·물체 크기를 넣지 않는다.

## 구현 체크리스트

- [x] Model/data: GRU 128×2, MLP256, train-only p95 scale, 고정 window index
- [x] Runtime: plugin registry, Trainer worker, best/last, stop/resume, SHA-256
- [x] UI: 설정/preset, graph, run/checkpoint 선택, prediction 확인
- [x] 검증: unit, synthetic overfit, lifecycle/resume, browser, 기존 회귀
- [x] 전체 VTOL sigma1 학습 및 best checkpoint test 1회/CV 비교

## 구현 판단 기록

| 연결 | 생산/소비 계약 | 결정 |
|---|---|---|
| data → model | input_features[B,16,4], labels[B,75,3] | 마지막 위치 원점, 공통 scale |
| plugin → runtime/UI | descriptor, defaults, schema, factories | 명시적 registry; 자동 최신 없음 |
| runtime → UI | status, JSONL metrics, validation preview | child process, 오류 노출 |
| resume → scheduler | 상태 복원 + epoch 연장 | 기존 LR 연속성 보존; 연장 정책 기록 |
| training → test | best 선택 완료 뒤 load_split(test) | test 기반 tuning 없음 |

Constant Velocity 비교는 noisy 마지막 두 점 대신 전체 16개 관측 위치·시간의
least-squares 속도를 사용하고 마지막 관측점에서 외삽한다. simulation truth는
비교에 사용하지 않는다. 정량 평가 대상은 미래 sigma1 관측 위치다.

작업은 기존 feature branch에서 수행하고 자동 commit/push는 하지 않는다.
공통 학습 service는 models/runtime/v1에 두고 모델별 구현과 분리한다.

## 실행 증거

소프트웨어/학습 산출물: complete. 연구 성능 우위/실기체 일반화: 입증하지 않음.

- `.venv/Scripts/python.exe -m pytest -q --basetemp temp/gru-stable-final`:
  **357 passed**, 4개 Transformers filesystem mtime 경고, 130.42초.
- `node --test visualization/v1/tests/camera_interaction.test.cjs`: **1 passed**.
- 모델/worker/UI 검토: code-reviewer 재검토 APPROVE, 추가 blocker 없음.
- CUDA 확인: PyTorch 2.11.0+cu128, RTX 4060, Transformers 4.57.6.
- 실제 Browser: parameter 저장, 학습 시작, 안전 중지, last resume,
  완료 run 선택, best validation 3D 예측과 populated graph 확인.
- 최종 responsive 수정 후 UI 테스트 **12 passed**, scoped Ruff PASS.
  실제 390px viewport에서 완료 run graph를 선택한 뒤 document width375px로
  가로 넘침이 없음을 확인하고 기본 desktop viewport로 복원했다.
- TensorBoard event를 service에서 읽어 표시하며 별도 port를 열지 않는다.

### Baseline 결과

완료 run: `outputs/models/gru/v1/20260908T111823_97a15ae85379/`.
train/validation/test windows = **2816/268/325**. Train-only scale =
**405.9862885504265 m**. best는 epoch30, early stopping은 epoch50/step2200.

| 모델 | Test ADE(m) | Test FDE/15초(m) | 1초(m) | 3초(m) | 5초(m) | 10초(m) |
|---|---:|---:|---:|---:|---:|---:|
| GRU Direct |47.9527|117.6759|4.5663|12.8330|23.5632|62.4742|
| Constant Velocity |42.7994|112.6240|3.0509|8.5788|17.8598|56.1796|

GRU는 이 설정에서 CV보다 ADE/FDE가 높다. 결과를 기반으로 parameter를
추가 tuning하지 않았다. 비교 대상은 noisy 미래 관측이며 실제 위치 오차로
부르지 않는다. 합성 VTOL 외 일반화, 0.2초 추론 지연은 검증하지 않았다.

`manifest.csv`의 **48개 파일 SHA-256 전부 일치**를 확인했다.
best model SHA-256:
`0bfd7c7b9f8f92bdd7c4274a3bd1d2f3eb1d970b17dae65a5d65866f00389271`.
success receipt, best/last 전체 상태, metric CSV/JSONL/TensorBoard,
window/normalization/source provenance/test 평가 파일 모두 존재한다.

### 실행 lineage와 절차 이탈

1. `20260908T111233_f1e25b14d0ac`: UI smoke(train44 windows)가 35epoch에서
   약3초 만에 끝나 Stop 전에 자동 test 평가가 수행됐다. 이 결과를 parameter
   선택에 사용하지 않았지만 **dataset이 마지막 run에서 최초로 평가됐다고
   주장할 수 없다**. 최초 1회 test-only 의도와의 절차 이탈로 보존한다.
2. `20260908T111452_524c24cdae59`: 전체2816 window로 시작하고 브라우저 Stop,
   step1 checkpoint 저장. test 미평가.
3. `20260908T111522_37c691e7fb44`: 브라우저 last resume. epoch14 상태 파일
   교체 중 Windows sharing PermissionError로 종료. epoch13 checkpoint 보존,
   test 미평가. 공통 atomic writer의 bounded retry와 회귀검사를 추가했다.
4. `20260908T111823_97a15ae85379`: 실패 run의 last에서 복원해 완료.
   동일 parameter·window·normalization, 마지막 run 안에서 best test 1회.

부모의 파일을 덮어쓰지 않았다. 각 run elapsed_s는 해당 프로세스 구간 시간이며
부모를 포함한 총 벽시계 실행 시간이 아니다. 중간 실패 checkpoint 이후 step은
재실행됐다. 실행 당시 source/uv.lock hash는 각 provenance에 보존한다.

### 화면 방향

IBM Carbon 참고의 흰색/회색·평면 구분선 방향을 적용했다. 사용자 요청에 따라
brand 홍보 typography·색상은 적용하지 않고 기존 Segoe UI/Malgun Gothic을
유지한다. 그래프는 meter와 horizon 의미를 보존하며 3D preview는 aspect=data다.
Web Interface Guidelines의 label, focus-visible, semantic button, live status,
responsive overflow 항목을 점검했다. 완전한 WCAG 인증으로 표현하지 않는다.
