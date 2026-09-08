# Variable-length viewer playback evidence

Date: 2026-09-08. Scope: scenario integration Task 3. Software checks only; no flight fidelity claim. Existing UI appearance and port 8088 are preserved. No server started.

Changed paths: `visualization/v1/app.py`, `visualization/v1/figures.py`, `visualization/v1/tests/test_variable_playback.py`, `visualization/v1/README.md`, Task 3 checkboxes in `plan/scenario_generator_integration.md`, this report. Existing dirty README and generation UI tests were preserved.

## Behavior

The public Episode metadata supplies its sample count/duration. The viewer loads 2..3001 exact stored samples, including evaluation truth only when explicitly requested. XY/XZ/YZ/3D share the same integer index. A mixed-duration comparison uses the minimum selected duration and discloses the common interval. Episode/dataset selection resets playback to paused step 0 and updates max, marks and status. Export validates the requested index against selected public metadata. Dataset subtitle uses metadata duration/range.

There was no playback JavaScript asset: Dash callbacks provide browser marker payloads. HTTP tests cover switching a 60-second dataset to 600 seconds and seeking the final sample in public-only and evaluation modes. The tests compare the actual marker payload to stored CSV positions. No resampling, interpolation or endpoint repetition was added.

## RED / GREEN

- RED: `.venv/Scripts/python.exe -m pytest -q visualization/v1/tests/test_variable_playback.py --basetemp temp/pytest-playback-red-valid` → **8 failed, 2 passed in 1.90 s**. Failures were the 301-sample viewer constraint and absent variable player bound. Two 301-sample legacy cases passed. Earlier fixture construction failures were corrected before this valid RED run.
- GREEN: same focused file, `--basetemp temp/pytest-playback-green` → **10 passed in 1.95 s**.
- Added real HTTP dataset-switch/seek cases: `--basetemp temp/pytest-playback-switch-green` → **12 passed in 2.81 s**.
- Earlier complete viewer suite: `.venv/Scripts/python.exe -m pytest -q visualization/v1/tests --basetemp temp/pytest-playback-suite` → **43 passed in 44.85 s** (before two switch tests and final subtitle/stale-state guard).
- Scoped Ruff on app, figures and new tests: **All checks passed**.
- Final complete viewer suite after all changes: `.venv/Scripts/python.exe -m pytest -q visualization/v1/tests --basetemp temp/pytest-playback-final` → **45 passed in 46.82 s**.

No new Browser session was opened; actual interactive Browser verification and project-wide integration results belong to the parent task. Existing viewer PNG tests ran in the complete viewer suite. No scientific generalization or whole-project completion is inferred from these checks.

## Generated representative datasets

Read the parent task's completed outputs with `load_viewer_dataset`, built the final `build_current_marker_extensions` payload, and asserted exact equality of final 3D truth z and final XY observed x against their stored rows. All three passed:

- `outputs/data_generation/v1/75b57f3e-8fc0-4aa0-a566-cc11345555dc`: 2174 samples.
- `outputs/data_generation/v1/7c4e9c4f-d6cf-4a8b-b957-6b323f3c34cc`: 2214 samples.
- `outputs/data_generation/v1/0f47f8c2-3e3c-4ee2-8e38-0287632b2f9e`: 2514 samples.

Final scoped static check: `.venv/Scripts/ruff.exe check visualization/v1` → **All checks passed**.
