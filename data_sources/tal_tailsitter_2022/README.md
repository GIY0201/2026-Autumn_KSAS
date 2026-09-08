# Tal–Karaman tailsitter motion reference

Acquired: 2026-09-07. Original: `2207.13218v1.pdf`, SHA-256 `3d485b0125c90d1ac9c0dd8b2f894c0e1753fbdcae473326eebf5ba466236067`.

Ezra Tal and Sertac Karaman (2022), *Global Incremental Flight Control for Agile Maneuvering of a Tailsitter Flying Wing*. [arXiv v1](https://arxiv.org/abs/2207.13218v1), [original PDF](https://arxiv.org/pdf/2207.13218v1), DOI [10.2514/1.G006645](https://doi.org/10.2514/1.G006645). arXiv non-exclusive distribution license is not a general open-source license; the local original is retained for research reference, not relicensed.

The repository stores the pinned PDF unchanged so a clean clone can reproduce every
generation preset. The verified downloader remains available for recovery or an
independent checksum check:

```powershell
uv run python data_sources/tal_tailsitter_2022/fetch_source.py
```

The downloader accepts only the pinned arXiv v1 URL and installs the file only
after its SHA-256 matches the value above. Use `--check` to validate an existing
local copy without network access.

## Permitted use

The v1 reduced generator references the reported motion categories, not the internal aircraft or controller. Table IV (PDF p.13) includes 8.3 m/s maximum measured speed for transition from hover. Section VI-E (PDF p.15) describes 8 m/s target, 3 s transitions, 2.7 m/s² tangential acceleration and a 3.5 m circular reference. These are a particular experiment, not universal speed/acceleration/turn limits. Reported angular rate is not automatically track-turn rate and is not copied as that bound.

The generated 60 s trajectories are new free-space scenarios, not reconstructed indoor flight logs. Operational speed limits, turn limits, vertical motion, response times and scenario durations are explicitly `simulation_design`. No body attitude, motor, rotor, flap, GPS or camera signal is reproduced. No independent flight validation or raw-log parameter fit has been performed. See [implementation scope](../../plan/reduced_motion_v1_implementation.md).
