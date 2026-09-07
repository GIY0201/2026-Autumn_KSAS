# VTOL·헬기 v1 간소화 운동모델 통합

2026-09-07. 상태: 구현·실행 검증 완료. 승인: 사용자의 “그렇게 해야지. 굳이 내부 장치는 의미가 없잖아”. 실기체 재현/일반화 검증 완료가 아니라 아래 명시 범위의 소프트웨어 완료를 뜻한다.

## 범위

원문에서 확인한 대표 기체의 운동 범위와 명시적 시뮬레이션 설정으로 새 연속 3D 궤적을 생성한다. 내부 장치를 재현하거나 비행제어기를 구현하는 작업이 아니다. 이전 full-dynamics 요구는 VTOL·헬기에 대해 이 승인으로 대체한다. X8·Crazyflie의 기존 모델과 원본·결과는 변경하지 않는다.

```text
실제 비행/공개 자료의 운동 범위 + 별도로 표시한 시뮬레이션 설정
    → 객체별 모드·명령과 연속 운동 응답
    → 동일한 v1 관측/정답 CSV + 출처·전체 설정
    → 같은 8088에서 생성·선택·XY/XZ/YZ/3D 재생
```

## 공통 제약

- 3D Local ENU, 단일 공중 객체, 60초·5Hz·301점. 바람·지면접촉·센서·예측기·점유영역은 제외.
- 위치·속도는 적분으로 연결한다. 명령 전환 때 위치나 속도를 강제로 재설정하지 않는다. forward cruise와 hover의 차이는 이름이 아니라 명령 범위/전환 규칙에 반영한다.
- 속도, 가속도, 선회율, 수직 이동 제한과 response time은 config에서 읽는다. 각 값의 `source_observed` / `derived` / `simulation_design` 근거를 구분한다. 자료가 없는 응답 계수를 실측 계수라고 표시하지 않는다.
- `EVIDENCE_INFORMED_KINEMATIC`은 자료 참고 시뮬레이션이다. 원본 로그에서 새로 fit한 모델이나 실제 비행 재현 검증 완료로 표시하지 않는다.
- public에는 기존 관측 CSV만, 명령·모드·속도 등은 evaluation에 저장한다. source hash·code hash·seed·config snapshot과 새 UUID 보존을 유지한다.
- production code·config는 `data_generation/v1`, viewer는 `visualization/v1`, 원본은 `data_sources`, 기록은 `plan`, 작업 임시는 `temp/reduced-motion-v1`에 둔다. 새 최상위 폴더·port·Git repo·commit·AGENTS 자동 편집 없음.

## 작업과 완료 조건

- [x] 대표 VTOL·헬기 원문에서 운동 수준 근거 재확인, 원본 보관과 수치/가정 대응표.
- [x] TDD로 설정 기반 bounded-motion 적분·시나리오 구현. 일정 속도/선회 해석값, 급격한 명령 변경의 가속·선회 제한, 수직 이동·정지·재가속, 잘못된 입력, sample/time 계약 검사.
- [x] 객체별 profile/preset 및 명시 dispatch. VTOL의 hover→전환→순항·선회→역전환, 헬기의 정지·수평/측방·상하·선회를 구별. 다양한 seed의 연속 명령 및 서로 다른 궤적.
- [x] 실제 dataset 생성, 같은 seed/새 UUID, public 경계·provenance·실패 전달·기존 X8/quad regression 확인.
- [x] 독립 Python/spec review, scoped Ruff와 full pytest, 같은8088에서 두 객체 생성→열기→네 그래프→Play/Pause 및 pilot 생성 확인.

## 작업 ledger

| 작업 / 연결 | 충돌 점검 | 처리 |
|---|---|---|
| 기존 full-dynamics 계획 ↔ 새 간소화 모델 | 과거 내부 계수 완비 기준과 새 사용자 승인이 다름 | 최신 승인 우선; 과거 기록은 보존하고 이 문서로 구분 |
| motion core ↔ 공통 writer | `MotionEpisode`에 core time/position/velocity 및 evaluation diagnostics/events 전달 | contracts/writer 변경 없이 재사용 |
| 새 profile ↔ loader/dispatch | 기존 loader는 physics-only, dispatch는 quadrotor-only | 명시 engine별 설정 검증과 미지원 조합 거부; quad 호환 유지 |
| preset ↔ 8088 UI | UI 목록은 실제 config에서 파생 | 실제 runnable config 추가, 기존 UI 상태·결과 검증 유지 |
| 자체 작업 일관성 | 속도/가속 한계 검사를 위한 운동 상태와 동일 경계를 테스트 | 수치 조건을 시험으로 확인; 임의 위치 곡선/장치 구현 제외 |

환경: 독립 Git 저장소가 아님을 `git rev-parse --show-toplevel`로 확인. 프로젝트 경계 지침에 따라 새 Git/worktree/.superpowers 디렉터리를 만들지 않고 이 문서 및 task-local brief/report로 추적한다. 자동 Git commit은 실행하지 않는다.

이전 suite 121 pass는 과거 상태다. 이번 수정의 최종 증거는 아래에 별도로 기록한다.

## 근거와 시뮬레이션 설정

| 객체 | 원문에서 확인한 참고 정보 | 신규 생성용 선택값 (`simulation_design`) |
|---|---|---|
| VTOL | Tal–Karaman Table IV의 hover→전진 최대 측정 속도8.3m/s; VI-E의 목표8m/s·3초·접선가속2.7m/s² 사례 | 수평속도≤8m/s, 수직속도≤2m/s, 수평가속≤3m/s², 수직가속≤1.5m/s², 선회율≤45deg/s; 속도응답1초·수직응답0.8초 |
| 헬기 | NASA TM-85890 Table4의1/20/40/60/100/140kt equivalent-airspeed steady trim reference | 수평속도≤40m/s, 수직속도≤4m/s, 수평가속≤2.5m/s², 수직가속≤1m/s², 선회율≤20deg/s; 속도응답2초·수직응답1.2초 |

수평 가속 제한에는 선회 구심 가속을 함께 포함한다. 두 제한을 각각 끝까지 소비해 실제 벡터 가속을 초과하지 않는다. 위 수치는 새로운 시뮬레이션 조건이며 실측 기체 한계·식별 계수로 부르지 않는다. VTOL 순항 판정4m/s도 모델의 단계 구분용 설정이지 실속 속도 추정이 아니다. 회전각은 이동 방향(track heading)으로, 기체 body yaw 또는 자세와 구분한다.

원본 PDF는 [VTOL source](../data_sources/tal_tailsitter_2022/README.md), [헬기 source](../data_sources/nasa_uh60_1984/README.md)에 SHA-256 및 출처·취득일과 함께 보관했다. 세부 명령 범위와 두 가지 기동 순서 선택은 profile snapshot으로 추적한다. 실제 실내 시험 공간을 60초 생성 영역에 강제로 재현하거나 전 기체에 일반화하지 않는다.

## 최종 실행 증거

구현은 `gpt-5.6-terra`의 `max`로 수행했다. 독립 Python review에서 발견한 비정렬 command 경계 문제는 TDD로 수정했다. 0.02초 내부 tick 사이에서 기동이 끝나면 해당 경계에서 적분을 분할하며, 종료 이후 도달을 이전 event 완료로 기록하지 않는다. 기동 종료 시각을 임의로 반올림하거나 위치·속도를 재설정하지 않는다.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-reduced-motion-verified
# 142 passed in 97.47s (0:01:37)
& .\.venv\Scripts\python.exe -m ruff check contracts data_generation visualization
# All checks passed!
```

최종 code hash: `18b0278d03df21cb8490d6d4fabc1165c572a06823b4570a9914ecf171f6907d`.

| 실제 생성 결과 (seed42) | dataset_id | 완료 Episode |
|---|---|---:|
| VTOL Browser 진단 | `510402f2-231b-47d5-82f8-54b98b02fde8` | 1 |
| VTOL CLI 동일 seed | `aa860a7e-de85-43e0-9611-bacd5d9295f4` | 1 |
| 헬기 Browser 진단 | `69ab60b1-ee8f-4b51-a17f-188bf3cea5b6` | 1 |
| 헬기 CLI 동일 seed | `0d5e364a-5f9b-45be-8d36-026c2182a799` | 1 |
| VTOL CLI pilot | `a5fd3a65-25fc-4e49-860e-c4d8767cb247` | 100 |
| 헬기 CLI pilot | `4336286a-3488-4b2f-bd73-f77ed9143f20` | 100 |

모두 `outputs/data_generation/v1/<dataset_id>/`에 보존했다. 두 객체 각각 UI/CLI 진단의 public observations와 truth 파일이 byte 단위로 같고, UUID는 다르다. Pilot마다 서로 다른 궤적100개, train70/validation15/test15, truth30,100행, observation90,300행을 확인했다. 여섯 결과 전부 public allowlist, 현재 code hash, 원본/source SHA-256, snapshot identity, inventory11개 파일의 hash·size를 검사했다. 모든 Episode의 위치·속도 유한성, 시간축, 설정된 속도/유한차분 가속 한계도 통과했다. 부동소수 오차를 위한 비교 허용값은 `1e-6`이다.

- VTOL 진단 observations SHA-256: `dd401d919a83b9e9c0638fc9dfb991d913785d2ffebdbeba8e9553de883886f6`.
- 헬기 진단 observations SHA-256: `fadee3314b63930e3a398fa8b7ca1bc963441684bec1ef68dd1486fe10c84d70`.
- X8 회귀 `bed81601-a6b9-43c8-ae86-1147ced57a5f`와 quad 회귀 `6a69f283-8185-4c33-b898-629a467adbbc`도 새로 실행했다. 각각 기존 seed42 기준 결과의 observations/truth와 byte 단위 일치를 확인했다. 이 회귀 결과는 새 bounded core의 경계 수정 전 code hash이며, 경계 수정은 해당 두 simulator에 영향을 주지 않는다. 최종 fullsuite와 독립 diff review로 기존 분기 보존도 확인했다.
- 초기 VTOL 시험 결과 `5c1951c8-4ac0-4568-a73c-43c8f5cfc254`, `2033593f-42be-4cb1-a8f0-c5c99c3a3972`는 경계 수정 전 결과다. 삭제/덮어쓰기 없이 보존하며 최종 검증 데이터로 취급하지 않는다.

같은8088 Browser에서 네 객체 생성 가능 표시, 두 객체 실제 생성→결과 열기→XY/XZ/YZ/3D 표시, 시간 진행과 Pause 유지, 한국어 Episode 이름을 확인했다. UI 표시/재생은 비행 타당성 증거가 아니다. Pilot100개의 완료 검증은 CLI로 했으며 Browser로100개 생성 완료를 별도 주장하지 않는다. 새 port나 fallback listener를 만들지 않았다.

인계 시 같은8088에서 최종 코드를 다시 시작하고 위 VTOL 진단을 명시적으로 열었다. HTTP200, code hash 재대조, in-app Browser의 네 그래프·Play 시간 진행·Pause 유지 및 Browser 오류 로그 없음(`[]`)을 확인했다. 서버 재시작으로 이전 검증 작업의 메모리 내 결과 목록은 초기화됐지만 모든 dataset 폴더는 보존된다. 두 개의 이번 작업용 pytest base 디렉터리만 제거했으며, Git 저장소가 없는 환경에서 재검토/복구에 필요한 수정 전 사본과 보고서는 `temp/reduced-motion-v1/`에 의도적으로 보존했다.

구현/검토 기록은 [implementation report](../temp/reduced-motion-v1/implementation-report.md), [Python review 및 경계 재검토](../temp/reduced-motion-v1/python-review.md), [통합 review](../temp/reduced-motion-v1/integration-review.md), [artifact audit](../temp/reduced-motion-v1/artifact-audit.md)에 남겼다. 초기 Python review의 HIGH는 재현→수정→재검토로 해결됐으며 두 검토 모두 최종 APPROVE다. 이전 원문 조사 단계의 Grok 인증 실패를 검토 통과로 취급하지 않았고, 이번 검증은 로컬 독립 reviewer와 실제 실행 근거를 사용했다.

## 해석 한계와 후속 범위

완료한 것은 새로운 합성 궤적을 만드는 소프트웨어다. 원본 궤적을 fit한 모델, 기체 장치 재현, 실비행 오차/전체 기동 한계, 실제 객체의 기동 빈도나 예측 성능을 검증한 것은 아니다. 공개 기동/정상비행 자료는 참고이고, 생성 분포·응답·수직/선회 제한은 profile에 명시된 시뮬레이션 설계다. 실제 로그 기반 식별/독립 검증, 관측오차 계약 변경, 다른 객체 추가, 학습/점유영역 구현은 별도 후속 요청으로 다룬다.
