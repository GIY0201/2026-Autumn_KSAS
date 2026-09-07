# Bitcraze positioning dataset source record

## Provenance

- Upstream repository: <https://github.com/bitcraze/positioning_dataset>
- Pinned commit: `275865f169ace04221daf7e7630b98d97fde41bd`
- Local subset: `data/lh2_kalman_flight/mocap00.npy` through `mocap03.npy`
- Hardware context stated by the upstream materials: Crazyflie 2.1 with active marker,
  Lighthouse, and uSD decks. This is distinct from the exact 27 g published
  Eschmann Crazyflie system-identification setup used for the v1 physical constants.

The four local arrays are preserved upstream materials. Their SHA-256 values, source
URLs, pattern labels, and fixed analysis split are recorded in
`data_generation/v1/configs/motion_reference/bitcraze_positioning.yaml` and verified
before analysis or source-grounded planning.

## Array meaning and processing boundary

The acquired `mocap00.npy`–`mocap03.npy` arrays are `float64` `N x 4` arrays with:

| Column | Meaning | Unit |
|---|---|---|
| 0 | timestamp | ms |
| 1–3 | active-marker centroid x/y/z position | m |

The reader only accepts numeric `N x 4` or documented `N x 16` arrays with
`numpy.load(..., allow_pickle=False)`; for `N x 16`, only the first four columns have
the mapping above. It rejects non-finite or non-increasing timestamps. It splits,
rather than bridges, every invalid XYZ run and finite time gap above the configured
threshold. Source time and source coordinate orientation are kept distinct from the
synthetic Local ENU episode origin.

## Fixed split and permitted use

- Calibration only: `mocap00` (sweep, nominal 0.25 m/s) and `mocap01` (sweep,
  nominal 0.5 m/s).
- Held-out comparison only: `mocap02` (random, nominal 0.25 m/s) and `mocap03`
  (random, nominal 0.5 m/s).

Calibration tracks characterize a conservative low-speed reference geometry and
motion-scale envelope. Held-out tracks must not choose planner geometry, thresholds,
or gains. No track is joined, copied, or replayed as a synthetic 60-second episode;
the generator uses new seeded targets and the published physical dynamics.

`collect_data.py` uses `PositionHlCommander.go_to` with duration implied by distance
and nominal velocity. Its present code geometry differs from some older scenario text
in the upstream README, so analysis reports measured valid geometry instead of treating
either command default as a universal flight envelope. Hardware scripts are never run
by this project.

## License and limitations

`collect_data.py` carries a GPL-2.0-or-later header and `qtm_thread.py` carries a
GPL-3.0 header. The upstream repository does not state a blanket license for the data
arrays. These local materials remain research reference data; this record does not
relicense scripts or arrays.

Analysis of marker-centroid trajectories does not supply raw control logs, does not
re-identify mass/inertia/thrust/motor-delay parameters, and does not independently
validate the Eschmann model in real flight. It characterizes motion scale and reference
tracking context only.
