# 세 객체 v1 — 공개 근거 채택과 구현 제한

2026-09-07. 이 문서는 원문 확인 결과이며 객체 생성 완료 기록은 아니다.

후속 사용자 승인에 따라 VTOL·헬기는 내부 장치를 복제하지 않는 간소화 운동모델로 진행한다. 아래의 누락 계수는 **full-dynamics 재현의 제한**이지 새 운동모델 구현의 blocker가 아니다. 현재 적용 범위는 [간소화 통합 기록](reduced_motion_v1_implementation.md)을 따른다.

## 쿼드콥터: Crazyflie 2.1 공개 식별 모델

Eschmann, Albani, Loianno (2024), *Data-Driven System Identification of Quadrotors Subject to Motor Delays*. [논문 v2](https://arxiv.org/abs/2404.07837v2), [공식 코드의 고정 commit](https://github.com/arplaboratory/data-driven-system-identification/tree/2d267dd07b4262f579ee223d20b26a6dc9d17147).

같은 기체의 질량, 관성, 로터 배치, 추력 다항식, 추력-반토크 비례 계수, motor delay를 사용한다. 원문 PDF·notebook·LICENSE를 실제 확보했고 `configs/profiles/crazyflie_eschmann_2024.yaml`에서 SHA-256으로 추적한다. 다른 큰 쿼드콥터의 계수나 RotorPy의 가정 drag를 섞지 않는다.

채택 상태는 **PUBLISHED_MODEL_REPRODUCTION**, 독립 실비행 검증 완료가 아니다. 특히 공식 notebook의 z accelerometer 단위 변환에 `9.18`이 사용되고 다른 축의 `9.81`과 다르다. 이 문제를 숨기거나 원본을 수정해 재식별한 것처럼 쓰지 않는다. Izz도 독립 측정값이 아니라 관성비를 이용한 추론이다. 저속 공중 운동만 대상으로 하며 지면 접촉·motor-off는 지원하지 않는다. 물리 계수와 프로젝트 controller/기동 설정은 config에서 분리한다.

세부 계수·파일 hash·출처 한계는 [source README](../data_sources/eschmann_crazyflie_2024/README.md)에 기록한다.

## VTOL: 실비행 근거와 실행 가능한 완전한 계수는 별개

| 원문 | 확인된 실험·모델 | 그대로 구현할 때 남는 문제 |
|---|---|---|
| Rohr et al. (2019), *Attitude- and Cruise Control of a VTOL Tiltwing UAV*, DOI `10.1109/LRA.2019.2914340`, [원문](https://arxiv.org/abs/1903.10623) | tiltwing의 hover/transition/cruise 실제 비행 | 관성·프로펠러/익형 곡선 등 전체 수치 세트 미확보 |
| Ntouros & Smeur (2026), *Coordinated Incremental Trajectory Tracking of a Tailsitter Drone*, [preprint](https://arxiv.org/abs/2607.11651v1) | Parrot Swing 실내 3D 실험, phi 모델·actuator 방정식 | 원문 계수와 정규화 입력을 문자 그대로 적용하면 최대 추력 가속도 `1.768 m/s²`가 중력보다 작아 hover 불가. 연결 코드에는 다른 voltage/input scale이 있지만 논문 계수에 임의로 섞을 근거가 없음 |
| Tal & Karaman (2022), *Global Incremental Flight Control for Agile Maneuvering of a Tailsitter Flying Wing*, DOI `10.2514/1.g006645`, [원문](https://arxiv.org/abs/2207.13218) | NED 6-DOF, 두 모터·두 flap, hover↔forward 실제 실험 | 관성, 추력/토크 계수, throttle 다항식과 일부 lever arm·actuator 제한 수치 미공개 |
| Tal, Ryou, Karaman (2023), *Aerobatic Trajectory Generation for a VTOL Fixed-Wing Aircraft Using Differential Flatness*, [원문](https://arxiv.org/abs/2207.03524) | 위 모델을 사용하는 실제 aerobatic 실험 | 누락된 전체 parameter package가 추가로 공개되지 않음 |

Parrot의 모순은 PDF 원문 그림과 HTML 양쪽을 확인했다. 추력 계수를 임의 배수로 보정하지 않는다. Tal 논문의 식별된 공력 계수만 있고 관성/입력 변환이 없는 상태도 완전한 simulator로 처리하지 않는다.

Kim et al. (2025)의 [공식 코드](https://github.com/JinraeKim/VTOLSmoothTransitionFlight)는 재현 가능한 **2D numerical Lift+Cruise** 예제다. 현재 합의한 3D 실측 기반 모델로 조용히 대체하지 않는다. 기존 [VTOL 조사 기록](vtol_source_review.md)의 timestamp 없는 CSV 제한도 유지한다.

현 상태: **필수 계수 확보 대기**. 논문이 존재한다는 사실이나 후보 이름을 UI에 표시한 것만으로 생성 가능이라고 표시하지 않는다. 이번 감사는 공개 모델 전체가 불가능하다는 증명이 아니라 위 후보들의 채택 한계다.

## 일반 헬기: NASA UH-60 모델과 판독이 남은 제어 계통

일관된 한 모델을 구성하는 두 정부 보고서를 확인했다.

- Talbot et al., NASA TM-84281: [공식 원문](https://ntrs.nasa.gov/api/citations/19830001781/downloads/19830001781.pdf). 6개 rigid-body, 3개 rotor flapping, 1개 rotor-speed 자유도와 main/tail rotor 힘·모멘트·inflow.
- Hilbert, NASA TM-85890: [공식 원문](https://ntrs.nasa.gov/api/citations/19840015585/downloads/19840015585.pdf). UH-60 기체 계수, fuselage 회귀식, 기울어진 tail rotor, 가변 stabilator, PBA 제어를 추가하는 동일 계통 모델.

원본 PDF SHA-256은 각각 `8a765e4f6db308e1ea184dbf6d2ff326f0d9f9a3f2fa2bc0d83d53721c511fdb`, `2bc58d43a399c4ef09c96eb704f01d384c43f70583d586826368358fcf88fc0c`다. 최초 조사 때의 HTTP 403 제한은 해소했고 이번에는 실제 PDF를 열어 확인했다.

Table 1–3의 질량/관성/로터·표면 계수와 제어 연결을 확인했다. Table 4는 near-hover에서 전진 비행까지 **level-flight trim과 안정성 미계수**의 비교 근거이며 transient 선회/상승의 실비행 검증은 아니다.

Howlett CR-166309까지 추적해 PBA 입력·gain·rate/position limit를 읽었다. Stabilator의 servo와 일부 제한은 확인했지만 원본 scan에서 speed-scheduled feedback 일부 값이 명확하지 않다. **해당 값을 추정해 full model 재현으로 부르지 않는다.** 고정 stabilator나 hover 국소 모델로 범위를 바꾸려면 그 단순화를 명시하고 별도로 합의한다. 다른 helicopter의 계수를 혼합하거나 쿼드콥터 mixer로 대체하지 않는다.

현 상태: **원문 전사 일부 미확정 / 물리 생성 미구현**. 세부 감사·판독 과정은 `temp/multi-object-v1/helicopter-evidence.md`와 전사 기록에 남겨 두며, 실제 기체 profile 채택 전 이 문서에 확정 수치를 승격한다.

## 실행과 연구 주장

공통 writer/계약/선택 UI는 v1에서 확장한다. 각 객체의 생성 가능 여부는 실제 구현·preset·실행 검증으로 판정한다. source hash 일치, software test 통과, 실제 비행 재현, 모델의 적용 범위는 서로 다른 근거다. 사용자에게 질문한 내부 장치 생략은 후속 답변으로 승인됐다. 자료 참고 운동 특성과 시뮬레이션용 응답/제한을 구분하며, 미수행 raw-log fit이나 실제 비행 재현을 주장하지 않는다.

## 간소화 모델에 채택한 실제 보관 원문

- VTOL: [Tal–Karaman source README](../data_sources/tal_tailsitter_2022/README.md). 고정 arXiv v1 PDF를 확보하고 Table IV 및 VI-E를 확인했다. 8m/s급 hover↔전진 비행 사례는 운동 범위 참고이며, body angular rate를 track turn rate로 바꾸어 쓰지 않는다.
- 헬기: [NASA source README](../data_sources/nasa_uh60_1984/README.md). TM-85890 원본을 보관하고 Table 4를 다시 확인했다. Equivalent airspeed의 steady trim reference를 ENU 실측 속도나 transient 검증으로 쓰지 않는다.
- 두 profile의 operating speed/acceleration/turn/vertical response와 기동 순서/시간은 `simulation_design`으로 명시한다. 이 방식은 새로운 위치 곡선을 강제로 그리거나 기존 X8 출력에 다른 이름을 붙이는 방식이 아니다.
