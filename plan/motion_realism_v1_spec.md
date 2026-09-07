# 쿼드콥터 우선 운동 현실화 명세

날짜: 2026-09-07. 승인: 사용자 “일단 쿼드콥터 부터 움직임 데이터 현실화 하자. 그리고 VTOL”.

## 목표와 판정 경계

실제 비행과 비교하는 절차를 갖춘 간소화 생성으로 개선한다. 순서는 쿼드콥터 → 표시 확인 → VTOL이다. 수치 안정성만 통과하고 현실성이 검증됐다고 보고하지 않는다. 내부 장치를 추가 재현하는 작업은 하지 않는다. 쿼드콥터의 기존 공개 물리 계수는 유지하고 명령·추종·실제 운동 비교를 우선 개선한다.

쿼드콥터의 첫 확인 범위는 Crazyflie 2.1의 저속 실내 비행이다. 고속·야외·FPV 전체 비행영역의 현실화로 확대 해석하지 않는다. 비교 자료의 추가 deck와 기존 27 g 식별 모델 간 차이가 있으므로 계수를 동일 실기체에서 재식별했다고 주장하지 않는다.

## 보존 조건

- 같은 v1, Local ENU, 60초·5 Hz·301개, 단일 객체, 신규 무풍을 유지한다.
- 관측오차 `sigma=1,3,5 m`, public/evaluation 구분과 기존 CSV 계약을 유지한다.
- 기존 source·dataset·manifest를 덮어쓰지 않는다. 기존 X8·헬기 동작은 변경하지 않는다.
- viewer는 `127.0.0.1:8088`만 사용한다. 사용자 요청 없는 Git commit/init, 다른 프로젝트 변경, AGENTS 자동 수정은 하지 않는다.
- 실행마다 달라지는 수치·경로·ID는 config/metadata에서 읽는다. Python에 하드코딩하지 않는다.
- 원본 위치를 재생하거나 조각을 이어 붙인 것을 새 합성 운동으로 표시하지 않는다.

## 쿼드콥터 근거

Bitcraze 공식 `positioning_dataset`, commit `275865f169ace04221daf7e7630b98d97fde41bd`의 `data/lh2_kalman_flight/mocap00..03.npy`를 확보했다. 원본 위치는 m, 원본 시간은 ms이며 앞의 4열이 시간과 marker 중심 XYZ다. 데이터 수집 설명상 00/01은 sweep(평균 명령 속도 0.25/0.5 m/s), 02/03은 random flight(0.25/0.5 m/s)다. 손으로 막대에 붙여 움직인 자료와 jitter-only 자료는 사용하지 않는다.

00/01로 운동 범위와 가감속을 분석하고 02/03은 후속 비교용으로 분리한다. raw NaN·비단조 시간·긴 결측을 숨기지 않는다. 최대 0.05초 간격의 유효 구간만 연결할 수 있고 긴 결측은 구간을 분리한다. 분석은 0.02초 간격, 11개 sample의 3차 Savitzky–Golay를 사용하고 미분에 필요한 양끝 5개 sample을 제외한다. 이 전처리는 분석 설정이지 public 관측의 평활화가 아니다. 원본과 처리 결과를 구분하고 사용 구간·처리율을 보고한다.

임의로 만든 3 m 고정 순서 대신 매 Episode의 새 목표와 속도를 명시 범위에서 선택한다. 목표 사이의 reference는 위치·속도·가속도·jerk가 양끝에서 연결되는 7차 rest-to-rest profile을 사용한다. 실제 기체는 reference 위치를 복사하지 않고 기존 6-DOF로 따라가며, PVA feedforward와 feedback을 사용한다. 각 구간은 기하 거리와 요청 평균 속도로 정한 시간, reference 최대 속도·가속도 제한을 함께 만족해야 한다. 참조 도착 후 아직 실제 위치/속도가 허용오차 밖이면 위치를 강제로 맞추지 않는다.

실제 비행 자료로 보정한 운동 범위, 합성 명령 설계, controller 설정을 구분한다. 실제 로그의 control 입력은 확보하지 않았으므로 외부 위치곡선을 따라가는 비교는 tracking/reference 비교이지 원래 조종 입력의 open-loop 재현이 아니다. 실제/기존/개선 결과의 속도·가속도·저속 비율·전환 연속성을 보고하며 단일 지표의 임의 합격선을 실비행 검증으로 승격하지 않는다.

## 표시

기존 화면의 움직이는 diamond는 noisy observation이었다. evaluation truth가 허용되고 켜진 때에만 truth 현재 위치를 별도의 표식으로 함께 움직여 사용자가 생성 운동과 관측오차를 구분하도록 한다. public-only는 truth 파일·표식을 읽거나 추가하지 않는다. 저장 시점을 보간하거나 관측오차를 줄여 보이지 않게 만들지 않는다.

## VTOL 후속

쿼드콥터 확인 뒤 진행한다. 기존 Tal Tailsitter 참고 profile의 독립 속도/선회 난수와 시간 종료만으로 넘어가는 명령은 개선 대상이다. 실제 자료·출판된 전환 사례의 시간/속도 관계를 먼저 확인하고, 운동 단계별 연결과 `speed × track_turn_rate`의 가속 한도 관계를 함께 만족하도록 구성한다. 일반 VTOL 전체와 특정 Tailsitter의 차이를 명시한다. 실제 위치 원본을 확보하지 못하면 해당 검증을 완료했다고 주장하지 않는다.

## 완료 증거

TDD RED/GREEN, scoped Ruff 및 전체 회귀 suite, 독립 코드 검토, 소량 새 dataset의 provenance·연속성 확인, 실제 자료와 비교한 CSV/그림, 8088에서 truth/observation을 구분한 재생 확인을 남긴다. 소프트웨어 완료와 현실성 근거의 범위·남은 제한을 각각 보고한다.

## VTOL 근거 검토 및 구현 범위 구체화

2026-09-07 원문 확인:

- [Tal & Karaman 원문](https://arxiv.org/abs/2207.13218v1)의 Section VI-E(PDF p.15)에서 실제 시험은 3.5 m 반경 궤도에서 8 m/s까지 3초 전환, 2.7 m/s² 접선 가속을 사용했다. 이는 공격적인 특정 Tailsitter 시험 사례이며 일반 VTOL의 공인 한도가 아니다. 원문 p.16의 원운동 실험 그림에는 약 20 m/s²의 전체 가속도가 나온다. 따라서 접선 2.7과 전체 가속 한도를 혼동하거나, 3 m/s² 제한의 생성기에 동일한 3.5 m/8 m/s 원운동을 요구하지 않는다.
- [AdamDony Quadplane 자료](https://github.com/AdamDony/quadplane-px4-flight-data/blob/main/docs/DATA_DICTIONARY.md)는 local position/velocity가 있지만 개별 CSV row의 timestamp가 없고 세션 길이로 균등 시간을 구성한다고 명시한다. 이 자료를 가속도·전환 시간의 정밀 fitting에 사용하지 않는다. 또한 다른 Quadplane이며 미출판 심사 중 manuscript다. 현재 Tailsitter의 계수·정량 검증으로 섞지 않는다.
- [VTOLSmoothTransitionFlight](https://github.com/JinraeKim/VTOLSmoothTransitionFlight)는 수치 시뮬레이션이므로 실제 비행 검증 근거로 사용하지 않는다.
- [KristofWesely 시험 로그 목록](https://github.com/KristofWesely/VTOL-fixed-wing-drone-for-data-collection/tree/main/Flight%20logs)은 hover/loiter와 crash/oscillation을 명명한 binary가 주로 확인됐으며, 성공적인 전진 전환의 timestamped XYZ 검증본을 확보한 상태가 아니다. 추가 원본 검증 없이 전환 reference로 승격하지 않는다.

따라서 이번 VTOL은 기존 Tal Tailsitter 출처를 유지하고 **출판된 비행 사례를 참고한 간소화 운동 개선**까지 수행한다. 검증된 실비행 trajectory fitting으로 표기하지 않는다. 개선 내용은 단계별 상태 확인, 연속적인 속도·선회 명령 변화, 실행 가능한 속도/선회 조합, 반복되는 짧은 지그재그 대신 상승→전진 전환→순항/완만한 선회→감속→hover/하강으로 연결하는 단일 시퀀스다. 60초 안에 모든 단계를 억지로 압축하거나 마지막 상태를 순간 재설정하지 않는다. 기존 helicopter/legacy profile은 이전 동작을 보존한다.
