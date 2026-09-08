# 독립 X8 참조 운동 수치 추출

날짜: 2026-09-07. 상태: 첫 X8 추출 함수 구현·독립 검토·14조건 실행 검증 완료. 질점 수치 보정과 실비행 검증은 미수행.

## 범위

사용자가 승인한 독립 Python 함수 방식으로 고정익부터 진행한다. 기존 `x8.py`의 동역학·RK4·trim과 `control.py`의 시험용 controller를 호출한다. PX4/Gazebo/WSL 설치·통신·8088 변경은 하지 않는다. 기존 모델의 코드·계수와 질점 profile은 보존한다.

흐름: 명시적 시험 설정 → baseline/entry/hold/recovery/post 연속 적분 → 조건별 실제 반응 CSV → 검토. 마지막 profile 보정은 후속이며 자동 반영하지 않는다.

## 파일 책임

- `data_generation/v1/x8_characterization.py`: 검증된 시험 조건, 부드러운 명령 전환, 독립 1회 시험 함수, ENU 상태·기동 수치 및 목표 tolerance 보고.
- `data_generation/v1/x8_characterization_run.py`: 명시 config/출력 경로를 받는 batch CLI, 새 UUID·CSV·provenance·환경·설정·hash 기록.
- `data_generation/v1/configs/motion_reference/x8_characterization.yaml`: 첫 시험 조건·시간·보고 기준. 수치의 역할과 근거를 함께 기재한다.
- `data_generation/v1/tests/test_x8_characterization.py`: 좌표·가속도·곡률 수학 fixture, 설정 거부, 실제 X8 적분·재현·출력 보존 검사.

## 의미와 검증 경계

- 모든 결과는 `REFERENCE_SIMULATION`, `REPORT_ONLY`다. 실제 기체 한계·실비행 검증·질점 데이터가 아니다.
- 전체 궤적을 임의의 위치로 이동·복구하지 않는다. 초기 airborne 위치는 시험의 좌표 기준이며 지상 이착륙 시나리오가 아니다.
- 신규 동역학 계수를 고르지 않는다. 시험 지속시간·명령 전환시간·목표 허용오차는 `experiment_protocol`이고 모델 성능으로부터 추출된 값이 아니다.
- ENU 속도와 관성 가속도로 수평 track 선회율/반경을 계산한다. body yaw rate와 구별한다. 직진·정지의 반경은 빈 값으로 표시하고 계산 불가를 0m로 바꾸지 않는다.
- 가속도는 기존 동역학 미분에 body 회전항을 더해 관성 좌표로 변환한다. IMU specific force나 5 Hz 관측 위치 차분이 아니다.
- 명령 bank와 실제 bank, 목표 속도/상승률과 실제 상태를 따로 기록한다. 회복 구간도 생략하지 않는다.
- hold/post 말단에서 설정된 시간 동안 목표 허용오차를 만족한 경우만 `terminal_target_met`를 표시한다. 이는 정상 비행 전체의 안정성 증명이나 모든 상태의 정착 판정이 아니다. 미수렴은 그대로 보고한다.
- 말단 판정은 CSV 저장 간격과 무관하게 모든 0.01초 control state에서 수행하고, 해당 구간의 시작·종료 상태를 모두 포함한다. 통계의 `sample_count`·최솟값·최댓값·중앙값은 CSV의 해당 phase 행을 사용한다. 연속 시간 전체나 더 짧은 physics substep에서의 허용오차 만족을 증명하는 것은 아니다.
- `t_s`는 상태 시각, `command_time_s`는 기록된 raw/applied command를 계산·갱신한 control 시각이다. 마지막 상태에는 다음 명령을 새로 계산하지 않으므로 마지막 command 시각은 상태보다 0.01초 이전이다. 중간 행의 가속도는 그 행에서 갱신한 명령, 마지막 행은 직전 명령으로 평가한다.
- 수치 실패나 기존 작업 영역 이탈은 실패 원인과 계산된 구간까지만 보존한다. 결과 성공률을 높이기 위해 threshold를 사후 조정하지 않는다.
- 내부 상태·참조 수치는 `evaluation/`에 저장하고 `public/`를 만들지 않는다. 일반 학습 dataset/UI에 자동 등록하지 않으며 기존 공통 dataset validator를 우회하지 않는다.
- 사용 코드·계수 snapshot·실행 설정·환경·seed(무작위 없음)·원문 DOI와 파일 hash를 남긴다. 같은 조건을 두 번 실행해도 결과 폴더는 다르고 수치 CSV는 재현되어야 한다.

## 첫 시험 구성과 미구현 범위

현재 설정은 16/18/20 m/s 각각 직선·좌선회·우선회 9개, 가속·감속·직선 상승·직선 하강·우선회 상승 5개로 총 14조건이다. 각 조건은 baseline 6초 → entry 3초 → hold 12초 → recovery 3초 → post 12초이며, 시작점을 포함한 36초·3,601개 상태를 기록한다.

이것은 조건별 반응을 확인하는 첫 참조 시험이며 기체의 전체 비행영역을 측정한 것이 아니다. 기존 결정 대조표의 연속 좌→우 선회 반전, 지상 이륙·착륙, 600초 전체 시나리오, 3도/10도 경로각 시험, quadrotor/VTOL/헬기 참조 수치 추출은 이 코드의 완료 범위에 포함하지 않는다. `terminal_target_met=False` 조건의 중앙값을 정상상태 성능으로 채택하지 않는다.

## 실행 안내

프로젝트 루트의 기존 Windows `.venv`를 사용한다.

```powershell
.\.venv\Scripts\python.exe -m data_generation.v1.x8_characterization_run --config data_generation/v1/configs/motion_reference/x8_characterization.yaml --output-root outputs/data_generation/v1
```

위 실행은 새 UUID 폴더에 시험별 위치·속도·가속도와 요약 CSV를 저장한다. 기존 60초 dataset 생성 CLI·8088 viewer는 변경하지 않는다.

## 실행 기록

### 독립 검토 및 회귀

- production 구현 전 failing test를 확인한 뒤 구현했다. 독립 code/Python 검토에서 clock 허용오차, hold 종료 경계와 저장 주기에 따른 목표 판정, 수치 실패 prefix 보존, 최종 command 시각 표기를 수정했다. 해당 회귀 6개는 수정 전 실패·수정 후 통과했다.
- 검토 후 추가한 예외 전파·저주기 저장 실패 prefix 검사까지 포함해 `test_x8_characterization.py`는 **24 passed in 1.93s**, exit 0이다. 독립 Python 재검토에서 원 지적 사항이 해소됐고, code reviewer도 clock 사전 거부를 확인했다.
- 첫 통합 회귀는 **217 passed in 120.46s**, exit 0이었다. 질점 Stage 1 검토 수정 후 source를 동결하고 재실행한 최종 통합 회귀는 **226 passed in 118.98s**, exit 0이다. `ruff check contracts data_generation visualization`과 `git diff --check`도 exit 0이다. 별도 질점 변경의 범위는 [Stage 1 검증 기록](point_mass_stage1_verification.md)을 따른다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-x8-characterization-final-suite
# 226 passed in 118.98s (0:01:58), exit 0
```

### 실제 14조건 실행

위 CLI를 실행해 `outputs/data_generation/v1/2fa46fa2-8b0d-4ab7-a930-aa2403e00b0d/`에 저장했다. 실행 exit 0, `status=complete`, `code_unchanged=True`, 실패 trial 0이다. 이는 요청된 계산이 완료됐다는 뜻이며 모든 명령에 목표대로 도달했다는 뜻이 아니다.

- [원시 상태 samples.csv](../outputs/data_generation/v1/2fa46fa2-8b0d-4ab7-a930-aa2403e00b0d/evaluation/samples.csv): **50,414행**, 14조건 × 3,601시점. 시간 grid·finite 위치/속도/가속도·마지막 command 시각을 독립 재검사했다.
- [조건·구간 summary.csv](../outputs/data_generation/v1/2fa46fa2-8b0d-4ab7-a930-aa2403e00b0d/evaluation/summary.csv): **70행**, 14조건 × 5구간.
- hold 말단 목표 충족은 **9/14**, post 말단 회복 목표 충족은 **14/14**다. 목표 기준은 설정의 속도 0.5 m/s·bank 1도·상승률 0.1 m/s 허용오차와 말단 2초 유지다.
- hold 미충족은 `left_18`, `right_18`, `left_20`, `right_20`, `turning_climb`이다. threshold를 바꾸지 않았고, 미충족을 실제 기체가 해당 기동을 할 수 없다는 판정으로 해석하지 않는다. 기존 시험용 controller와 주어진 시험 시간의 영향을 포함한 결과다.

아래는 목표를 충족한 16 m/s 선회 시험의 **허용오차를 연속 충족한 말단 구간만** 다시 집계한 값이다. 전체 hold 중앙값과 구별하며, 실제 기체의 일반적인 선회 성능으로 채택하지 않는다.

| 시험 | 집계 시각 | 실제 속도 중앙값 | 실제 bank 중앙값 | 수평 선회 반경 중앙값 |
|---|---|---:|---:|---:|
| 좌선회 | 18.81–21.00초 | 15.995 m/s | −9.007도 | 189.800 m |
| 우선회 | 14.12–21.00초 | 16.074 m/s | +9.026도 | 168.780 m |

입력은 각각 bank −10/+10도이며, 표의 실제 bank와 동일하지 않다. ENU track 선회율 부호는 좌선회가 양수, 우선회가 음수다. 이 두 결과의 좌우 차이를 확인했으며 평균내거나 대칭 성능으로 자동 보정하지 않았다.

### 수치 일관성 및 보존 확인

- 18 m/s 우선회 6초 시험을 physics dt 0.0025초와 0.00125초로 각각 다시 실행했다. 모두 601시점으로 완료됐고, 위치·속도·가속도의 최대 절대 차이는 각각 `5.0831e-8 m`, `2.0745e-8 m/s`, `6.1956e-7 m/s²`였다. 이 단일 시험의 수치 일관성 검사이며 실비행 충실도나 전체 기동 영역의 수렴 증명이 아니다.
- output의 `files.csv`에 기록된 **9개 파일 SHA-256**과 `source_hashes.csv`의 **현재 source/dependency 7개 SHA-256**을 재대조해 모두 일치했다. `public/`가 없는 참조 전용 output임도 확인했다.
- 기존 `x8.py`, `control.py`, `scenario.py`는 작업 전 SHA-256과 동일하다. model 계수·controller·기존 결과·production profile을 변경하지 않았다.
- Git commit/push, PX4/Gazebo/WSL 설치, 새 dependency 추가는 하지 않았다. 이 추출기는 listener를 만들지 않는다.

| 대상 | SHA-256 |
|---|---|
| code hash | `2116abc7ec5b2227bf7634dc4a2f4c983ae6b7c2017ba7f85fabcc104c3fb82b` |
| samples.csv | `ea98d99a8a4fd2262a689448ef56ca4b173a8d45406aee097183d596b2c52870` |
| summary.csv | `b334531b463b86f05b79ad70c0addc48bde48551a18d7e422832ebb12d97432d` |
| manifest.csv | `796f2a27ec1e1f40a86a19286061f7f3946e979a8e4734f8df7b74f0e311b40c` |

검토 전 첫 실행 `5e8e351c-89f6-4a51-a835-76ee51091693`은 덮어쓰거나 삭제하지 않았다. 당시 판정·command 시각 수정 전 코드의 기록이므로 채택 대상이 아니며 위 새 UUID를 명시해 확인한다.

다음 단계는 미충족·좌우 차이의 원인을 확인하고 채택할 조건별 참조 수치를 선정하는 일이다. 질점 profile 보정 및 다른 객체의 참조 모델 이식은 아직 수행하지 않았다.
