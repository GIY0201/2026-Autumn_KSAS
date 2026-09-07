# 공통 형식 v1 — 설계 단계

- [README](README.md), [생성 설계 4절](../../plan/data_generation_v1_design.md), [실행 checklist](../../plan/x8_v1_implementation_checklist.md)를 읽는다.
- 공통 좌표는 Local ENU(x=East, y=North, z=Up), 위치 m·시간 s이다. 기본 생성 Episode는 60초·5 Hz·301개다. X8-GEN-V1의 원점은 synthetic local origin이며 WGS84/GPS 해석을 하지 않는다.
- 생성 표형 데이터는 모두 CSV다. 최종 파일/접근 분리·열·검증 schema는 아직 없으며 원본 ZIP/ULog·checkpoint·PNG를 CSV로 변환하라는 뜻이 아니다.
- 물리 적분 간격과 학습 context 길이는 각각 0.2초 관측 간격과 60초 저장 길이에서 자동 결정하지 않는다.
- 합성 pilot은 물체 종류별 Episode 단위 70/15/15로 분리하고 학습 구간은 무작위·평가 구간은 고정한다. 공개 X8 원본 TRAIN/VALIDATION의 분할과 구별한다.
- X8 원본의 body/NED와 외부 공통 좌표를 구분한다. GPS 지리각 단위·XYZ 원점/좌표를 확인하기 전 추정 변환을 하지 않는다.
- public 입력은 `episode_id`, `variant_id`, `step`, `t_s`, `x_m`, `y_m`, `z_m`, `sigma_x_m`, `sigma_y_m`, `sigma_z_m`, `valid`만 포함한다. truth·명령·실제 오차·내부 상태는 evaluation reader에서만 읽는다.
- 정답·명령·잠재 상태·오차 실현값은 예측 입력과 학습 데이터 인터페이스에서 제외한다. 평가에서만 정답을 결합한다.
- RED-SDS 출력 표본 개수·voxel tie 처리·불확실성 표현·결측 처리·manifest 열은 미확정이다. 인계의 목표값과 상세 형식 확정을 구분한다.
