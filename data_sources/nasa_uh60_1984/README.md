# NASA UH-60 motion reference

Acquired into the project: 2026-09-07 by copying the previously inspected official original without modification. File: `19840015585.pdf`. SHA-256 `2bc58d43a399c4ef09c96eb704f01d384c43f70583d586826368358fcf88fc0c`.

Kathryn B. Hilbert (1984), *A Mathematical Model of the UH-60 Helicopter*, NASA TM-85890. [Official NTRS record](https://ntrs.nasa.gov/citations/19840015585), [original PDF](https://ntrs.nasa.gov/api/citations/19840015585/downloads/19840015585.pdf). U.S. Government report; prior NTRS inspection recorded public use permitted. No new license is assigned to the original. The current web fetch returned 403; the existing original's hash and Table 4 were rechecked locally.

## Permitted use

Table 4, PDF p.21 / printed p.15, reports steady level-flight trim at equivalent airspeeds 1, 20, 40, 60, 100 and 140 kt. These are published model-trim reference points from near-hover through forward flight, not raw position-time observations, certified speed limits, or transient turn/climb data. Equivalent airspeed is not treated as measured ENU ground speed.

The v1 reduced generator uses this as a conventional-helicopter motion reference. All chosen ENU speed/acceleration/turn/vertical/response/duration settings are explicitly `simulation_design`, not measured NASA coefficients. The generator does not reproduce the UH-60 rotor, stabilator, engine, body attitude or flight controller. It can generate new helicopter-like movement for simulation but does not claim raw-log fitting, NASA model reproduction, or independent flight validation. See [implementation scope](../../plan/reduced_motion_v1_implementation.md).
