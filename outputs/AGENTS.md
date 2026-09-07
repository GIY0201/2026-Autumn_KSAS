# 실행 결과 지침

- [README](README.md)와 [배치 규칙 5절](../plan/project_structure.md)을 먼저 읽는다. 원본은 여기가 아니라 `data_sources/`에서 관리한다.
- 생성: `data_generation/<version>/<dataset_id>/`, 모델: `models/<model_name>/<version>/<run_id>/`, 시각화: `visualization/<version>/<run_id>/`. 경로는 이 폴더 기준이다.
- 실제 실행 때 새 ID를 만들며 기존 결과를 덮어쓰지 않는다. 코드 버전과 실행 횟수를 혼동하지 않고 자동 최신 선택을 금지한다.
- `manifest.csv`에 코드/형식 버전·입력 ID·코드 hash·seed·설정/환경 기록 위치를 추적 가능하게 남긴다. 원본·episode·분할·부모 실행 관계도 필요한 범위에서 연결한다.
- 모델 결과에는 checkpoint와 해당 평가 결과를 함께 둔다. 부분 실행·실패·완료·검증 상태를 구분하고 실패 결과를 성공으로 바꾸지 않는다.
- 관측 입력과 평가용 정답·명령·내부 상태를 분리한다. 파일이 같은 실행 폴더에 있더라도 학습기가 모든 CSV를 자동 입력하지 않게 한다.
- `.gitkeep`나 빈 폴더는 실행 증거가 아니다. 이번 문서 작업에서 가짜 ID·빈 manifest·예시 checkpoint를 만들지 않는다.
- 결과 삭제·이동은 사용자가 승인한 정확한 대상만 처리한다. 다른 실행의 정리를 임시파일 청소에 포함하지 않는다.
