# Gazebo 참조 운동 데이터 도입·환경 점검

날짜: 2026-09-07  
상태: 참조 데이터 추출 방향 승인, 공개 소스·로컬 환경 확인. 설치·시뮬레이션 실행·수치 추출은 미실행.  
합의 출처: 사용자의 “이걸 사용해서 고정익·쿼드콥터·VTOL 모델의 데이터를 뽑자” 요청 및 [헬기 자료 링크](https://discuss.ardupilot.org/t/helicopters-in-gazebo/136332). 기존 질점 생성 결정은 [전환 명세](point_mass_migration_design.md), 단계 구분은 [1단계 계획](point_mass_stage1_plan.md)을 유지한다.

후속 정정: 사용자가 독립 Python 함수로 수치를 계산하는 방식을 승인했다. 아래 Gazebo/WSL 설치 경로는 이전 검토 기록으로 보존하되 현재 실행 계획으로 사용하지 않는다. 현재 첫 구현은 [독립 X8 수치 추출](x8_characterization.md)이며, PX4/Gazebo/WSL은 설치하지 않는다.

최신 정정: 이후 사용자는 정밀 재현 확대가 아니라 Gazebo 계수·식에 근거한 **최소 질점 제약**을 요청했다. [Gazebo 계수 기반 1차 계산표](gazebo_minimum_constraints.md)에 네 모델의 원본 값과 독립 계산값을 기록했다. 이는 Gazebo 실행 데이터가 아니며 기존 테스트 설정값의 자동 채택·profile 변경도 아니다. 아래 설치·기동 수집 절은 이전 경로의 기록이다.

## 1. 역할과 변경 범위

```text
Gazebo 기체 동역학 + PX4 또는 ArduPilot 제어
  → 기동 명령에 대한 실제 시뮬레이션 위치·속도 기록
  → 속도·가속·선회·전환 반응의 조건별 참조 표
  → 검토·비교 후 기체별 질점 profile 보정
  → 질점 엔진으로 새로운 연구용 Episode 생성
```

- Gazebo 직접 출력은 `reference simulation`이며 실측 자료나 질점 생성 결과로 재명명하지 않는다.
- 참조 수치도 해당 모델·controller·시험 조건의 결과다. 다른 크기의 기체나 같은 종류 전체의 실제 성능 한계로 일반화하지 않는다.
- 현재 진행 중인 질점 1단계의 엔진·60초 계약·기존 결과는 보존한다. 최대 600초 이륙–착륙 시나리오와 상태 검토 패널은 기존 단계 구분을 따른다.
- 카메라·영상 인식·GPS 복원은 연구에 추가하지 않는다. autopilot 시뮬레이션 내부 센서와 연구 데이터의 관측 입력을 구별한다.

## 2. 객체별 첫 참조 후보

| 객체 | 후보 | 제어 연결 | 실제 확인한 범위 |
|---|---|---|---|
| 고정익 | `gz_rc_cessna` | PX4 SITL | 공식 실행 target 존재. 지상 활주·이착륙 및 요구 기동의 정상 완료는 아직 미검증 |
| 쿼드콥터 | `gz_x500` | PX4 SITL | 공식 기본 모델 존재. 카메라·LiDAR 변형을 별도 기체 종류로 늘리지 않음 |
| VTOL | `gz_standard_vtol` | PX4 SITL | 공식 실행 target 존재. hover↔wing 전환 응답 추출은 미실행 |
| 일반 헬기 | T-Rex 450 기반 1.75배 크기 모델 | ArduPilot SITL | rotor head·swashplate·LiftDrag·ArduPilot plugin 소스 존재. 미병합 PR과 physics 패치 필요 |

고정익의 `gz_advanced_plane`은 다른 lift physics를 사용하는 별도 후보다. Standard VTOL과 tailsitter·tiltrotor도 동일 모델로 합치지 않는다. PX4의 airframe 설정과 Gazebo 동역학 모델은 구분한다. 위 후보 선택이 기존 X8/Crazyflie 데이터·profile의 자동 교체를 뜻하지 않는다. [PX4 공식 Gazebo Vehicles](https://docs.px4.io/main/en/sim_gazebo_gz/vehicles)

## 3. 헬기 확인 결과

사용자 링크의 원문 직접 요청은 응답 제한으로 열리지 않아, 관련 개발 PR·연결된 physics PR·GitHub API에서 핵심 내용을 교차 확인했다. 포럼 본문을 직접 읽은 것으로 보고하지 않는다.

- [ArduPilot/SITL_Models PR #149](https://github.com/ArduPilot/SITL_Models/pull/149)는 단순 외형 파일이 아니라 로터 기구와 공력·제어 연결을 포함한다. 개발자가 Gazebo Harmonic/Ionic에서 시험했다고 기록했지만, 이 프로젝트에서 재현한 결과는 아니다.
- 모델은 소형 RC 헬기 기반이다. 유인 대형 헬기의 성능 자료로 해석하지 않는다.
- PR은 기본 배포판에 없는 physics 기능이 없으면 모델이 로드되더라도 정상 작동하지 않을 수 있다고 설명한다. 외형 표시와 안정적인 운동 생성은 별도 검증이다.
- 아래 상태는 2026-09-07 GitHub API의 `state`, `merged`, `head.sha`, `base.ref` 확인 결과다. 향후 설치 때 다시 확인한다.

| 항목 | 확인 상태 | head commit | 대상 branch |
|---|---|---|---|
| [SITL_Models #149](https://github.com/ArduPilot/SITL_Models/pull/149) | open, 미병합 | `58b9e688555fe2e6fe43571269a49ddb851f82f5` | `master` |
| [gz-physics #753](https://github.com/gazebosim/gz-physics/pull/753) | merged | `b9e7b963f5073e7c7d96fa025a94febc78484e85` | `gz-physics8` |
| [gz-physics #754](https://github.com/gazebosim/gz-physics/pull/754) | open, 미병합 | `813779a5743a95eaf1b9f02e4c9cc49ced8f3093` | `gz-physics8` |
| [gz-sim #2960](https://github.com/gazebosim/gz-sim/pull/2960) | open, 미병합 | `54d0ebfc8ece246ec29e624bce11b901b2151e01` | `gz-sim9` |

`gz-physics #753`의 merge commit은 `f1643e4a2349f1e6ae1a61c2e38d53cef2a4f30a`다. 미병합 PR의 임시 `merge_commit_sha`를 통합 완료 근거로 사용하지 않는다. PR의 대상 major version과 실제 사용할 Harmonic/Ionic 라이브러리의 호환성을 확인한 뒤 적용해야 한다. 기존 PX4용 정상 환경의 system library를 헬기 패치로 무단 교체하지 않는다.

외부 CAD 이용 조건은 별도 확인 대상이다. PR의 permission 요청 문구를 재배포 허가 완료로 해석하거나 외부 모델을 프로젝트 MIT LICENSE로 GitHub에 올리지 않는다.

## 4. 로컬 환경 점검과 실행 전 조건

확인 명령: Windows `Get-Command wsl,docker,gz,gazebo,git,uv`, `wsl --list --verbose`; 기존 Ubuntu에서 `/etc/os-release`, `command -v`, 제한된 표준 PX4 경로 점검.

- 기존 WSL 배포판은 `Ubuntu`, WSL 2이며 Ubuntu 26.04 LTS다. 확인을 위해 해당 배포판에서 읽기 전용 명령만 실행했다.
- Windows PATH에는 `gz`, `gazebo`, `docker`가 없었다. 기존 Ubuntu PATH에서도 `gz`, `gazebo`, `docker`, `cmake`를 찾지 못했다. 이 결과를 모든 디스크의 미설치 증명으로 확대하지 않는다.
- Gazebo Harmonic의 공식 Ubuntu binary 대상은 22.04와 24.04다. 26.04에 다른 배포판의 apt 저장소를 강제로 넣지 않는다. [Gazebo 공식 설치 문서](https://gazebosim.org/docs/harmonic/install_ubuntu/)
- 제안하는 설치 경계는 **기존 Ubuntu 26.04를 보존하고 데이터 추출용 Ubuntu 24.04 WSL 환경을 별도로 준비**하는 것이다. 신규 배포판·패키지 설치는 사용자 확인 후 진행한다. Windows 프로젝트 `.venv`는 변경하지 않는다.
- 현재 8088 viewer는 유지한다. autopilot/Gazebo 내부 통신은 viewer port와 별개이므로, 실제 실행 전에 필요한 로컬 port·바인딩·소유 process·종료 방법을 명시한다. 임의 port fallback이나 외부 노출, 다른 작업의 process 종료는 금지한다.

## 5. 추출 데이터와 확인 기준

1. 설치가 승인되면 PX4 버전과 모델 submodule commit, Gazebo/physics 버전, world/model/controller 설정·hash를 고정한다. `main`/`latest`를 자동 추종하지 않는다.
2. 첫 실행은 X500의 이륙–정상 hover–이동–착륙으로 상태 기록 경로를 확인한다. 이후 고정익과 Standard VTOL을 연결한다. 헬기는 패치 재현과 안정 hover 검사를 별도 수행한다.
3. 각 기동은 안정 비행 → 연속적인 진입 → 유지 → 회복 → 안정 비행으로 기록한다. 시험 전마다 초기 상태·환경을 명시하고, 기동 중 위치를 이상 경로로 덮어쓰지 않는다.
4. simulator time과 물리 상태의 위치·속도, 명령, 비행 phase를 저장한다. 시뮬레이터 추정기 출력과 physics truth를 구분하고, 선택한 기록 지점·좌표축·원점을 명시한다.
5. 무풍을 명시하고 ENU/m/s로 변환한다. 출발 고도 0m는 정의된 기준점의 초기 높이에 상대화하며, 기체 중심과 지면 접촉 높이를 혼동하지 않는다.
6. 고주기 원자료를 보존하고 가속도 계산 방법·필터·sampling 간격을 기록한다. IMU specific force를 관성계 가속도로 그대로 사용하지 않는다. 5 Hz 관측본과 원본 적분·기록 주기는 별개다.
7. 속도·경로각·선회 반경/율·접선/횡/수직 가속·전환 시간의 조건별 표와 실패 기록을 만든다. 요구 명령과 달성 상태, 진입·유지·회복 구간을 분리한다. 서로 다른 시험의 극값을 합쳐 가능한 기동으로 만들지 않는다.
8. 질점 보정 조건과 비교 조건을 분리하고, 미지원 운동은 지원한다고 표시하지 않는다. 외부 실비행 검증을 하지 않았다면 결과 상태는 simulator reference에 한정한다.

수치 허용오차는 실제 기록 주기·수치 수렴·참조 반복성 확인 후 versioned config에 근거와 함께 둔다. 이 문서에서 임의의 성능 수치나 합격선을 새로 발행하지 않는다.

## 6. 파일 배치와 현재 완료 범위

- 외부 고정 버전의 원본은 기존 `data_sources/<source_id>/` 규칙을 따른다. 실제 확보하지 않은 source 폴더를 미리 만들지 않는다.
- 향후 추출·정규화 코드/설정/테스트는 `data_generation/v1/`에 둔다. Gazebo 수집기의 상세 파일·인터페이스는 환경과 실제 기록 API 확인 후 별도 구현 계획으로 확정한다.
- 추출 결과는 새 `outputs/data_generation/v1/<dataset_id>/`에 `reference simulation`으로 구분한다. 현재 60초 public dataset의 validator를 우회하거나 참조 시험을 자동 학습 데이터로 등록하지 않는다.
- 표·원본 로그·설정·환경·코드/source hash·seed를 추적한다. truth/명령/내부 상태는 공개 observation reader로 노출하지 않는다. 기존 결과·source 파일은 덮어쓰지 않는다.
- 이번에 완료한 것은 모델 존재·개발 상태·환경 점검과 승인 범위 기록이다. 설치, simulator build, 기동 실행, CSV 수집, viewer 연결, 질점 profile 보정은 아직 완료되지 않았다.
