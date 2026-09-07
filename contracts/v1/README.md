# Contracts v1

상태: **X8-GEN-V1 strict CSV contract와 reader 구현·검증 완료**.

[상위 지침](../AGENTS.md), [v1 지침](AGENTS.md), [생성 설계 및 구현 기록](../../plan/data_generation_v1_design.md)을 먼저 읽습니다. 이 폴더는 물리 모델이나 viewer UI를 구현하지 않고, producer/consumer가 공유하는 파일 경계·단위·시간축만 정의합니다.

## 고정 의미

- Local ENU: `x=East`, `y=North`, `z=Up`; 위치 단위 m, 시간 단위 s.
- 60초, 5 Hz, step `0..300`, 301 samples; `t_s = step × 0.2`.
- public 위치 관측은 `sigma_1m`, `sigma_3m`, `sigma_5m` 세 variant를 모두 보유합니다.
- GPS/위경도·지도 좌표계는 contract에 포함하지 않습니다.

## 파일 경계

`validation.py`는 public directory에 아래 두 파일만 존재하도록 거부 규칙을 적용합니다.

```text
public/episodes.csv
public/observations.csv
```

public observation 열은 아래 순서로 고정됩니다.

```text
episode_id,variant_id,step,t_s,x_m,y_m,z_m,
sigma_x_m,sigma_y_m,sigma_z_m,valid
```

evaluation truth는 자동 탐색하지 않으며 호출자가 명시한 `evaluation/truth.csv`에서만 읽습니다. truth 열은 위치·속도·가속도를 포함하지만 `PublicDataset` type에는 truth·command field가 없습니다.

## 사용

```python
from pathlib import Path

from contracts.v1.csv_io import read_evaluation_truth, read_public_dataset

dataset_path = Path("outputs/data_generation/v1/<dataset_id>")
public = read_public_dataset(dataset_path / "public")
truth = read_evaluation_truth(dataset_path / "evaluation" / "truth.csv")
```

미지원 variant, row 누락/중복, 비유한값, 잘못된 sigma/time, public의 extra file은 `ContractError`로 명확히 거부합니다. 다른 버전의 internal module을 직접 import하지 말고 버전이 명시된 이 reader를 사용합니다.

`tests/test_contracts.py`의 4개 contract test를 포함해 최신 전체 suite는 35 passed입니다.
