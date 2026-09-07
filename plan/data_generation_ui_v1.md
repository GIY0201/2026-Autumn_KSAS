# 데이터 생성 UI v1 Implementation Plan

> 실행: 현재 작업에서 inline으로 구현하고, 기능 완료 후 독립 code review와 실제 Browser 검증을 수행한다. 파일 배치와 무단 commit 금지는 프로젝트 지침이 우선한다.

**Goal:** 8088 viewer에서 명시한 X8 설정과 seed로 생성 작업을 시작하고, 진행 상태를 확인한 뒤 새 결과를 선택해 재생한다.

**Architecture:** 생성 계산·작업 상태는 `data_generation/v1/service.py`의 공개 인터페이스가 담당한다. 별도 Python process 하나만 실행하고 viewer는 설정 목록·상태만 조회한다. 선택된 dataset은 Browser별 Store로 전달하며 서버의 현재 dataset을 전역 교체하지 않는다. 결과는 기존 CSV 계약과 UUID writer를 그대로 사용한다.

**Tech Stack:** 기존 uv Python, `subprocess.Popen` 별도 worker, Hydra/OmegaConf, Dash/Plotly. 추가 의존성·서버 포트 없음.

날짜: 2026-09-07. 출처: 사용자의 생성 방법/생성 UI 요청. 상태: 구현·최종 실행 검증 완료.

## 범위와 보존할 경계

- 실제 구현된 X8 설정만 노출한다. 목록·설명·seed 기본값은 설정 파일에서 읽는다.
- diagnostic 1개, pilot 100개라는 기존 의미와 60초·5 Hz·3D·무풍·관측오차 계약은 바꾸지 않는다.
- 정수 seed를 검증하고 중복 실행을 차단한다. 계산 중/쓰기 중/완료/실패/취소를 구분한다.
- Browser 새로고침에도 같은 서버의 실행 상태를 조회할 수 있다. 서버 종료 시 소유한 worker만 종료한다. 재시작을 넘는 작업 재개 기능은 없다.
- 완료된 데이터만 열 수 있다. 실패 artifact는 보존하며 완료로 표시하지 않는다.
- 현재 궤적은 생성 시작 시 바꾸지 않는다. 결과 열기 또는 명시적인 dataset 선택 때만 초기 시각으로 변경한다.
- public-only는 새 dataset에서도 evaluation 파일을 읽지 않는다. XY/XZ/YZ/3D와 marker streaming을 유지한다.
- 다른 객체는 자료·운동모델 검토 대상이다. X8 계수나 label만 바꾼 가짜 객체는 추가하지 않는다.

## 실행 순서와 검증

- [x] `data_generation/v1/tests/test_service.py`: 설정 목록, seed 거부, 동시 제출 거부, 진행/실패/취소, 출력 검증을 먼저 테스트한다.
- [x] `data_generation/v1/generate.py`: 선택적인 `progress(completed, total, phase)` hook을 추가하되 기본 CLI 결과를 보존한다.
- [x] `data_generation/v1/service.py`: `GenerationService.submit(preset_id, seed)`, `snapshot()`, `cancel()`, `close()`를 구현한다. 별도 worker는 `generate_from_config`와 출력 검증을 수행한다.
- [x] `configs/*.yaml`: 사람이 읽을 수 있는 UI 설명을 설정에 기록한다.
- [x] `visualization/v1/generation_ui.py`, `tests/test_generation_ui.py`: 생성 설정·seed·시작·취소·상태·결과 열기와 명시 dataset 선택을 추가한다.
- [x] `visualization/v1/app.py`: dataset별 layout과 Store를 연결한다. data callback마다 선택 key로 dataset을 확인한다.
- [x] `assets/styles.css`: 기존 light scientific UI의 명확한 label·focus·반응형 배치를 유지한다.
- [x] focused RED → GREEN, 전체 pytest와 Ruff, 독립 review, 8088 실제 1개 생성 → 결과 열기 → 재생을 검증한다.
- [x] 루트와 담당 README에 실행 방법·서비스 경계·기존 결과 보존을 기록한다. 기존 AGENTS의 README 라우팅을 사용하며, 일반 작업에서 지침을 자동 변경하지 않는 최신 사용자 규칙에 따라 AGENTS는 수정하지 않는다.

후속 객체는 사용자 선택에 따라 VTOL로 정했다. 자료의 실비행/시뮬레이션 구분과 채택 보류 이유는 `plan/vtol_source_review.md`에 기록한다. VTOL 생성기 구현은 이번 UI 완료 범위가 아니다.

테스트 예:

```python
with pytest.raises(ValueError):
    service.submit("diagnostic", -1)
assert service.snapshot() is None
```

```powershell
& .\.venv\Scripts\python.exe -m pytest -q data_generation/v1/tests/test_service.py visualization/v1/tests/test_generation_ui.py --basetemp temp\pytest-generation-ui
& .\.venv\Scripts\python.exe -m pytest -q --basetemp temp\pytest-generation-ui-all
& .\.venv\Scripts\python.exe -m ruff check contracts data_generation visualization
```

## 완료 증거

### 자동 검증

```powershell
& .\.venv\Scripts\python.exe -m pytest -q --basetemp temp\pytest-generation-ui-verified
# 65 passed in 64.51s
& .\.venv\Scripts\python.exe -m ruff check contracts data_generation visualization
# All checks passed!
```

- 새 service/UI 테스트를 실패 상태에서 먼저 실행한 뒤 구현과 보완을 통해 통과시켰다. public-only/evaluation 두 경로의 실제 Dash HTTP callback을 테스트했다.
- 독립 code/Python review에서 Windows status CSV의 read/replace 경합, 완료 receipt의 결과·seed·개수 검증, PNG export root와 생성 root 분리를 보완했다. Windows 실제 open-handle 경합의 bounded retry도 별도로 확인했다.
- 재현을 위해 `pytest` 임시 경로는 작업별로 분리한다. Ruff 범위는 프로젝트 코드 폴더로 한정한다. `ruff check .`는 `temp/uv-cache`·Python 설치본까지 검사하므로 프로젝트 검증 명령으로 사용하지 않는다.

### 8088 Browser 실제 실행

- 최신 코드의 같은 `127.0.0.1:8088`에서 seed 43 생성 작업을 시작·중단했다. 상태 CSV가 `cancelled`로 남았고 기존 결과는 보존됐다.
- 이어 diagnostic / seed 42를 생성하는 동안 기존 궤적 Play가 진행됐다. 완료 후 명시적으로 `생성 결과 열기`를 눌러 새 dataset의 step 0 / Paused로 이동했다.
- 새 dataset에서 Play/Pause, step 143 / 28.6초, XY/XZ/YZ/3D 네 그래프와 현재 위치 marker를 실제 화면으로 확인했다. Browser error/warn 로그는 없었다. 새 두 Dropdown의 accessible name도 확인했다.
- 완료 dataset: `outputs/data_generation/v1/421fbcc5-2086-49d6-a15d-d900494427e3/`. public observation 903행, evaluation truth 301행, manifest `status=complete`, `seed=42`, source 점검은 `NOT_RUN`이다.
- 앞선 같은 seed 42 결과 `d5862f45-cc75-442c-9283-561a43d54efe`와 폴더 ID가 다르며 둘 다 보존됐다. 두 `public/observations.csv`의 SHA-256은 동일하다.

```text
observations.csv SHA-256:
1631d12ef2be1608ecd409a2b4dd41735ed5fd1023c7e4c45a099a3db9ac754d

421fbcc5-2086-49d6-a15d-d900494427e3 manifest code_hash:
434552565c98067253f0ce4b73c53913208dc9ca4983cb9198a57503ffd75f4a
```

100개 pilot의 전체 생성은 이번에 실행하지 않았다. 단일 생성 UI 성공이나 동일 seed 재현을 실측 일반화·VTOL 모델 검증으로 해석하지 않는다. 이번에 추가한 테스트용 임시 폴더와 작업용 backup은 검증 후 정리하며 실제 생성물과 `temp/generation-jobs/`의 요청·상태·로그는 보존한다.
