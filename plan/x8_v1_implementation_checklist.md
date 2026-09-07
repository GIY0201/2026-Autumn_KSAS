# X8-GEN-V1 구현 checklist

상태: `COMPLETE_VERIFIED`  
작성일: 2026-09-07  
근거: 사용자 승인된 X8 생성·관측 합성·XY/XZ/YZ/3D 재생 v1 명세. 이 문서는 실행 추적용이며 기존 GRU 기준이나 RED-SDS 인계를 바꾸지 않는다.

## 고정 경계

- 수치 XYZ 위치는 upstream observer가 제공한다고 가정한다. GPS, 지도/위경도, 카메라, 영상 검출·거리 추정·센서 융합은 제외한다.
- 실제 비행자료와 논문은 X8의 object-specific dynamics 근거 및 motion check에만 사용한다. source GPS 필드는 읽지 않는다.
- 신규 Episode는 single object, empty space, calm air, Local ENU, 60 s, 5 Hz, 301 samples다.
- public observation과 evaluation truth·command·diagnostic은 분리한다.
- 이번 실행은 X8만 포함한다. 예측 모델·점유영역·다른 object type은 별도 후속 범위다.

## 추적 항목

| ID | 작업 | 상태 | 통과 증거 |
|---|---|---|---|
| X8-00 | 루트·담당 `AGENTS.md`에 실행 범위와 검증 게이트 기록 | VERIFIED | 해당 지침의 현재 내용 |
| X8-01 | `uv` Python 3.12 project, 유효한 dependency lock, test runner 준비 | VERIFIED | `pyproject.toml`, `uv.lock`, `.venv`; 최신 `uv run pytest` 실행 |
| X8-02 | contracts v1: public/evaluation 타입, CSV I/O, strict validation | VERIFIED | contracts 4 tests와 최신 전체 suite 통과 |
| X8-03 | source reader: headerless X8 TRAIN motion-field mapping과 checksum/provenance | VERIFIED | source 3 tests, 원본 13 TRAIN/4 VALIDATION checksum·split 확인 |
| X8-04 | X8 6-DOF dynamics, propulsion, actuator delay, ENU conversion, calm trim | VERIFIED | dynamics 4 tests, 수치 trim residual gate 통과 |
| X8-05 | internal controller, event progression, continuous scenario generation | VERIFIED | scenario 3 tests, 실제 60초 diagnostic Episode 완료 |
| X8-06 | observation variants, split, manifests, result preservation | VERIFIED | dataset 4 tests, public contract·UUID 결과·manifest 확인 |
| X8-07 | Dash/Plotly XY/XZ/YZ/3D replay, controls, PNG export | VERIFIED | viewer 5 tests, Browser Play/Pause·shared step·four PNG export 확인 |
| X8-08 | X8 TRAIN motion check report, full test suite, requirement audit | VERIFIED | `REPORT_ONLY` TRAIN motion report, `25 passed`, Ruff 통과, 실제 output manifest/hash 확인 |

## 실행 규칙

1. 각 ID는 production code보다 failing test를 먼저 작성·실행한다.
2. test가 기대한 이유로 실패한 뒤에만 최소 구현을 추가한다.
3. `VERIFIED`는 해당 focused test와 전체 suite가 최신 실행에서 통과했다는 출력이 있을 때만 쓴다.
4. `BLOCKED`는 원인·영향·다음 안전한 조치를 기록하되, 실패를 성공으로 바꾸지 않는다.
5. X8-08 전에는 구현·source motion check·과학적 일반화를 같은 주장으로 합치지 않는다.

## 완료 근거와 해석 제한

- 최신 전체 실행: `uv run pytest -q --basetemp temp\pytest-final` → `25 passed in 63.22s`.
- 정적 검사: `uv run ruff check contracts data_generation visualization` → `All checks passed`.
- 실제 생성 dataset: `outputs/data_generation/v1/ccc2b760-0b3e-46c9-a565-ff224e74f8df/`.
- 실제 Browser export: `outputs/visualization/v1/090b520c-ecfd-4124-85ee-14d3d8ed82d5/`.
- X8 source motion check는 fixed-model residual report이며 acceptance threshold가 없다. 따라서 VERIFIED는 구현·schema·실행 경로의 검증을 뜻하며, 실비행 성능 보장·field validation·다른 기체 일반화를 뜻하지 않는다.
