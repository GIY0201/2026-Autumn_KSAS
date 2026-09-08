# 외부 원본 자료

논문·공개 데이터셋 등에서 확보한 원본을 출처와 기체별로 구분해 보관할 위치입니다.

- 확보한 원본: `x8_dataverse_2024/` — Skywalker X8 DataverseNO V1의 `00_README.txt`와 headerless CSV 17개. source DOI, checksum, split은 해당 폴더의 `README.md`에 기록합니다.
- 사용자 제공 `Holybro Pixhawk.zip`은 프로젝트 루트에 있으며 그대로 보존합니다. X8 원본으로 간주하거나 이 문서 정리 과정에서 이동하지 않습니다.
- 출처, 원본 버전, 기체, 이용 조건을 함께 기록하고 확보한 재현용 원본을 Git에 보존합니다.
- clean clone에서도 모든 생성 preset을 실행할 수 있도록 profile과 motion-reference가 참조하는 공개 원본 파일을 함께 추적합니다.
- 전처리·변환 결과로 원본을 덮어쓰지 않습니다. 가공 결과는 프로젝트 루트의 `outputs/`에 둡니다.

파일 배치 규칙은 [project_structure.md](../plan/project_structure.md)를 따릅니다.

원본을 다루기 전에 [이 폴더 지침](AGENTS.md)과 [생성 설계의 이전 점검 기록](../plan/data_generation_v1_design.md)을 확인합니다. 메모리 내 원본 점검 이력과 디스크에 자료를 보관한 상태는 구분합니다.
