# 쿼드콥터·VTOL 운동 현실화 v1 실행 기록

날짜: 2026-09-07. 명세: [motion_realism_v1_spec.md](motion_realism_v1_spec.md).

## 현재 상태

| 범위 | 소프트웨어 상태 | 과학적 주장 범위 |
|---|---|---|
| 쿼드콥터 | 구현·독립 검토·새 데이터·Browser 재생 확인 | 저속 실내 Crazyflie 자료를 참고한 새 reference 생성. 독립 실비행 검증 아님 |
| 재생 화면 | 실제 위치 circle / 관측 위치 diamond, wire-format 회귀 수정 및 Browser 확인 | 저장 위치를 그대로 표시. 관측오차를 줄이거나 보간하지 않음 |
| VTOL | 구현·10-seed·새 CLI/UI 생성 및 Browser 재생 확인, 독립 검토 Approved | Tal Tailsitter의 출판된 비행 사례를 참고. timestamped 실측 위치 fitting 미수행 |
| 전체 통합 검사 | 최종 167 passed, Ruff PASS, 전체 26-file 독립 검토 Approved | 소프트웨어 검사와 실비행 현실성 검증을 구분 |

## 쿼드콥터 변경

Bitcraze 공식 [positioning_dataset](https://github.com/bitcraze/positioning_dataset)의 고정 commit `275865f169ace04221daf7e7630b98d97fde41bd`에서 실제 비행 `mocap00`/`mocap01`을 calibration 자료로, `mocap02`/`mocap03`을 비교 전용으로 분리했다. 원본 README·수집 script·NPY 7개의 SHA-256은 보존했다.

- 고정 polygon의 목표점을 바꾸던 방식에서 seed별 새 3D 목표와 연속적인 출발·감속·정지 reference로 변경했다.
- 실제 위치는 기존 physical integration으로 계산한다. 원본 위치를 복사하거나 실제 상태를 목표점에 강제 대입하지 않는다.
- reference가 끝나도 실제 위치·속도가 허용오차 안에서 유지될 때까지 기다리며, 제한 시간 내 도달하지 못하면 실패를 기록한다.
- 실행 시작 시 reference 설정 전체와 SHA-256을 한 번 고정하고 모든 Episode가 그 snapshot을 사용한다.
- 기존 physical coefficients 및 legacy waypoint 실행 경로는 유지했다.

`0.75 m/s` reference peak speed와 `1.0 m/s²` reference peak acceleration은 실측 자료를 참고해 선택한 **설계값**이다. 실기체에서 식별한 계수나 실제 상태의 강제 상한이 아니다. 자료의 추가 deck와 기존 27 g physical model의 차이도 남아 있다.

### 결과 위치

- 원본 분석: `outputs/data_generation/v1/abf5e75e-a211-44cc-af6a-36a02d15bac6/`
- 선행 쿼드콥터 CLI 진단: `outputs/data_generation/v1/9f5bd82d-20c7-47d2-9d01-688dea9c5ead/`
- 최종 코드의 8088 UI 진단: `outputs/data_generation/v1/cc8dac82-4a2f-42bd-804f-b093a45df9ff/`
- Master seed: `42`, Episode seed: `2684470948`.
- 선행 CLI code hash: `b7e453d0425794ddbe8bb5ab3f0167cb089ee07f3f0942999d5fdd215234855d`.
- 최종 UI code hash: `b21a3082123a904183ce20547a3a2f2cff13a3217dcd8437a3ec415c0ca22ad0` (같은 generator의 VTOL 변경 포함).
- Truth SHA-256: `cd78cf19636d10222f563a8433844b02b0bb301b9c032890851bb06c9654659b`.

분석 결과는 별도의 analysis-kind artifact이며 학습용 public dataset이 아니다. 생성 dataset의 기존 physical-source provenance 형식은 유지하고, 추가 Bitcraze evidence는 effective model snapshot 및 분석 artifact에 기록한다.

최종 UI 진단도 public contract 및 11/11 inventory hash를 통과했고, 선행 CLI와 episodes·observations·truth·diagnostics·commands·triggers 6개 CSV가 byte 단위로 일치했다. 현재 8088 화면에는 이 UI 진단을 열어두고 sigma1·truth 표시·step0 일시정지 상태로 준비했다. 이번 서버의 재생 데이터 목록에서 VTOL UI 진단도 선택할 수 있다.

### 실측 참고 및 이전 생성과 비교

아래 생성 비교는 동일 master seed 42의 이전 `7cee3913-c9f3-48cb-a72e-4ddaee4a0915`와 새 진단의 actual truth에서 계산했다. 같은 seed라도 reference 방식이 달라 같은 경로를 추종하는 대조 실험은 아니다.

| 지표 | 이전 생성 | 새 생성 |
|---|---:|---:|
| XYZ 이동 범위 (m) | 4.017 / 4.009 / 1.587 | 1.574 / 1.671 / 1.229 |
| 총 이동 거리 (m) | 38.843 | 19.931 |
| 실제 속도 median / q95 / max (m/s) | 0.723 / 1.156 / 1.178 | 0.250 / 0.872 / 1.032 |
| 실제 가속도 median / q95 / max (m/s²) | 0.351 / 1.452 / 1.600 | 0.464 / 1.363 / 1.853 |
| 연속 저장 가속도 벡터 차이 max (m/s²) | 0.721155 | 1.079919 |

Calibration 두 비행의 speed q95는 0.551/1.027 m/s, acceleration q95는 0.612/1.319 m/s²다. 새 actual acceleration q95는 calibration 최대 q95를 약간 초과한다. 실제 가속도 변화량도 전부 개선된 것은 아니다. **Reference 연결이 부드럽다는 사실을 actual jerk 감소나 전체 실비행 현실성 검증으로 해석하지 않는다.**

분석 CSV는 invalid/gap, 짧은 구간, resampling 끝 잔여 시간, filter 양끝, 비행 높이 filter, 실제 사용 interval을 별도로 기록한다. 원본 span과 각 시간의 합 차이는 최대 `8.53e-14 s`로 확인했다. Sample exposure와 elapsed interval time은 구분한다.

## 재생 화면 검증

포트는 `127.0.0.1:8088`만 사용했다. 생성 작업이 없음을 확인한 후 이 작업이 실행한 서버만 교체했다.

- Simulation truth current: circle. Observation current: diamond.
- XY/XZ/YZ/3D 모두 같은 저장 step을 사용한다. 매 tick에는 current marker만 갱신한다.
- `show_truth=False`와 `public_only=True`에서는 truth를 표시하지 않는다. Public-only는 evaluation 파일도 읽지 않는다.
- 관측오차 `sigma=1,3,5 m`와 60초·5 Hz·301개 계약은 그대로다.

초기 Browser 검사에서 NumPy current-marker 좌표의 Plotly 6 typed-binary 직렬화 때문에 `extendTraces`가 실패했다. 시간 숫자만 진행되는 것을 정상 재생으로 판단하지 않고, JSON wire-format 회귀를 추가해 current-marker 좌표만 일반 list로 수정했다.

수정 후 fresh reload에서 실제 SVG point transform 변경, Play/Pause, 마지막 step 300, 처음 step 0으로 되감기, truth 숨김·복원을 확인했고 신규 console error는 없었다. Python tests는 1~4 Episode, 모든 투영, endpoint/backward payload 및 wire format을 검사한다.

추가 VTOL UI 검사에서는 자동화의 End/Home 키 입력만으로 slider aria 값과 callback 상태가 함께 갱신되는 것을 확인하지 못했다. 마우스의 0초/60초 눈금 선택은 정상 반영됐다. 따라서 이 기록은 키보드 시점 이동 전체를 검증 완료로 일반화하지 않는다. Slider/app 코드는 이번 변경 대상이 아니었다.

## VTOL 변경과 결과

Tal Tailsitter profile만 명시적으로 `vtol_phase_aware_v1`을 선택한다. Selector가 없는 이전 VTOL과 헬기는 기존 실행 경로를 유지한다.

- 한 번의 hover → climb → level-off → forward transition → cruise → 좌 또는 우 선회 → cruise → back transition → hover → 선택적 descent → final hover 순서다.
- 목표 speed와 track turn rate를 수평 가속 한도 안에서 함께 선택한다. 불가능한 조합은 적분 전에 거부한다.
- Reference는 endpoint-flat blend로 연결하고 실제 속도·수직 속도·선회율이 허용오차 안에서 유지된 후에만 다음 단계로 간다. 실제 위치·속도·heading을 목표값으로 강제 대입하지 않는다.
- 제한 시간 초과는 실패, 60초 관측창 종료로 미완료된 단계는 cutoff로 구분한다. 마지막 hover 이후 두 번째 이륙을 시작하지 않는다.

| 비교 항목 | 공개 사례 | 새 simulation 설정·확인 결과 |
|---|---|---|
| 전진 transition | Tal Section VI-E: 8 m/s, 3 s, 접선 가속 2.7 m/s² | 목표 5.5–6.5 m/s, 최소/reference 4–5 s + 실제 상태 확인. 실측 fitting 아님 |
| 선회 | 3.5 m radius 사례는 현재 3 m/s² 전체 수평 가속 한도와 양립하지 않음 | 목표 14–20 deg/s와 speed를 가속 budget에 맞춰 선택. Body angular rate를 track rate로 사용하지 않음 |
| 실제 생성 상태 | Timestamped source XYZ 미확보 | CLI master seed42: 최대 수평 속도 6.424271 m/s, 최대 수평 가속 3.000000 m/s², 마지막 전체 속도 약 2.93e-10 m/s |

Direct Episode seed42의 `a_i = (v_(i+1) - v_i) / 0.2 s`, `max ||a_(i+1) - a_i||`는 이전 `2.931395`에서 새 `1.622365 m/s²`로 약 44.7% 감소했다. 이는 저장 간격 기준 가속도 변화량이며 순간 jerk 상한이나 실비행 검증 결과가 아니다. 같은 seed라도 기동 구성 자체가 바뀌었다. Seeds 0–9는 10/10 complete, 301 samples, NaN·timeout·cutoff 없이 끝났다.

- CLI 결과: `outputs/data_generation/v1/6ba40386-d36e-41b7-8681-446874dc96b3/`.
- 8088 UI 생성 결과: `outputs/data_generation/v1/20948e86-7734-4807-ab73-1c42d4d7b940/`.
- 두 결과 모두 master seed42, Episode seed2684470948, code hash `b21a3082123a904183ce20547a3a2f2cff13a3217dcd8437a3ec415c0ca22ad0`.
- Truth SHA-256: `be57458634726df0ede0315b88a34d46120c32e51b55e099ea1e6f01e962ee8b`.
- Parent가 두 public contract와 각 11/11 inventory hash를 확인했다. CLI/UI의 episodes·observations·truth·diagnostics·commands·triggers 6개 CSV가 byte 단위로 일치했다.
- Browser에서 VTOL 설정 → seed42 생성 → 결과 열기 → 실제 XY/XZ/YZ marker 이동 → Pause → 60초/0초 눈금 이동을 확인했다. Console error는 없었다. 전체 페이지 screenshot의 중복 stitching은 DOM 중복으로 판정하지 않았다.
- 헬기 seed17, 이전 selector-free VTOL seed42의 position/velocity SHA-256은 before-task baseline과 일치했다. 기존 데이터와 원본 PDF는 보존했다.

## 현재까지의 실행 증거

- 쿼드콥터 초기 전체 suite: `150 passed in 193.62s` (후속 수정 전 기록).
- 쿼드콥터 마지막 수정 focused suite: `37 passed in 19.07s`, Ruff PASS.
- 추가 Episode seeds 0/1/42/73: 모두 complete, finite `(301,3)` 위치.
- 재생 화면 최종 focused suite: `17 passed in 40.69s`, Ruff PASS.
- 쿼드콥터 최종 진단 public contract 및 11/11 inventory hash 확인. 이전 source-grounded seed42와 truth/observation bytes 일치.
- 분석 artifact 7/7 inventory hash 확인.
- 쿼드콥터 및 화면의 scoped 독립 검토: Approved.
- VTOL focused suite: `47 passed in 26.14s`, scoped Ruff PASS.
- Parent 전체 Ruff: `data_generation/v1 visualization/v1 contracts/v1` PASS.
- Parent 첫 최종 통합 suite: `1 failed, 164 passed in 199.87s`. 실패는 새 truth-first marker 순서를 반영하지 않은 기존 HTTP UI assertion 한 곳이었다. Production 동작을 되돌리지 않고 truth·observation 모두 검사하도록 assertion을 보완했다.
- 위 UI assertion 수정 독립 검토 Approved. VTOL endpoint-flat 및 3개 reference-channel handoff test 보완도 focused `9 passed in 1.26s`, scoped 재검토 Approved다. 두 보완 모두 test-only이며 production과 profile은 변경하지 않았다.
- Parent **최종 전체 suite: `167 passed in 198.97s`, exit 0**.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp\pt-motion-verified
& .\.venv\Scripts\python.exe -m ruff check data_generation/v1 visualization/v1 contracts/v1
```

최종 Ruff는 `All checks passed!`, exit0이다. `gpt-6-astra` 전체 26-file 독립 검토도 Approved이며 Critical/Important gate와 추가 production 수정 요구는 없었다. 구현 worker는 요청한 `gpt-5.6-terra` max를 사용했다. 상세 최종 검토는 `temp/motion-realism-20260907/final-review.md`에 있다.

최종 `_code_hash()`가 위 VTOL CLI/UI 및 쿼드콥터 UI manifest의 `b21a...2ad0`과 일치함을 다시 확인했다. X8/control/scenario/quadrotor physical implementation과 헬기/X8 preset·profile 9개 파일의 baseline byte 비교 및 Tal PDF 원본 SHA도 일치했다.

이번 변경 후 새 100개 pilot 전체 실행은 하지 않았다. 검증 범위는 자동화 suite, VTOL 10-seed 직접 실행, 쿼드콥터 추가 seed smoke, 객체별 새 단일 Episode CLI/UI 생성 및 재생이다. 이전 reduced-motion 단계의 100개 pilot을 새 profile 검증으로 인용하지 않는다.

세부 RED/GREEN 및 검토 이력은 `temp/motion-realism-20260907/`의 task report·review·progress에 보관한다. Git repository가 아니므로 baseline copies도 복구용으로 유지한다. 기존 source·output은 삭제하지 않았다.

Parent가 이번 최종 검증에 만든 `temp/pt-motion-final`, `temp/pt-motion-verified` 두 pytest fixture 폴더는 완료 뒤 절대 경로·workspace 내부·비-link 여부를 확인해 제거했고 `Test-Path=False`를 확인했다. 보고서·baseline·생성 데이터는 보존했다.

추가 공개자료 검토용 Grok 호출은 로그인되지 않아 실행되지 않았다. 인증 설정은 바꾸지 않았으며, 위 source 판단은 Codex가 원문과 local files로 확인했다.
