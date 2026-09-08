# Visualization v1

상태: **Dash/Plotly local viewer 구현·Browser 검증 완료**. 일반 생성 목록은 고정익·쿼드콥터의 제약 질점 profile과 VTOL·헬기의 bounded-motion profile만 노출합니다. 기존 X8·Crazyflie 6-DOF CLI 결과는 명시 경로로 열어 재생할 수 있지만, 일반 생성 preset으로 다시 표시하지 않습니다.

[상위 지침](../AGENTS.md) → [v1 지침](AGENTS.md) → [생성 설계 및 구현 기록](../../plan/data_generation_v1_design.md)을 읽습니다. viewer는 명시적으로 선택한 v1 dataset을 contract reader로 읽으며, 표시 중 데이터를 재생성하거나 `latest` output을 자동으로 선택하지 않습니다. 별도의 생성 패널은 사용자 요청 때만 generator의 공개 service API로 작업을 제출합니다.

## 제공 기능

- 생성 설정·seed 선택, background 생성, 진행/실패 상태, 중단, 완료된 결과 열기.
- `객체별 지원 상태 · v1`에서 고정익 제약 질점·쿼드콥터 제약 질점·VTOL·일반 헬기의 지원 상태를 확인. 생성 가능 여부는 실제 preset metadata에서 유도하며 이름만 있는 생성 버튼은 만들지 않음.
- 처음 연 dataset과 이번 서버에서 생성한 완료 dataset을 명시적으로 선택. 새 결과로 이동할 때 재생을 멈추고 step 0으로 초기화.
- 하나의 stored integer step을 공유하는 XY/XZ/YZ/3D view.
- 각 Episode는 `X8 고정익 UAV · 진단 시뮬레이션 1`처럼 사람이 읽는 이름으로 표시하고, UUID 기반 `episode_id`는 선택 목록의 `ID:`로 함께 남겨 추적성을 유지.
- 객체 이름은 public `identity_label`을 그대로 사용. `Unknown`이나 X8처럼 생긴 ID를 보고 X8으로 바꾸지 않음.
- dataset에 Episode가 하나면 비교 Dropdown을 비활성화하고 그 이유를 표시한다. 둘 이상이면 최대 네 Episode를 선택해 비교한다.
- XY(수평 이동), XZ(고도 변화), YZ(측면 이동), 3D ENU의 카드 제목을 화면에 직접 표시. XY는 첫 번째 카드이며 데이터나 투영에서 제외하지 않음.
- 최대 네 Episode overlay. Simulation truth는 solid line과 현재 circle, observation은 dashed line과 현재 diamond로 표시한다. legend의 observation jitter는 measurement noise이며 실제 경로가 아님을 함께 밝힌다.
- 2D equal-scale axes와 3D `aspectmode=data` physical scale.
- Play/Pause, 0.25×/0.5×/1×/2×/4×, slider seek. 정적 궤적을 매 tick마다 다시 그리지 않고 current marker만 갱신하며, metadata에 기록된 2..3001개 저장 step 이외의 보간을 만들지 않음. 기존 60초/301개 결과도 같은 reader로 재생.
- Episode 길이가 다르면 선택된 Episode들의 공통 최소 시간까지만 재생하며 화면에 이유와 종료 시간을 표시. Episode 선택이나 dataset 변경 시 step 0에서 Pause하고 slider 범위·시간 눈금·상태를 갱신. 짧은 Episode의 마지막 위치를 반복해 긴 경로로 표시하지 않음.
- evaluation viewer에서만 truth toggle을 표시. `--public-only`는 truth·command·diagnostic을 읽거나 표시하지 않음.
- 선택 상태의 XY/XZ/YZ/3D PNG 네 장을 한 새 UUID run으로 export.

## 실행

```powershell
$env:UV_CACHE_DIR = "$PWD\temp\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\temp\uv-python"

uv run python -m visualization.v1 `
  --dataset-path "$PWD\outputs\data_generation\v1\<dataset_id>"

# public 파일만 쓰는 화면
uv run python -m visualization.v1 `
  --dataset-path "$PWD\outputs\data_generation\v1\<dataset_id>" `
  --public-only
```

기본 host는 `127.0.0.1`, port는 **고정 `8088`**이며 원격 interface를 허용하지 않습니다. 다른 port를 자동 선택하거나 fallback으로 열지 않습니다. 실행 중 Pause와 tick request의 순서를 보존하려고 local server는 single worker로 실행합니다.

## 생성 패널 사용 순서

1. `객체 · 생성 설정`에서 고정익 제약 질점, 쿼드콥터 제약 질점, VTOL, 헬기의 `1개 미리보기` 또는 `100개 생성`을 선택합니다. 같은 객체의 두 설정은 생성량·분할을 구분하며 다른 기체가 아닙니다. 고정익·쿼드콥터의 legacy 6-DOF X8/Crazyflie config는 CLI 비교·재현용으로 보존하되 일반 목록에는 노출하지 않습니다. VTOL·헬기는 내부 장치 재현이 아닌 자료 참고 운동모델이며 생성 설정 설명에 적용 범위를 표시합니다.
2. `Seed`를 입력하고 `데이터 생성`을 누릅니다. seed를 바꾸면 새 초기조건·기동을 만들고, 같은 설정과 seed는 재현됩니다.
3. 생성 중에도 아래 궤적을 재생할 수 있습니다. `생성 중단`은 현재 작업만 중단합니다.
4. 완료 후 `생성 결과 열기`를 누릅니다. 이전 결과는 삭제하지 않습니다. public-only 경계는 새 결과에서도 유지됩니다.

쿼드콥터 제약 질점은 `mocap00`/`mocap01` reference envelope을 provenance로 기록한 60초 운동학 진단입니다. 수 m 범위의 이동에 m 단위 관측오차가 더해져 관측선과 현재 diamond 표식이 크게 튈 수 있습니다. 실선 Simulation truth와 원형 current truth, 점선 observation과 diamond current observation을 비교하세요. observation jitter는 measurement noise이며 실제 경로가 아닙니다. 필요하면 기존 `sigma 1m` 보기로 오차 크기의 영향을 확인하세요. 재생은 0.2초 저장점을 표시하며 평활화하거나 중간 경로를 만들어 숨기지 않습니다. [Stage 1 검증 기록](../../plan/point_mass_stage1_verification.md)을 참조하세요.

`generation_ui.py`는 UI와 명시 dataset registry만 소유하고, 실제 생성·프로세스·상태는 `data_generation/v1/service.py`가 담당합니다. Browser마다 별도 dataset key를 전달하므로 다른 탭의 선택을 전역으로 교체하지 않습니다. 생성 작업은 같은 서버에서 하나만 실행합니다.

`--output-root`는 PNG export 위치에만 적용됩니다. UI 생성물은 프로젝트의 `outputs/data_generation/v1/`에 기록합니다. 최초 실행에는 기존처럼 `--dataset-path`가 필요하며 자동 최신 검색은 하지 않습니다. 서버 재시작 뒤 이전 결과를 열려면 해당 결과 경로를 명시합니다.

2026-09-07 고정익·쿼드콥터 제약 질점의 실제 Browser 생성 → 결과 열기 → XY/XZ/YZ/3D 표시 → Play/Pause를 확인했습니다. 해당 실행의 dataset ID·hash·테스트/Browser 범위와 독립 review 상태는 [Stage 1 검증 기록](../../plan/point_mass_stage1_verification.md)을 참조합니다. VTOL·헬기의 기존 검증은 [최신 검증 기록](../../plan/reduced_motion_v1_implementation.md)에, legacy Crazyflie Browser 기록은 [이전 v1 검증](../../plan/multi_object_v1_implementation.md)에 보존되어 있습니다. 서버를 재시작하거나 새 생성 설정을 추가한 뒤에는 페이지를 새로고침해 선택 목록을 갱신합니다.

## 결과 보존

`Export four-view PNG`는 다음을 새로 생성합니다.

```text
outputs/visualization/v1/<run_id>/
├─ xy_step_<step>.png
├─ xz_step_<step>.png
├─ yz_step_<step>.png
├─ 3d_step_<step>.png
├─ view_state.csv
├─ manifest.csv
├─ files.csv
└─ environment.txt
```

input dataset이나 code folder 옆에 image를 저장하지 않습니다. `090b520c-ecfd-4124-85ee-14d3d8ed82d5`는 실제 Browser에서 export한 run입니다.

`tests/test_figures.py`는 shared current XYZ, marker-only playback update, integer player endpoint, single/four-view PNG output을 검증하고, `tests/test_app.py`는 public-only truth boundary, Episode 선택 상태와 필수 control·그래프 canvas 높이를 검증합니다. `tests/test_cli.py`는 viewer port가 `8088`로 고정되고 다른 port를 거부하는지 검증합니다. `tests/test_generation_ui.py`는 실제 Dash HTTP callback의 생성→조회→결과 열기→재생과 public-only를 검증합니다. 최신 실행 증거는 [생성 UI 기록](../../plan/data_generation_ui_v1.md)을 참조합니다.
# 위치·속도·가속도 검토 패널 (2026-09-08)

## 그래프 초기화·선택 전환 수정

헬기 데이터셋을 열거나 Episode 수를 변경한 뒤 그래프가 비는 문제가 있었다.
빈 Graph에 `extendData`가 먼저 적용되면서 Plotly의 `indices must be valid indices
for gd.data` 오류가 발생했다. 초기 layout에 실제 figure를 포함하고, 선택 변경과
재생을 하나의 figure callback으로 통합했다. dataset/Episode/variant/truth가 바뀌면
전체 figure와 render-context를 함께 갱신하고, 동일 context의 tick만 marker 좌표
`Patch`를 전송한다. 기존 `extendData` callback은 제거했다.

재현 2 tests RED 후 GREEN, 관련 viewer 35 tests 통과 및 변경 파일 Ruff 통과.
실제 헬기 dataset `3c2dfe6a-58fd-45de-bf94-442da24c2555`를 8088에서 열어
XY/XZ/YZ/3D가 표시되는 것을 확인했다. 생성 데이터는 수정/재생성하지 않았다.

재생 조작부와 XY/XZ/YZ/3D 그래프 사이에 `현재 위치 · 속도 · 가속도` 표가 있다.
현재 step의 위치 XYZ(m), 속력(m/s와 km/h), 속도 Vx/Vy/Vz(m/s), 가속도
Ax/Ay/Az 및 크기(m/s²)를 표시한다. 선택한 최대 4 Episode의 표가 같은 시각을 사용한다.
Play/Pause/시간 역탐색/데이터셋 전환에 동기화하며 그래프 전체를 매 tick 재생성하지 않는다.

출처는 `evaluation/truth.csv`의 저장값이다. 가속도는 writer가 저장 속도를 시간 미분한
값이고, 관측오차가 들어간 위치의 미분값이나 IMU specific force가 아니다.
`public-only`에서는 검토값을 읽지 않고 미제공 안내를 표시한다.
truth 궤적 체크박스는 그래프 표시만 제어하며 이 명시적 평가용 표와는 별도다.

생성 설정의 `모든 시나리오 + 학습데이터`는 객체 catalog 전체와 공개 관측 시퀀스 목록을
한 결과로 만든다. 완료 후 `생성 결과 열기`로 재생한다. 실제 모델 학습은 실행하지 않는다.
CLI에서 만든 결과는 서버의 이번 실행 결과 목록에 자동 등록하지 않는다. 명시적인
`--dataset-path`로 열거나 UI에서 생성한다. 포트는 8088을 유지한다.
# 결과 이름과 카메라 조작

- 서버 시작 시 생성 서비스의 `outputs/data_generation/v1`에서 완료된 v1 dataset 목록을 복원합니다. 객체 설명·Episode 수·seed·폴더명으로 구분하며, 시작할 때 지정한 선택은 유지합니다. 분석/실패/생성 중 결과 및 필수 파일이 없는 폴더는 제외합니다. 목록은 metadata 기준이며 전체 내용 검사는 선택 시 수행합니다. 외부 CLI에서 이후 생성한 결과는 서버 재시작 시 반영됩니다.

- 새 생성 폴더는 `객체설명_모드_개수episodes_seed값_고유접미사`로 저장합니다. 기존 UUID 폴더는 변경하거나 삭제하지 않습니다.
- Episode 선택 항목에는 읽을 수 있는 이름만 표시하며, 원래 ID는 tooltip과 선택값에 보존합니다. 드롭다운은 제어 패널 밖으로 펼쳐집니다.
- 3D 드래그·휠 조작 중에는 3D 마커 갱신만 보류하고, 조작이 끝나면 현재 재생 시점으로 따라잡습니다. 재생 시간과 다른 평면·상태표는 계속 진행합니다. 모든 기기에서 일정 FPS를 보장하는 변경은 아닙니다.
