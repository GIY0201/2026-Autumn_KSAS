# 식별 정보가 변하는 학습 입력

갱신일: 2026-09-08. 사용자 합의를 반영한 구현 명세다. 생성 흐름과 파일을 처음 확인할 때는 [현재 데이터 생성 방식](data_generation_current.md)을 먼저 읽는다.

## 합의한 범위

- 위치는 계속 관측한다. 객체 종류만 알려지거나 가려진다. 위치의 `valid`와 객체 종류의 `identity_known`은 별개다.
- 같은 원본 Episode와 관측오차 변형마다 `acquired`(미식별→식별), `unknown`(항상 미식별), `lost`(식별→미식별) 세 입력을 만든다.
- 반복 전환·오분류는 이번 범위에서 제외한다. 실제 카메라의 한계가 아니라 연구용 가정이다.
- 전환형은 한 번만 전환한다. 시작·종료에서 각각 3초 이상 확보한 저장 시점에서 seed로 선택한다. 두 전환형과 모든 관측오차 변형이 같은 시점을 공유한다. 기동 이벤트는 참조하지 않는다.
- 모든 파생본은 원본의 split을 유지한다. 독립 비행 수와 파생 입력 수를 구분한다.

## 파일과 입력 경계

`training/{train,valid,test}/{acquired,unknown,lost}/observations.csv`에는 기존 관측 컬럼과 `identity_known`, `observed_object_type`을 저장한다. 미식별 행은 반드시 `0,unknown`이다. 식별된 종류는 생성 요청의 object_id이며, 이를 바탕으로 운동 규칙을 선택하는 용도가 아니다.

각 패턴 폴더의 `sequences.csv`는 구간 목록이다. 기존 `training/sequences.csv`, `training/summary.csv` 및 분할별 기본 observations는 위치 전용 입력을 위해 보존한다. 식별 변형까지의 개수는 `training/identity_summary.csv`로 확인한다. 두 목록을 합쳐 중복 학습하지 않는다.

`episode_id`, `variant_id`, sequence ID, split, 폴더의 패턴 이름은 관리용이다. `identity_schedule.csv`의 미래 전환 시점, 생성 설정, 실제 객체 설명도 모델 입력이 아니다. 모델 입력은 위치·시간·관측 정보와 현재 시점의 식별 컬럼만 명시적으로 선택한다.

전체 Episode 저장을 기본으로 한다. 구간 추출 옵션을 쓰면 기존 구간에 전환을 포함하는 구간을 추가하여 유지한다. 향후 16개 context/75개 target 학습 loader에서도 전환이 context 안에 포함되는 사례를 유지해야 한다. 이 변경은 모델 학습을 실행하지 않는다.

새 결과에만 적용하며 과거 데이터는 덮어쓰지 않는다. 설정은 `training/settings.yaml`, 결과 파일 해시는 `files.csv`에 포함한다.
