# 시각화 v1 — 궤적 재생

- [README](README.md), [생성 설계 5절](../../plan/data_generation_v1_design.md), [실행 checklist](../../plan/x8_v1_implementation_checklist.md)를 읽는다.
- 첫 입력은 공통 형식을 통과한 X8 3D 데이터다. 지원 버전·좌표·단위를 확인하고 미지원 형식/차원은 명확히 거부한다.
- 로컬 Dash·Plotly Browser viewer와 server-side PNG export를 사용한다. 단일 Episode 검사·최대 4개 Episode 비교를 지원하며, 넓은 화면에서는 XY/XZ/YZ/3D를 2×2로, 좁은 화면에서는 세로로 배치한다.
- 초반 XY·XZ·YZ·3D 범위를 유지하며 동일 데이터의 재생/일시정지/시간 탐색을 동기화한다. 같은 시각의 위치가 모든 보기에서 일치해야 한다. Episode overlay와 다중 물체 상호작용 생성을 혼동하지 않는다.
- 3D의 회전·확대는 카메라만 바꾼다. 원래 좌표를 재생성하거나 수정하지 않는다.
- 시간 역탐색·첫/마지막 시점·일시정지·배속·보기 전환을 검증한다. 결측 구간을 정상 연속 관측처럼 숨기지 않는다.
- 경로·현재 위치·ENU 축/단위·데이터 ID·출처·true/observed 구분과 속도·가속도·기동 구간을 표시한다. 평면의 동일 길이 축 척도를 기본으로 제안하며 파생값 계산/평활화 출처를 밝힌다.
- PNG 결과는 새 run ID의 `outputs/visualization/v1/<run_id>/`에 저장하며 코드·원본 폴더에 넣지 않는다.
- 예측 점유 영역 UI는 별도 후속 범위이다. RED-SDS 인계가 왔다고 첫 궤적 재생에 미구현 모델을 연결하지 않는다.
