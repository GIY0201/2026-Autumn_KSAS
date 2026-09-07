# 실행 결과

이 폴더는 구현 실행의 영속 산출물만 보관합니다. 코드·원본·임시 test file을 넣지 않습니다.

| 결과 종류 | 저장 위치 | 현재 확인된 실행 |
|---|---|---|
| 생성 데이터 | `data_generation/<version>/<dataset_id>/` | `data_generation/v1/ccc2b760-0b3e-46c9-a565-ff224e74f8df/` |
| 모델 학습·평가 | `models/<model_name>/<version>/<run_id>/` | 없음 — 모델 미구현 |
| 시각화 PNG·기록 | `visualization/<version>/<run_id>/` | `visualization/v1/090b520c-ecfd-4124-85ee-14d3d8ed82d5/` |

각 실행은 새 ID를 만들고 기존 결과를 덮어쓰지 않습니다. `final`, `final2`, `수정본`, `최종_진짜` 같은 이름과 latest 자동 선택을 사용하지 않습니다.

각 result folder의 `manifest.csv`에는 code/contract version, input data ID, code hash, seed 또는 view state, 설정·environment 위치, status를 기록합니다. `files.csv`는 보관 대상 파일의 SHA-256을 기록합니다.

생성 결과의 public/evaluation 경계는 [contracts/v1/README.md](../contracts/v1/README.md), export 동작은 [visualization/v1/README.md](../visualization/v1/README.md)에 설명되어 있습니다. 파일 배치의 상위 규칙은 [project_structure.md](../plan/project_structure.md)를 따릅니다.
