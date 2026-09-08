# Task 1 variable-length contract/writer 검증

2026-09-08. 범위: `scenario_generator_integration.md` Task 1. 상태: scoped implementation complete; 전체 full-flight 통합 및 독립 review는 부모 작업에서 수행한다.

## 변경

- `contracts/v1/validation.py`: 공통 5 Hz를 유지하고 Episode별 2..3001 samples, duration/count/dt 정합성과 모든 variant의 길이·step을 검증한다.
- `contracts/v1/csv_io.py`: 명시한 evaluation truth만 읽으며 sibling `public/episodes.csv`의 Episode ID/count/time에 대조한다. 누락·중복·미등록 ID·비균일 timestamp는 거부한다.
- `data_generation/v1/dataset.py`: MotionEpisode의 실제 길이로 observation/truth/metadata를 기록한다. 2 samples의 gradient도 지원한다. snapshot은 `episode_sample_counts`를 기록하고 서로 다른 길이면 공통 `sample_count`는 null이다.
- `contracts/v1/tests/test_contracts.py`: 명시 evaluation 경로 테스트의 기존 단일 truth row fixture를 metadata와 일치하는 301개로 수정했다.
- `data_generation/v1/tests/test_variable_length_dataset.py`: 실제 writer/readers를 사용한 120초 왕복, 2/3001개 경계, 혼합 길이, 잘못된 metadata와 sequence 거부를 검증한다.
- `contracts/v1/README.md`: 가변 길이와 evaluation metadata 대조를 설명한다.

기존 GeneratedEpisode의 complete 조건은 301 samples 그대로다. public 열/allowlist와 split grouping 및 source provenance 검사는 유지했다. 시작 시 있던 X8 point-mass provenance 관련 dirty 수정은 보존했다.

## 실행 증거

```powershell
.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_variable_length_dataset.py --basetemp temp/pytest-variable-red
# 16 failed, 4 passed in 3.07s: complete generic episodes must contain 301 samples

.\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_variable_length_dataset.py contracts/v1/tests data_generation/v1/tests/test_dataset.py data_generation/v1/tests/test_multi_object_dataset.py --basetemp temp/pytest-variable-verified
# 47 passed in 2.25s

.\.venv\Scripts\python.exe -m ruff check contracts/v1/validation.py contracts/v1/csv_io.py contracts/v1/tests/test_contracts.py data_generation/v1/dataset.py data_generation/v1/tests/test_variable_length_dataset.py
# All checks passed!
```

## 남은 범위

이 기록은 저장·계약에 한정한다. full-flight 물리 동작, UI 재생, 실제 기체 일반화의 검증 근거가 아니다. evaluation truth reader는 이제 동일 dataset의 유효한 `public/episodes.csv`가 필요하다. CSV 열과 기존 호출 signature는 변경하지 않았다. scoped code reviewer와 전체 통합 검증은 부모 작업에 인계했다.
