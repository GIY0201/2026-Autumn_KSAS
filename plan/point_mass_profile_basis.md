# Point-Mass Stage 1 profile basis

Date: 2026-09-07  
Status: implementation input basis; it is not a flight-limit, system-identification, or field-validation record.

## Purpose and boundary

This table makes every Stage 1 point-mass profile value traceable before a runnable
profile is registered.  It keeps four roles distinct:

- `source reference`: a value or artifact directly retained from a supplied source/config.
- `legacy simulation design`: an existing v1 implementation setting, not a measured
  aircraft capability.
- `derived`: a displayed calculation from a named input and formula.
- `new simulation design`: a bounded generator setting chosen for this Stage 1
  diagnostic; it is neither fitted nor certified.

The profiles do not use `mocap02` or `mocap03` to choose a value.  Those files remain
comparison-only under the existing Bitcraze reference contract.  The X8 public
TRAIN/VALIDATION logs are not fitted or tuned here.  In particular, the legacy X8
working range is not promoted to a physical performance claim.

## Baseline preservation record

Before Stage 1 code changes, the branch had exactly these user-provided untracked
planning files: `plan/point_mass_migration_design.md` and
`plan/point_mass_stage1_plan.md`.  The command below completed successfully.

```powershell
.\.venv\Scripts\python.exe -m pytest -q contracts/v1/tests data_generation/v1/tests visualization/v1/tests --basetemp temp/pytest-point-mass-baseline
# 167 passed in 102.14s
```

The following legacy implementation SHA-256 values are the preservation comparison
set.  A later Stage 1 verification must show that these files remain byte-identical.

| File | SHA-256 |
|---|---|
| `data_generation/v1/x8.py` | `82BC1D5758D8967892204B95030D4F774C26F5677626F5E876D8750C01448BA0` |
| `data_generation/v1/control.py` | `B9AAE315C1C8771D6E4842F139CCF9EFABD493FF121531A9A94753AFBB7271F2` |
| `data_generation/v1/scenario.py` | `F4A79B8729D1F28F772511C753B01BA6EF2567C57A6752CBDB55BCD2756D85FD` |
| `data_generation/v1/quadrotor.py` | `4FECB6D4BFA37ECB837FD2CB97F3EF0F2A67C3F79B02A7A853404449E4D1AD2E` |
| `data_generation/v1/quadrotor_scenario.py` | `5863EAD2D99939FE5580DB80F4A36041AAD931AAA1729B150FE4DBFC0C92F716` |
| `data_generation/v1/bounded_motion.py` | `1518897AB2E45BA3AC06044649374F36908AE142D03A167E1EB20D560FBB0C5F` |
| `data_generation/v1/bounded_motion_scenario.py` | `0356E5B48AC0317469D5D99609190AB35240AC9532B61EDDE5771142D123E468` |
| `data_generation/v1/vtol_phase_motion.py` | `F68336EB9F1A9D4C2A875AC7A408302D23536D3595E278D959A8E896FEB6837A` |

## Fixed-wing point-mass profile

The fixed-wing Stage 1 profile is a new kinematic model with model ID
`fixed_wing_point_mass_v1`.  Its source provenance remains the X8 DataverseNO
dataset, but this runner does not replay or fit the log.  The profile's
`source_evidence` cites the local source README and its profile records the exact
local README hash `55C7A34F411B9549B64E611853AFD3180D13EAE1D33BF2D3E6DD4C780711BBB7`.

| Profile field / value | Role | Source or calculation | Limit on interpretation |
|---|---|---|---|
| 60 s, 5 Hz, 301 stored samples | source reference | `contracts/v1/validation.py` v1 contract | Storage contract, not a physical integration limit. |
| `horizontal_speed_min_mps = 14`, `horizontal_speed_max_mps = 22` | legacy simulation design | `scenario.py:_working_region_failure`, SHA `F4A79B...85FD` | Existing X8 diagnostic working-range check only. |
| `bank_reference_deg = 15` | legacy simulation design | `control.py:MAX_BANK_COMMAND_RAD`, SHA `B9AAE...71F2` | Controller command cap; not an airframe bank certification. |
| `horizontal_acceleration_max_mps2 = 2.62768394795467` | derived | `9.80665 * tan(15 deg)` | Coordinated-track budget convention for this new point-mass model, not a measured X8 acceleration. |
| `track_turn_rate_max_rad_s = 0.187691710568191` | derived | `2.62768394795467 / 14` | The rate that respects the above budget at the required minimum speed. |
| nominal 18 m/s, recovery 19 m/s, vertical targets `+/-0.5 m/s` | legacy simulation design | `scenario.py:_default_events`, SHA `F4A79B...85FD` | Existing event targets, not source-log fitting. |
| nominal turn magnitude `0.0960653880758149 rad/s` | derived | `9.80665 * tan(10 deg) / 18`; 10 deg is the old left/right bank target | Track turn rate, explicitly not copied from an X8 body angular-rate channel. |
| initial XY `[-500, 500] m`, Z `[80, 120] m`, heading `[-pi, pi]` | legacy simulation design | `scenario.py:run_default_x8_episode` | Synthetic initialization range only. |
| point-mass integration `0.02 s`, tangential cap `1.0 m/s^2`, vertical speed cap `1.0 m/s`, vertical acceleration cap `0.5 m/s^2`, path-angle cap `20 deg` | new simulation design | Explicit profile values; `0.02 s` divides the 0.2 s storage grid | Not mapped to body pitch, actuator response, or a flight envelope. |
| command order and finite dwell times | mixed: legacy target sequence + new simulation design | Existing target order is retained; duration is explicit configuration rather than a hidden controller trigger | No Stage 2 ground, 600 s, smooth-entry/exit, or mission logic is encoded. |

For the two geometric references used in the config, the calculation is retained here
so a later profile edit cannot silently reinterpret it:

```text
straight 100 m at 18 m/s = 5.55555555555556 s
30 deg turn at 0.0960653880758149 rad/s = 5.45044147622736 s
```

## Quadrotor point-mass profile

The quadrotor Stage 1 profile is a new kinematic model with model ID
`quadrotor_point_mass_v1`, separate from the preserved
`crazyflie_eschmann_2024` 6-DOF profile.  It uses only the existing
`mocap00`/`mocap01` calibration/reference envelope.  The reference-config SHA-256 is
`3D7A6583E3B090A43A2106EB92DCD70E59D53D784E3EDA8CC5186A93E3723DF6`.

| Profile field / value | Role | Source or calculation | Limit on interpretation |
|---|---|---|---|
| 60 s, 5 Hz, 301 stored samples | source reference | `contracts/v1/validation.py` v1 contract | Storage contract, not flight dynamics. |
| `integration_dt_s = 0.02` | source reference | `bitcraze_positioning.yaml:analysis.resample_dt_s` | A reference-analysis grid reused as a kinematic integration grid. |
| nominal speed set `{0.25, 0.5} m/s` | source reference | `bitcraze_positioning.yaml:planner.nominal_mean_speeds_mps` | Calibration/reference motion scale only. |
| max speed `0.75 m/s`, horizontal/tangential acceleration cap `1.0 m/s^2` | source reference | `bitcraze_positioning.yaml:reference_envelope` | 95th-percentile reference envelope, not a Crazyflie aircraft limit. |
| initial/command Z bounds `[0.25, 1.25] m` | source reference | `bitcraze_positioning.yaml:planner.target_bounds_enu_m` | Indoor reference envelope only. |
| zero minimum horizontal speed | new simulation design | Hover-capable point-mass object category | Does not reintroduce motor, thrust, or attitude dynamics. |
| max vertical speed `0.4 m/s`, vertical acceleration cap `1.0 m/s^2`, max track turn rate `1.0 rad/s` | new simulation design | Explicit profile values | Bounded diagnostic settings, not source-fit response coefficients. |
| seeded initial heading and command dwell/order | new simulation design | Explicit profile values | No raw-control replay and no use of held-out `mocap02`/`mocap03`. |

## Shared kernel conventions

- State is Local ENU position, horizontal speed, track heading, vertical speed, and
  track turn rate.  Track heading is never named or serialized as body yaw.
- The kernel uses `(s cos(chi), s sin(chi), w)` for velocity.  Its horizontal
  tangential and normal components share one acceleration budget; vertical
  acceleration is checked independently.
- A target whose speed, path angle, or turn demand is infeasible is rejected before
  position integration.  There is no 6-DOF fallback, forced endpoint, or hidden
  clipping to make a configuration appear valid.
- The final command extends only to the 60 s observation-window boundary and is
  recorded as `episode_window_cut_off`; completed finite commands remain separately
  auditable in `evaluation/commands.csv`.

## Exclusions retained for this stage

This basis does not add the Stage 2 600 s/ground/takeoff/landing/100 m/3 deg/10 deg
rules, a chosen-maneuver smooth-entry/exit policy, or the Stage 3 inspector.  It also
does not claim that either constrained point-mass result reproduces a source flight
or validates real-world safety.

## Approved later calibration direction (not Stage 1 evidence)

The user has approved a later, separate calibration investigation that may extract
response summaries from the preserved X8 6-DOF implementation for a point-mass
profile update.  No Stage 1 value above is currently supported by an unrun 6-DOF
condition sweep, and no such sweep is treated as completed evidence here.  Until a
separate fixed protocol, inputs, outputs, and results are recorded, these values stay
in their stated `legacy simulation design`, `derived`, or `new simulation design`
roles.  A possible future PX4 reference is likewise not a Stage 1 dependency or
installed component.
