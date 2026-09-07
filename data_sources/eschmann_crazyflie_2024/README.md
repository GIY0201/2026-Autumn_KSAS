# Crazyflie 2.1 — 공개 식별 모델 원본

취득일: 2026-09-07. 기체: 27 g Crazyflie 2.1. 상태: 논문·공식 notebook 확보와 계수 대조; 이 프로젝트에서 원본 로그 재식별/독립 실비행 검증은 아직 수행하지 않음.

Jonas Eschmann, Dario Albani, Giuseppe Loianno (2024), *Data-Driven System Identification of Quadrotors Subject to Motor Delays*, IROS 2024 accepted according to author arXiv. [논문 v2](https://arxiv.org/abs/2404.07837v2), arXiv DOI `10.48550/arXiv.2404.07837`. 이것을 출판사 DOI로 표기하지 않는다.

[공식 저장소](https://github.com/arplaboratory/data-driven-system-identification)의 commit `2d267dd07b4262f579ee223d20b26a6dc9d17147`에서 notebook과 MIT LICENSE를 받았다. Notebook은 자료로 읽었으며 외부 셀/설치 스크립트를 실행하지 않았다. 논문 PDF는 arXiv perpetual non-exclusive license이고, notebook의 MIT license와 구별한다.

| 보존 원본 | SHA-256 |
|---|---|
| `2404.07837v2.pdf` | `3090601ee793a15a34cefef2d5eb601f4f745cb6ea072d1ade271a96fe53894e` |
| `sysid.ipynb` | `5b510d31dfef5c8ba3cd75669e85d0fe5b0b812b19731f73f150dd5f660b4b36` |
| `LICENSE` | `4725c3777c219911cdc5ad944641ce8070c71694c8c7e1f704785163da2cc874` |

## 계수 대응

- 질량 0.027 kg, rotor XY displacement 각각 0.028 m, rotor 배치/추력/반작용 방향: notebook model 정의.
- Body FLU, 양의 thrust body +z; world 중력은 -z. 출력 world는 프로젝트의 Local ENU다.
- `Ixx=1.0286343346554766e-5`, `Iyy=1.1052384506476651e-5` kg m²: notebook 보존 출력. `Izz=1.954627471337678e-5`는 식별된 독립 측정치가 아니라 `(Ixx+Iyy)/2 * 1.832`로 추론된 값이다.
- Rotor당 thrust `f=0.02126557 - 0.01117503*u + 0.12012994*u²` N: notebook 보존 출력, PDF §IV-A의 반올림 값과 일치. `u`는 dimensionless normalized command/state이며 실제 RPM 단위가 아니다. 원 notebook은 motor log를 65536으로 나눈다.
- `motor_time_constant=0.072 s`: PDF Fig.2. `torque_per_thrust=0.004548 m`: PDF Fig.7와 §IV-A의 반올림 값.
- 강체 운동과 motor first-order lag: PDF Eq.(1)–(6). 독립 ambient wind, 추가 drag, 배터리·지면·회전익 진동 모델은 도입하지 않는다. 저속의 논문 모델 재현이며 전체 비행영역 정확도를 주장하지 않는다.

## 반드시 드러낼 한계

1. 공식 notebook의 reader는 acc.x/y에 9.81, acc.z에 **9.18**을 곱한다. 논문 중력 설명의 9.81과 불일치한다. 원본을 조용히 수정하거나 저장된 계수를 재식별 결과로 부르지 않는다. 현재 채택 의미는 **공개된 모델/계수의 재현**이며 이 단위 불일치를 해소한 독립 식별이 아니다.
2. 상수항이 있는 thrust fit은 `u=0`에서도 추력을 내므로 motor-off/추락/착륙 모델로 쓰지 않는다. 공개 논문 역시 낮은 throttle 자료가 부족하다고 설명한다. 진단의 동작 구간과 controller 제한은 실행 설정에 명시한다.
3. Controller gains·이동 명령은 이 프로젝트가 설계한 simulation 실험 조건이지 제조사 한계나 실측 기동 빈도가 아니다. 그래프와 수치 테스트 통과는 실비행 재검증이 아니다.

원본은 변경하지 않는다. 후속 raw-log 재식별이 필요하면 별도 식별 설정·결과·hash로 기록하고 현재 published snapshot과 구분한다.
