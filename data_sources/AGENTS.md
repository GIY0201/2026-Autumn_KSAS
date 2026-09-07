# 외부 원본 지침

- [README](README.md)와 [생성 설계 3절](../plan/data_generation_v1_design.md)을 먼저 읽는다. source별 예외가 생기면 그 폴더에만 추가 지침을 둔다.
- 원본은 출처·기체별 `data_sources/<source_id>/`에 보관하고 가공본으로 덮어쓰지 않는다. 출처 URL/DOI·버전·license·취득일·checksum·기체·열/단위 설명을 기록한다.
- 실제 source를 확보할 때만 폴더를 만든다. 다운로드·수치 점검·모델 재현·검증은 서로 다른 상태다.
- 루트의 `Holybro Pixhawk.zip`은 사용자 제공 원본이다. 현재 위치를 유지하고 정리 작업이라는 이유로 이동·수정·삭제하지 않는다. X8 원본으로 오인하지 않는다.
- X8 원본과 Holybro/Ranger를 섞어 하나의 기체 운동모델로 식별하지 않는다. 별도 데이터의 적합성은 별도 근거로 판단한다.
- 원본의 추정 상태·보정·보간·필터링·시간 정렬을 명시한다. EKF를 정답, 무풍 추정값을 실측 무풍, 보간 빈도를 센서 빈도로 바꾸어 표현하지 않는다.
- 원본 TRAIN/VALIDATION 구분을 보존한다. 현재 미열람인 X8 VALIDATION은 설정 고정 전 튜닝에 사용하지 않는다.
- 가공 결과는 `outputs/data_generation/<version>/<dataset_id>/`에 두고 원본 식별자와 변환 기록을 연결한다. 단편 이어붙이기를 실제 연속 비행으로 표시하지 않는다.
