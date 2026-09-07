# 후속 객체: VTOL 자료·운동모델 검토

날짜: 2026-09-07. 상태: 후보 조사 / 운동모델 미채택 / 미구현.  
합의 출처: 사용자가 X8 다음 대상을 `VTOL — 수직이착륙·순항 전환부터`로 선택했다. 이번 선택은 검토 대상을 정한 것이며 특정 논문 모델이나 계수의 채택이 아니다.

후속 사용자 요청으로 추가 대상이 VTOL·쿼드콥터·헬기로 확대됐다. 범위와 연결 구조 제안은 [세 객체 확대 기록](multi_object_generation_scope.md)을 따른다. 아래 VTOL 근거 점검과 미채택 상태는 유지한다.

## 이번에 확인한 것

| 후보 | 확인된 근거 | 연구에 적용할 때 남는 제한 |
|---|---|---|
| Rohr et al. (2019), *Attitude- and Cruise Control of a VTOL Tiltwing UAV* | 운동모델·제어기와 hover/transition/cruise 실비행 시험을 보고. DOI `10.1109/LRA.2019.2914340`은 저자 arXiv 페이지에서도 연결됨. [저자 원문](https://arxiv.org/abs/1903.10623) | 실제 비행 검증이 있는 모델 후보. 모든 계수/구현 코드/원본 로그를 확보·재현했다는 뜻은 아님. tiltwing과 Lift+Cruise 기체는 구분해야 함. |
| Kim et al. (2025), *Smooth Reference Command Generation and Control for Transition Flight of VTOL Aircraft Using Time-Varying Optimization* | AIAA SciTech 2025, DOI `10.2514/6.2025-1123`; hover→cruise와 역전환을 다루는 공식 코드 공개. [논문](https://arxiv.org/abs/2501.00739), [공식 코드](https://github.com/JinraeKim/VTOLSmoothTransitionFlight) | 논문 2.1절은 **2D Lift+Cruise**, 검증은 numerical simulation. 현재 3D 실측 기반 생성기의 주 근거로 곧바로 채택하지 않음. 전환 제어 아이디어의 참고 후보. |
| Islam/Dony/Hasan/Hossain, quadplane 공개 자료 | repository가 실비행 전환·역전환 CSV, local position/velocity, actuator 출력과 CFD 자료를 공개했다고 설명. [원본 저장소](https://github.com/AdamDony/quadplane-px4-flight-data) | README는 2026 under-review manuscript, About는 SciTech 2027로 표기가 달라 게재 확정 근거로 쓰지 않음. 원본 전체 무결성 감사·모델 식별은 아직 하지 않음. |

마지막 후보의 [data dictionary](https://github.com/AdamDony/quadplane-px4-flight-data/blob/main/docs/DATA_DICTIONARY.md)는 **CSV에 행별 timestamp가 없고 세션 길이에 균일 시간축을 가정한다**고 명시한다. 따라서 전환 양상/구간별 움직임의 참고 후보로는 남기지만, 가속도·전환 지연·입력 응답을 정밀하게 식별하는 주 자료로 채택하는 것은 보류한다. 이는 GPS 센서를 구현해야 한다는 뜻이 아니라 원본 운동의 시간 대응이 확인되어야 한다는 뜻이다.

논문 저자·제목·연도·DOI와 원문의 검증 종류를 확인했다. Scholar 전용 검색에서는 직접 일치 결과를 확보하지 못해 저자 arXiv/공식 저장소를 사용했다. 전체 문헌 검색을 완료했다는 주장은 하지 않는다.

## 다음 구체적 작업

1. 실비행 시험 근거가 있는 후보에서 객체 형식(tiltwing/Lift+Cruise), 3D 운동식, 계수 출처, 재현 가능한 코드·시간 정보 있는 원본 로그를 확인한다.
2. 선택한 **한 기체**의 hover → 전환 → 순항 → 역전환 → 수직 이동이 연속 상태로 계산되는 최소 생성기를 설계한다. 서로 다른 기체의 계수를 섞지 않는다.
3. 신규 생성은 기존 합의대로 무풍·단일 객체·3D·60초·5 Hz로 연결할지 지원 범위를 확인한다. 기존 X8 계수·시나리오는 수정하지 않는다.
4. 채택 근거와 파일 배치를 정한 후 별도 생성 구현을 진행하고, 동일 public/evaluation 계약 및 8088 UI에 등록한다. 미구현 VTOL을 현재 UI 선택지에 노출하지 않는다.

GPS/카메라/센서 모델, 실제 탑재, 예측기 구현은 이번 확대 검토의 요구사항이 아니다. 원본은 운동모델의 근거로 사용하며 새 궤적은 그 모델에서 합성한다.
