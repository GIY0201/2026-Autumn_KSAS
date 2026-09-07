# GRU v1

상태: **폴더 준비만 완료, 기능 미구현**.

[상위 지침](../../AGENTS.md)과 [GRU v1 지침](AGENTS.md)을 먼저 읽습니다. 별도 작업의 최신 RED-SDS 합의는 [모델 설계 인계](../../../plan/model_design_handoff.md)에 기록했습니다. 기존 GRU 기준은 보존하지만, 이 폴더의 존재는 GRU 구현 승인이 아닙니다. RED-SDS를 이 폴더에 넣지 않습니다.

이 폴더는 향후 GRU의 학습·추론·평가 코드와 해당 버전의 설정·테스트를 보관합니다.

- `configs/`: 모델·학습·평가 설정을 둘 자리. hyperparameter는 아직 정하지 않았습니다.
- `tests/`: 이 모델 버전의 테스트를 둘 자리. 현재 테스트 코드는 없습니다.
- 학습·평가 결과 위치: `outputs/models/gru/v1/<run_id>/`.

checkpoint, 예측 결과와 평가표는 코드 폴더에 넣지 않습니다. 다른 모델 종류를 이 폴더의 다음 버전으로 관리하지 않습니다.

파일 배치 규칙은 [project_structure.md](../../../plan/project_structure.md)를 따릅니다. 이번 폴더 준비는 연구 기준 문서의 GRU 설계를 변경하지 않습니다.
