# Skywalker X8 source — DataverseNO V1

- Dataset: Løw-Hansen, Bogdan; Hann, Richard (2024), *System identification campaign - Skywalker X8 UAV*, DataverseNO, V1.
- Dataset DOI: `10.18710/U4TLYV`.
- Source page: <https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/U4TLYV>.
- License: CC0 1.0, as recorded by the source repository.
- Retrieved: 2026-09-07.
- `00_README.txt`: source-original file, repository MD5 `4bc6c339fe46ad2ecac8d2d17d5eaa19` verified locally.
- `raw/training/`: 13 source-original headerless 41-column CSV logs.
- `raw/validation/`: 4 source-original headerless 41-column CSV logs. They are preserved for a later fixed-configuration evaluation and are not used for tuning.

The raw logs are preserved unmodified. `data_generation/v1/source.py` verifies all 17 repository MD5 values before source motion checking. The reader retains motion-related fields but deliberately ignores the source GPS columns; no geographic-coordinate conversion is part of X8-GEN-V1.

The source README states that records were manually synchronized and resampled to 40 Hz; the external IMU was transformed to the body frame at the CG, and estimated vertical wind was included. These are source-processing facts, not claims that the records are unfiltered ground truth or calm-air trajectories.
