# 세 객체 v1 통합 — 구현 계획과 실행 기록

날짜: 2026-09-07. 이 문서의 실행 기록은 **쿼드콥터 통합·검증 완료 및 VTOL·헬기 full-dynamics 보류 시점**이다. 이후 사용자가 내부 장치 생략을 승인하여 [간소화 운동모델 통합](reduced_motion_v1_implementation.md)을 진행한다. 아래 과거 증거와 보류 이유를 소급해 완료로 바꾸지 않는다.

## 목표와 구조

X8을 보존하면서 VTOL·쿼드콥터·일반 헬기의 근거 있는 운동모델을 객체별로 추가한다. 같은 8088 화면에서 실제 지원하는 설정을 선택해 새 Episode를 생성·재생한다. 근거 없는 모델이나 이름만 있는 선택지를 생성 완료로 표시하지 않는다.

기준: [범위 및 근거](multi_object_generation_scope.md), [결정 대조표](decision_reconciliation.md), 기존 X8 설계. 기술: Python 3.12, NumPy/SciPy, Hydra/OmegaConf, 기존 Dash/Plotly UI. 새 dependency나 서버는 기본적으로 필요하지 않다.

공통 제약:

- 모든 코드·설정·테스트는 기존 기능별 v1 안에 둔다. X8 식·계수·원본 VALIDATION·기존 출력은 변경하지 않는다.
- 60초·5 Hz·301점·Local ENU·독립 ambient wind 없음·단일 객체. public은 관측/시간/오차, truth와 controller/state는 evaluation 전용이다.
- 매 실행은 새 dataset_id. source/model/object 식별자, 원본 hash, 사용한 전체 설정을 기록한다. UI 객체 목록은 지원 config에서 얻는다.
- 힘/모멘트로 상태를 적분한다. 위치를 지정 경로에 강제로 대입하지 않는다. controller/기동 실험 설정과 식별된 물리 계수의 근거를 구분한다.
- 확인되지 않은 load-bearing 계수가 있으면 해당 모델을 완료로 승격하지 않는다. 부분 구현과 과학적 재현의 한계를 명시한다.
- Git 저장소 생성·commit·다른 project 변경·AGENTS 자동 변경은 하지 않는다. 작업 brief/backup은 `temp/multi-object-v1`에 둔다.

## 작업 1 — 공통 결과·출처 저장 (TDD)

파일: `data_generation/v1/records.py`(신규), `dataset.py`, `tests/test_multi_object_dataset.py`(신규).

1. 먼저 새 객체 record 저장 시 X8 출처/diagnostic이 섞이지 않는 테스트와 유한 값·shape·중복 ID 거부 테스트를 작성하고 RED를 확인한다.
2. `MotionEpisode`는 episode_id/status/failure_reason/seed/steps/t_s/position_enu_m/velocity_enu_mps와 evaluation 전용 named diagnostic arrays, event records를 갖는다. X8의 기존 `GeneratedEpisode`는 보존한다.
3. `GenerationRequest`에 object_id/model_id/source_id/input_id와 resolved model configuration·source file hash 기록을 추가한다. 레거시 호출의 X8 defaults만 유지하고 새 객체는 명시적으로 공급한다.
4. writer의 공통 관측/정답/split 알고리즘은 그대로 사용한다. X8 diagnostic 열은 유지하고 새 객체는 자신의 실제 diagnostic만 기록한다. 조작·추력 값 등을 X8 elevon 열에 넣지 않는다.
5. GREEN 후 기존 dataset/contract 테스트도 실행한다.

## 작업 2 — 객체별 물리·기동 (근거 확보 후 TDD)

파일: `data_generation/v1/<object>*.py`, `configs/profiles/<model>.yaml`, `tests/test_<object>.py`, `data_sources/<source_id>/README.md` 및 실제 확보 원본.

- 쿼드콥터 후보: Eschmann et al. 2024 Crazyflie 2.1. 원문과 고정 commit notebook의 동일 기체 계수 사용. Izz의 추론 성격과 저속 모델의 공력 생략을 표시한다.
- VTOL/헬기: 별도 원문 감사에서 식·전체 계수·적용 범위를 확인한 뒤 대표 기체를 채택한다. tiltwing/Lift+Cruise, hover-only/forward-flight를 혼합하지 않는다.
- 각 모델은 입력 검증/actuator 제한, 힘·모멘트 부호, trim/hover, motor 또는 rotor 반응, quaternion 정규화, dt 수렴, 여러 seed의 연속 궤적을 검사한다.
- 명령은 config에 두고 출처의 물리 한계와 프로젝트의 진단 실험 설정을 구별한다. 전환 때 state를 reset하지 않는다. 실패는 실패로 기록한다.

## 작업 3 — 실행과 UI 연결 (TDD)

파일: `generate.py`, 실제 지원 preset YAML, `service.py`(필요한 검증만), `visualization/v1/generation_ui.py`, `app.py`, 해당 tests.

1. 미지원 model_id와 잘못된 source/profile 조합 거부, 새 모델의 실제 generate_from_config 출력 검사.
2. 명시 config dispatch → 모델 → 공통 writer를 연결한다. motion check는 그 source가 지원할 때만 허용한다.
3. UI는 실제 preset metadata로 객체·설정을 표시한다. 준비되지 않은 모델은 생성 가능한 것처럼 노출하지 않는다.
4. 기존 X8 선택·동작과 dataset 선택/재생, 진행·취소·실패 검증을 유지한다.

## 작업 4 — 전달 검증

- 수정 범위 Ruff 및 전체 pytest. 새 구현의 RED/GREEN command/result를 기록한다.
- 객체별 실제 diagnostic 생성, 동일 seed 재현·다른 UUID, public contract·provenance 검사.
- 알려진 기존 8088 server만 재시작하고 실제 browser에서 생성→열기→네 그래프→Play/Pause를 확인한다. 다른 port fallback 금지.
- 독립 task review 및 final Python/code review. 실제 읽은 source·차이·실행 증거로만 완료 판정한다.

## 실행 ledger

| 작업 | 상태 | 증거/남은 일 |
|---|---|---|
| v1 통합 결정 반영 | 완료 | 이전 v2 제안을 최신 사용자 지시로 대체 |
| 공통 writer | 구현·독립 review 통과 | focused 22 passed; mutable provenance와 event 시간 범위 회귀 포함 |
| 쿼드콥터 근거 | 공개 모델 재현용 채택 | 공식 commit `2d267dd07b4262f579ee223d20b26a6dc9d17147`; 원문/notebook/LICENSE 원본 hash 검증. 독립 실비행 검증 아님 |
| VTOL/헬기 근거 | 일부 필수 항목 미확정 | [원문 감사 결과](multi_object_source_audit.md); 누락값을 임의로 채우지 않음 |
| 쿼드콥터 물리/CLI | 구현·실행·독립 review 통과 | 아래 최종 suite·실제 생성·hash 기록; 물리/수치 실패 검토 PASS |
| 객체 UI | Browser 실행 검증 | X8/쿼드콥터 각각 진단·pilot; 실제 생성·열기·네 그래프·Play/Pause·중단 확인. VTOL/헬기는 미지원 사유만 표시 |
| 구현된 범위 최종 검증 | PASS | 121 passed, scoped Ruff PASS, 독립 writer/physics/integration review PASS. 세 객체 전체 완료를 뜻하지 않음 |

중간 실행 증거(최종 완료 아님):

- 공통 변경 후 `contracts/v1/tests data_generation/v1/tests visualization/v1/tests`: **95 passed, 99.78 s**.
- profile 원본 hash/경로 검증: RED collection error → **10 passed**.
- 잘못된 seed/count/profile 사전 거부: **4 failed → 4 passed**.
- Unknown identity를 X8로 바꾸지 않기: **3 failed, 1 passed → 4 passed**.
- UI 결과의 object/model/source/input 일치 검사: **4 failed → service 26 passed**.
- 실제 preset에서 객체별 준비 상태를 유도하고 UI에 미지원 사유 표시: **2 failed → 관련 36 passed**.
- 이 수치 이후에도 물리 구현과 추가 수정이 이어지므로 최종 suite와 Browser 검증은 별도로 갱신한다.

추가 중간 증거:

- 쿼드콥터 core 원문 Eq.(1)–(6), notebook 계수·로터 방향과 코드의 독립 대조: PASS.
- 전체 suite **116 passed, 156.86 s**, scoped Ruff PASS. 이후 no-profile X8 label과 실행 중 code-hash 변경 검사를 보완 중이므로 최종 증거로 대체 예정.
- 기존 사용자의 X8 pilot `59fb488b-096a-42e3-b637-b90cbb3b0182`는 서버를 중단하지 않고 **100/100 완료**를 확인했다. 원본을 수정하거나 삭제하지 않는다. 이 실행은 구 writer를 사용했고 코드 편집 기간과 겹쳤다. 구 writer는 종료 시점 파일 hash를 기록하므로 시작 시점 소스 일치가 확정된 새 검증 결과로 사용하지 않는다. 궤적이 잘못됐다는 판정과는 구분한다.

## 최종 실행 증거 — 2026-09-07

코드 고정 후 다음 명령을 실제 실행했다. 이후 변경은 README와 본 기록뿐이며 생성 코드·YAML은 변경하지 않았다.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-multi-object-v1-verified
# exit 0: 121 passed in 158.37s
& .\.venv\Scripts\python.exe -m ruff check contracts data_generation visualization
# exit 0: All checks passed!
```

- no-profile 요청을 다른 객체 이름으로 위장하는 회귀와 실행 시작/저장 시 code hash 변경 거부: RED 4 → GREEN 4. 시작 hash를 검증한 뒤 새 폴더를 할당하고 그 값을 manifest에 기록한다.
- 독립 review: common writer PASS, quadrotor 원문/수치/overflow PASS, 최종 dispatch/hash/실패 전달 narrow review PASS. 마지막 review는 테스트를 별도로 재실행하지 않았으며 위 full suite는 main에서 실행했다.
- 신규 실행의 공통 code SHA-256: `d93ed2faa4247f4fae71567653ed3a9cff05abc240159064e8da94aa06b1d654`.

### 실제 생성·재현

| 생성 방식 | dataset_id | 결과 |
|---|---|---|
| Chrome 8088, 쿼드콥터 진단 seed 42 | `82819fa0-7f34-4539-9d5e-358a4f2f8afb` | complete; 301 truth / 903 observations; 11개 inventory hash 일치 |
| 사용자 in-app 8088, 같은 설정·seed | `7cee3913-c9f3-48cb-a72e-4ddaee4a0915` | complete; 새 UUID; 위 결과와 observations/truth hash 각각 일치; 11개 inventory hash 일치 |
| CLI X8 diagnostic seed 42 | `682915fb-7975-462a-8081-2e4d67a377d3` | exit 0, complete; 변경 전 X8 observations hash 일치 |

쿼드콥터 두 실행의 observations SHA-256은 `58248968c436da2a7cd5ba03656086ee5afd3740bee4f2b4cb84bb4989f7c0d1`, truth는 `532985a056cf5bbe88e4133170dd3aa8daf14361bfb11e6856cf3019e06b1e47`다. object/model/source/input 식별자, 고정 commit의 source 3개 hash, 물리/실험 config snapshot을 실제 manifest/provenance에서 확인했다.

X8 observations SHA-256은 `1631d12ef2be1608ecd409a2b4dd41735ed5fd1023c7e4c45a099a3db9ac754d`로 기존 `421fbcc5-2086-49d6-a15d-d900494427e3` 결과와 일치한다.

### Browser·서버

- 같은 `127.0.0.1:8088`만 사용했다. 기존 X8 pilot 완료 후 해당 서버를 재시작했으며 다른 port를 만들지 않았다.
- 쿼드콥터 실제 네 그래프의 선·마커를 screenshot으로 확인했다. public identity가 X8로 바뀌지 않고 사람이 읽는 Crazyflie 이름으로 표시된다.
- Play에서 step 66 / 13.2초 → step 150 / 30초로 진행했다. Pause 후 step 165 / 33초가 후속 조회·생성 작업 중에도 유지됐다. 확인한 Browser log에 app error는 없었다.
- 쿼드콥터 pilot 중단 시험은 `0/100`에서 소유 worker만 중단했다. 완료된 데이터와 열려 있던 궤적은 보존됐다. **쿼드콥터 100개 전체 batch 성공은 확인하지 않았다.**
- 사용자 in-app 탭도 새로고침했고 같은 seed 쿼드콥터를 생성해 결과를 열었다. 서버 재시작 전 페이지의 오래된 dataset key는 새로고침으로 해소했다.

### 남은 일과 연구 해석

VTOL·헬기의 실제 생성은 아직 구현하지 않았다. UI 지원 상태 표시와 공통 연결 구조를 이 두 모델의 통합 완료로 세지 않는다. 필수 계수/수식을 확보하거나, 공개 단순화 모델을 명시적으로 허용받은 뒤 객체별 물리·실행 검증을 진행한다. [원문 감사](multi_object_source_audit.md)의 구체적 누락을 기준으로 이어간다.

Crazyflie는 published model reproduction이며 독립 실비행 재검증이 아니다. 수 m 저속 진단의 controller/waypoint 범위를 실제 기체의 전체 기동 다양성으로 주장하지 않는다. 공개 원문의 accelerometer 변환 불일치·Izz 추론 제한을 보존했다. 자동 Grok 자문은 로그인 문제로 이용하지 못했으며 그 review를 통과했다고 주장하지 않는다.
