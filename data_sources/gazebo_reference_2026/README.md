# Gazebo reduced-force reference sources

취득일: 2026-09-07. 네 객체의 독립 병진 운동 계산을 위한 고정 공개 원본이다. meshes·CAD·센서 자료는 가져오지 않았다. LF text snapshot이며 로컬 파일 SHA-256은 구현 config와 실행 manifest에서 확인한다. 원본은 수정하지 않는다.

- PX4/PX4-gazebo-models `5577035667afb4b63fe1f966fb1a58bbb05d905b`: `models/{x500,x500_base,rc_cessna,standard_vtol}/model.sdf`를 이름별 SDF로 보관.
- gazebosim/gz-sim `9647bb4a461d48343801e33109e9f615fb2712f1`: `src/systems/lift_drag/LiftDrag.cc`, `src/systems/multicopter_motor_model/MulticopterMotorModel.cc`.
- srmainwaring/SITL_Models `58b9e688555fe2e6fe43571269a49ddb851f82f5`: `Gazebo/models/{helicopter,rotor_head_cw}/model.sdf`. ArduPilot/SITL_Models PR #149의 미병합 후보.

원문 링크·계수 역할은 [원본 대조표](../../plan/gazebo_minimum_constraints.md)를 따른다. C++ 원문에는 Apache-2.0 copyright/license notice가 있으며 그대로 보존했다. SDF 및 다른 외부 자료의 이용 조건은 각 upstream을 따른다. 이 프로젝트의 MIT license로 재허가하지 않으며 외부 원본은 기존 .gitignore에 따라 Git 대상에서 제외한다. 외부 재배포는 이번 작업 범위가 아니다.

Gazebo/PX4 runtime을 설치하지 않는다. 독립 구현은 source force primitives와 질량을 사용하지만 회전 동역학·제어기·기구를 완전히 재현하지 않는다. controller 및 trial limits는 시뮬레이션 설계이며 source maxima로 표시하지 않는다.

`MulticopterMotorModel.cc`의 thrust와 rotor drag 축은 서로 다르게 보존한다. thrust는 motor link pose가 회전한 local `+Z`이고 rotor drag는 joint pose가 회전한 joint axis의 수직 유속에 적용된다. 따라서 source snapshot의 RC Cessna·Standard VTOL puller처럼 두 축이 다른 경우에도 reduced implementation이 하나의 축으로 치환하지 않는다. 이 frame parsing은 source force 식의 충실성을 위한 것이며 Gazebo runtime·6-DOF 재현 검증은 아니다.
