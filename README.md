# 2026 KSAS

> 현재 데이터 생성 방식은 [데이터 생성 안내](plan/data_generation_current.md)를 먼저 확인하세요. 관측오차 3종·식별 패턴 3종·train/valid/test 저장과 아직 남은 모델 학습 작업을 구분했습니다. 아래의 60초·이전 모델·실행 결과 서술은 해당 단계의 기록입니다.

동적 공중 객체의 미래 점유 영역 예측 연구를 위한 독립 작업 공간입니다.

## 전체 이착륙 시나리오 — 2026-09-08 추가

기존 60초 경로와 별도로 `full_flight_fixed_wing`, `full_flight_helicopter`, `full_flight_vtol` 생성 설정을 추가했습니다. 최대 600초·5 Hz의 질점 궤적이며 F64/H96/V64 조합을 명시적으로 선택할 수 있습니다. 아래의 기존 60초 설명은 legacy 경로에 해당합니다. 수치 설정은 생성용 가정이며 Gazebo 동역학을 실행하거나 실기체 운용 한계를 검증한 모델은 아닙니다.

```powershell
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name full_flight_fixed_wing scenario_id=F05-D seed=17
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name full_flight_helicopter scenario_id=H10-F seed=17
.\.venv\Scripts\python.exe -m data_generation.v1 --config-name full_flight_vtol scenario_id=V05-D seed=17
```

UI의 `전체 이착륙 · 1개 미리보기`는 각 객체의 기본 조합을 실행합니다. 다른 조합은 위 CLI의 `scenario_id`로 선택합니다. 현재 실행 중인 viewer에는 재시작 후 새 코드가 적용됩니다. [실행 기록 및 제약](plan/scenario_generator_integration.md)을 참고하세요.

현재 연구용 v1 생성 선택지는 **고정익·쿼드콥터의 제약 질점 모델**과 기존 **VTOL·일반 헬기의 bounded-motion 모델**입니다. 기존 X8·Crazyflie 6-DOF CLI와 결과는 비교·재현 경로로 보존하지만, 일반 Browser 생성 목록에는 노출하지 않습니다. 제약 질점 경로는 Local ENU 60초 운동학 생성기이며, 독립 실비행 검증이나 6-DOF 식별 완료를 뜻하지 않습니다. 예측 모델 학습·추론·점유영역 계산은 아직 이 작업 범위에 포함하지 않았습니다.

사용자 지시에 따라 **VTOL·쿼드콥터·일반 헬기도 별도 v2가 아니라 기존 v1에 통합했습니다.** VTOL·헬기는 내부 장치 대신 속도·가속·선회·상승/하강의 연속 운동을 모델링합니다. 공개 자료의 참고 정보와 생성용 시뮬레이션 설정은 구별하며, 공통 저장·관측·8088 UI를 재사용합니다. 최신 범위와 검증 결과는 [간소화 운동모델 통합 기록](plan/reduced_motion_v1_implementation.md), 이전 쿼드콥터 검증은 [기존 v1 통합 기록](plan/multi_object_v1_implementation.md)을 확인하세요.

## 현재 구현 범위

| 영역 | 현재 상태 | 책임 |
|---|---|---|
| `data_generation/v1/` | 4개 연구용 생성 경로 | 고정익·쿼드콥터 제약 질점, VTOL·헬기 bounded-motion, 60초 Episode, 관측오차 합성, 출처·설정 추적, background 생성 service; X8 source motion check는 legacy CLI 전용 |
| `contracts/v1/` | 구현·검증 완료 | public/evaluation CSV 경계, strict schema validation, reader |
| `visualization/v1/` | 구현·Browser 검증 완료 | 생성 설정·seed·진행·중단·결과 선택, Dash/Plotly XY/XZ/YZ/3D 재생·Pause·PNG 네 장 export |
| `models/gru/v1/` | 미구현 | 별도 모델 설계와 구현 범위 |

새로운 합성 Episode는 Local ENU(`x=East`, `y=North`, `z=Up`)의 단일 객체이며, 무풍, 60초, 5 Hz, 시작점 포함 301개 시점입니다. GPS/지도/위경도, camera·영상 처리·거리 추정·sensor fusion, 예측 모델·점유영역은 이 v1 구현에서 제외합니다.

## 빠른 실행

새로 clone한 경우에는 아래의 **Git 버전 관리와 외부 자료** 절을 먼저 확인하세요. 원본 자료와 기존 생성 결과는 GitHub에 포함하지 않습니다.

프로젝트 루트의 PowerShell에서 실행합니다. 이 관리형 Windows 환경에서는 `uv` cache/runtime을 프로젝트 `temp/`로 지정해야 합니다.

```powershell
$env:UV_CACHE_DIR = "$PWD\temp\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\temp\uv-python"
uv sync --locked

# 고정익 제약 질점 진단 dataset 1개 생성
uv run python -m data_generation.v1 --config-name diagnostic_fixed_wing_point_mass

# 쿼드콥터 제약 질점 진단 dataset 1개 생성
# uv run python -m data_generation.v1 --config-name diagnostic_quadrotor_point_mass

# X8 원본 자료를 별도로 준비한 경우에만 source motion check 실행
# uv run python -m data_generation.v1 --config-name diagnostic_with_motion_check

# 명시적으로 선택한 dataset만 local viewer로 열기
uv run python -m visualization.v1 `
  --dataset-path "$PWD\outputs\data_generation\v1\<dataset_id>"
```

viewer는 기본적으로 `http://127.0.0.1:8088`에서만 열립니다. port는 `8088`로 고정하며 다른 port를 자동 선택하지 않습니다. `--public-only`를 추가하면 evaluation truth와 diagnostic을 읽지 않습니다. `latest`를 추론하거나 자동 선택하지 않으므로 항상 `<dataset_id>`를 명시합니다.

실행한 viewer 상단에서 **생성 설정 선택 → seed 입력 → 데이터 생성 → 생성 결과 열기**를 할 수 있습니다. 일반 목록은 `고정익 UAV · 제약 질점 모델`, `쿼드콥터 · 제약 질점 모델`, `VTOL · 간소화 운동모델`, `헬기 · 간소화 운동모델`의 `1개 미리보기`와 `100개 생성`을 실제 YAML metadata에서 읽습니다. 기존 X8·Crazyflie 6-DOF config는 CLI 비교·재현용으로 남아 있지만 일반 목록에는 없습니다. 자세한 제약과 실행 증거는 [Stage 1 검증 기록](plan/point_mass_stage1_verification.md), [생성 README](data_generation/v1/README.md), [viewer README](visualization/v1/README.md)를 참조합니다.

## 생성 결과와 추적

최신 VTOL 진단 dataset은 다음에 보존되어 있습니다. 다른 객체와 100개 pilot의 ID는 [최종 실행 기록](plan/reduced_motion_v1_implementation.md)에 있습니다.

```text
outputs/data_generation/v1/510402f2-231b-47d5-82f8-54b98b02fde8/
```

이 결과는 public contract 검증을 통과했으며, `public/`에는 `episodes.csv`, `observations.csv`만 있고 truth·command·diagnostic은 `evaluation/`에 분리되어 있습니다. `manifest.csv`, `files.csv`, `effective_config.yaml`, `environment.txt`가 실행 시점의 입력·hash·seed·환경을 기록합니다.

Browser에서 실제로 내보낸 네 장 PNG 실행 결과는 다음에 보존되어 있습니다.

```text
outputs/visualization/v1/090b520c-ecfd-4124-85ee-14d3d8ed82d5/
```

각 실행은 새 UUID 폴더를 만들며, 코드 폴더나 입력 dataset 옆을 덮어쓰지 않습니다. 전체 저장 규칙은 [outputs/README.md](outputs/README.md)를 따릅니다.

## 검증 상태와 해석 경계

- 2026-09-07 VTOL·헬기 통합 후 `pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-reduced-motion-verified`: **142 passed in 97.47s**, scoped Ruff PASS, 독립 Python/통합 review APPROVE. VTOL·헬기 Browser 생성·재생, 각각 pilot100개, 동일 seed의 UI/CLI CSV 재현, source/inventory hash와 수치 한계를 확인했습니다. [최신 검증 기록](plan/reduced_motion_v1_implementation.md)을 참조합니다.
- 생성 UI 추가 전 기록: `.\.venv\Scripts\python.exe -m pytest -q --basetemp temp\pytest-final-8088` → **35 passed in 63.25s**. UI 추가 후 최신 실행은 [생성 UI 기록](plan/data_generation_ui_v1.md)을 참조합니다.
- `uv run ruff check contracts data_generation visualization`: **All checks passed**.
- 실제 Browser에서 동일 step의 XY/XZ/YZ/3D 재생, Play/Pause, 네 PNG export와 output manifest를 확인했습니다.
- 공개 X8 TRAIN 13개에 대해 body velocity, angular rate, Euler angle, indicated speed, specific force를 재적분해 비교한 결과는 `REPORT_ONLY`입니다. 합격선·field validity·다른 기체로의 일반화를 주장하지 않으며, VALIDATION은 tuning에 사용하지 않았습니다.

사용자 제공 `Holybro Pixhawk.zip`은 프로젝트 루트에 그대로 보존되어 있으며, X8 원본으로 취급하지 않았습니다.

## 문서와 지침

루트 `AGENTS.md` → 담당 폴더 `AGENTS.md` → 해당 버전 `AGENTS.md`·`README.md` 순으로 읽습니다.

- [X8 데이터 생성·평면/3D 재생 설계와 구현 기록](plan/data_generation_v1_design.md)
- [X8-GEN-V1 구현 checklist](plan/x8_v1_implementation_checklist.md)
- [생성 UI 구현·검증 기록](plan/data_generation_ui_v1.md)
- [후속 VTOL의 자료·운동모델 검토](plan/vtol_source_review.md)
- [VTOL·쿼드콥터·헬기 v1 통합 범위](plan/multi_object_generation_scope.md)
- [세 객체 v1 구현·검증 기록](plan/multi_object_v1_implementation.md)
- [VTOL·헬기 간소화 운동모델 통합](plan/reduced_motion_v1_implementation.md)
- [제약 질점 Stage 1 전환 명세](plan/point_mass_migration_design.md)
- [제약 질점 Stage 1 실행·검증 기록](plan/point_mass_stage1_verification.md)
- [독립 X8 참조 운동 수치 추출](plan/x8_characterization.md) — `REFERENCE_SIMULATION` 후속 작업이며, 질점 profile을 자동 변경하지 않습니다.
- [Gazebo 계수 기반 최소 운동 제약](plan/gazebo_minimum_constraints.md) — 원본 계수와 조건부 계산값을 기존 테스트 설정에서 분리한 1차 기록이며, 생성용 상한은 아직 미적용입니다.
- [객체별 공개 근거와 미확정 항목](plan/multi_object_source_audit.md)
- [기능별·버전별 파일 배치](plan/project_structure.md)
- [모델 설계 인계](plan/model_design_handoff.md)

기존 GRU 연구 기준은 [동적_공중_객체_미래_점유영역_설계.md](동적_공중_객체_미래_점유영역_설계.md)에 보존되어 있으며, 이번 생성·viewer 구현이 그것의 모델 구조를 변경하지 않습니다.

## Git 버전 관리와 외부 자료

2026-09-07 사용자 요청으로 [GIY0201/2026-Autumn_KSAS](https://github.com/GIY0201/2026-Autumn_KSAS)를 이 프로젝트의 GitHub 원격 저장소로 사용합니다. 처음 제공된 `GIY0201/KSAS-` 주소는 이 주소로 연결되며, GitHub repository ID `1360108702`가 동일함을 확인했습니다. 원격 이름은 `origin`, 기본 브랜치는 기존 저장소의 `main`입니다. 로컬 작업 폴더는 `2026_KSAS`를 그대로 유지하며, 다른 로컬 프로젝트의 코드를 통합한 것이 아닙니다. 기존 초기 커밋과 MIT `LICENSE`를 보존합니다. **공개 저장소이므로 비밀정보나 원본 비행 자료를 커밋하지 않습니다.**

- 관리 대상: 프로젝트 코드, 버전별 설정과 테스트, `pyproject.toml`, `uv.lock`, 설계 문서·도식, 폴더별 지침, 이 프로젝트에서 작성한 출처 기록.
- 제외 대상: `.venv/`, cache·log·개인 tooling 설정, `Holybro Pixhawk.zip` 등 원본 압축파일, 외부 CSV·NPY·PDF·notebook·스크립트, `outputs/`의 실행 결과, `temp/`의 임시 파일, credential 파일. `outputs/`와 `temp/`의 안내 문서는 관리합니다.
- 제외는 `.gitignore`로 처리하며 로컬 파일을 삭제하거나 이동하지 않습니다. 특히 원본 자료와 실행 결과의 백업은 Git과 별도로 필요합니다.
- 이후 기능 작업은 `feature/<작업명>`, 수정은 `bugfix/<작업명>` 등 작업별 브랜치에서 진행합니다. 커밋은 `feat:`, `fix:`, `docs:`, `chore:` 등 Conventional Commits 형식을 사용합니다. 변경 파일을 명시적으로 stage하고 `git diff --cached`로 확인합니다.
- 원격 이력을 강제로 덮어쓰는 push는 하지 않습니다. Git commit 이력은 코드 변경 추적이고, `v1` 같은 기능 버전과 `dataset_id`/`run_id`에 따른 결과 추적은 기존 규칙대로 별도로 유지합니다.
- 이번 등록은 현재 **60초 v1 구현의 기준선**입니다. 최대 10분의 고정익·VTOL·헬기 이륙–착륙 시나리오는 설계 논의 중이며 아직 구현된 기능으로 표시하지 않습니다.

새 작업 환경에서는 다음처럼 내려받습니다. 기존 프로젝트 폴더 안에 중복 clone하지 않습니다.

```powershell
git clone https://github.com/GIY0201/2026-Autumn_KSAS.git 2026_KSAS
cd 2026_KSAS
uv sync --locked
```

원본이 필요 없는 X8 `diagnostic` 생성과 달리, X8 source motion check 및 쿼드콥터·VTOL·헬기 profile은 별도 원본이 필요합니다. 각 `data_sources/<source_id>/README.md` 또는 `SOURCE.md`와 `data_generation/v1/configs/profiles/`, `configs/motion_reference/`에 기록된 출처·고정 버전·경로·hash를 따라 자료를 준비해야 합니다. 자료별 원래 이용 조건을 따르며 프로젝트 MIT LICENSE를 외부 자료에 적용하지 않습니다. 원본 부재나 hash 불일치 검사를 우회하지 않습니다.

전체 테스트에도 원본이 필요한 항목이 있습니다. 이 컴퓨터에 원본이 있는 상태의 테스트 통과를, 원본 없는 새 clone에서의 전체 테스트 통과로 해석하지 않습니다.
## 전체 시나리오 + 학습 데이터 / 속도 검토

8088 UI의 `객체 · 생성 설정`에서 `고정익 / 헬기 / VTOL · 모든 시나리오 + 학습데이터`를
선택하면 catalog 전체를 생성하고 같은 dataset에 공개 관측 시퀀스 목록을 저장한다.
재생 화면에는 현재 위치·속력·축별 속도·가속도 표가 추가되어 있다.
상세 실행법과 입력 경계는 [생성 README](data_generation/v1/README.md),
[시각화 README](visualization/v1/README.md), [작업 기록](plan/telemetry_training_generation.md)을 따른다.
이 기능은 학습 데이터 준비이며 예측 모델을 학습시키는 기능이 아니다.
