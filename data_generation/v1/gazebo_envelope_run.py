"""Write immutable, evaluation-only steady-force boundary and diagnosis tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import platform
import sys
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
import scipy
from omegaconf import OmegaConf

from .gazebo_envelope import find_upper_boundary, solve_trim
from .gazebo_reference import load_catalog


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def diagnose(row, settings):
    """Explain failed original tracking criteria; do not infer actuator causes."""
    checks = (
        [("speed_mps", "speed_mps", "speed_tolerance_mps", "속력 오차"),
         ("path_angle_deg", "path_angle_deg", "angle_tolerance_deg", "상승·하강 각도 오차")]
        if row["mode"] in ("wing", "transition") else
        [("horizontal_mps", "speed_mps", "speed_tolerance_mps", "수평 속력 오차"),
         ("vz_mps", "vertical_mps", "vertical_tolerance_mps", "상승·하강 속도 오차")]
    )
    if row["mode"] == "rotor" and float(row["speed_mps"]) != 0:
        checks.append(("turn_deg_s", "turn_deg_s", "turn_tolerance_deg_s", "선회 속도 오차"))
    reasons, result = [], dict(row)
    for measured, commanded, tolerance, label in checks:
        desired = float(row[commanded])
        error = max(abs(float(row[f"achieved_{measured}_{stat}"]) - desired) for stat in ("min", "max"))
        result[f"{measured}_worst_error"] = error
        if error > settings[tolerance]:
            reasons.append(label)
    if row["status"] != "complete":
        reasons.append("실행 중단: " + row["failure_reason"])
    if str(row["terminal_window_complete"]).lower() != "true":
        reasons.append("마지막 유지 구간 미완료")
    original_met = str(row["target_met"]).lower() == "true"
    if not original_met and not reasons:
        reasons.append("저장 요약만으로 원인 미확정: bank 또는 원본 시점 확인 필요")
    assert not (original_met and reasons), "diagnosis contradicts original criteria"
    result["unmet_reasons"] = "; ".join(reasons)
    return result


def run(config_path, output_root):
    config_path = Path(config_path).resolve()
    options = OmegaConf.to_container(OmegaConf.load(config_path), resolve=True)
    if options["schema_version"] != "gazebo-steady-envelope-v1":
        raise ValueError("unsupported envelope schema")
    reference_path = config_path.parent / options["reference_config"]
    models, config = load_catalog(reference_path)
    project = Path(__file__).resolve().parents[2]
    inputs = [config_path, reference_path, Path(__file__).resolve(), Path(__file__).with_name("gazebo_envelope.py"),
              Path(__file__).with_name("gazebo_reference.py"), Path(__file__).with_name("gazebo_trials.py"),
              project / "pyproject.toml", project / "uv.lock", project / options["prior_conditions"]]
    hashes = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs]
    identifier = str(uuid.uuid4())
    root = Path(output_root).resolve() / identifier
    evaluation = root / "evaluation"
    evaluation.mkdir(parents=True, exist_ok=False)
    write_csv(root / "inputs.csv", hashes)
    (root / "configuration.json").write_text(json.dumps(dict(search=options, reference=config), ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "environment.json").write_text(json.dumps(dict(python=sys.version, platform=platform.platform(), numpy=np.__version__, scipy=scipy.__version__), indent=2), encoding="utf-8")
    print(f"OUTPUT {root}", flush=True)
    cases = [(model, "wing", angle, bank) for model, angle, bank in itertools.product(options["wing_models"], options["wing_path_angles_deg"], options["wing_banks_deg"])]
    cases += [(model, "rotor", vertical, turn) for model, vertical, turn in itertools.product(options["rotor_models"], options["rotor_vertical_speeds_mps"], options["rotor_turns_deg_s"])]
    ceiling, step = options["speed_search_ceiling_mps"], options["speed_scan_step_mps"]
    if ceiling <= 0 or step <= 0 or step > ceiling:
        raise ValueError("invalid scan range")
    speeds = np.unique(np.append(np.arange(0, ceiling, step), ceiling)).tolist()
    boundaries, probes = [], []
    for index, (model_id, mode, vertical, turn) in enumerate(cases, 1):
        identity = dict(case_id=f"trim_{index:04d}", model_id=model_id, mode=mode,
                        path_angle_deg=vertical if mode == "wing" else 0,
                        vertical_mps=vertical if mode == "rotor" else 0,
                        bank_deg=turn if mode == "wing" else 0,
                        requested_turn_deg_s=turn if mode == "rotor" else 0)
        def probe(speed):
            return solve_trim(models[model_id], mode, speed, vertical, turn, config, options["force_residual_tolerance_mps2"])
        boundary, samples = find_upper_boundary(probe, speeds, options["boundary_precision_mps"])
        row = dict(**identity, **boundary)
        if boundary["feasible_speed_mps"] is not None:
            low = next(p for p in samples if p["speed_mps"] == boundary["feasible_speed_mps"])
            row.update({"limit_" + k: v for k, v in low.items() if k not in ("speed_mps", "feasible")})
        if boundary["infeasible_speed_mps"] is not None:
            high = next(p for p in samples if p["speed_mps"] == boundary["infeasible_speed_mps"])
            row["beyond_active_bounds"] = high["active_bounds"]
            row["beyond_residual_mps2"] = high["force_residual_mps2"]
        row["interpretation"] = "conditional_force_trim_only_not_dynamic_or_real_aircraft_maximum"
        if model_id == "helicopter":
            row["interpretation"] += ";fixed_trial_rotor_rpm;no_fuselage_drag_or_power_limit;not_an_operational_speed_limit"
        boundaries.append(row)
        probes.extend(dict(**identity, **p) for p in samples)
        print(f"{index}/{len(cases)} {model_id} {mode} vertical={vertical} turn={turn} {boundary}", flush=True)
    write_csv(evaluation / "boundaries.csv", boundaries)
    write_csv(evaluation / "trim_probes.csv", probes)
    with inputs[-1].open(encoding="utf-8-sig", newline="") as stream:
        diagnoses = [diagnose(row, config["settings"]) for row in csv.DictReader(stream)]
    write_csv(evaluation / "prior_tracking_diagnosis.csv", diagnoses)
    counts = Counter(reason for row in diagnoses for reason in row["unmet_reasons"].split("; ") if reason)
    write_csv(evaluation / "diagnosis_counts.csv", [dict(reason=k, cases=v) for k, v in counts.items()])
    report = ["# Gazebo 유래 함수: 정상 힘 균형의 속력 경계", "",
              "기존 생성기·제어기는 수정하지 않았다. 기존 제어기의 명령 추종 여부와 별도로, 동일한 힘 함수에서 가속/감속 없이 고도·경로각·선회를 유지하는 힘 균형을 탐색했다.", "",
              "**실제 기체 최고 속력이나 전체 10분 시나리오 검증이 아니다.** 고정익은 총속력, 회전익 모드는 수평 속력이다. 30도 기울기/bank와 받음각 90% 제한은 기존 실험 설정이며 Gazebo가 보증한 성능 한계가 아니다.", "",
              f"0~{ceiling:g} m/s를 {step:g} m/s 간격으로 전부 검사한 뒤 가장 높은 통과 구간을 {options['boundary_precision_mps']:g} m/s 이내로 좁혔다. 상한까지 통과하면 미확정으로 남긴다. 좁은 불연속 통과 구간, 최적화의 다른 해, 동적 안정성은 이 방법으로 보장하지 않는다.", "",
              "## 직선 비행", "", "| 객체 / 모드 | 경로각 또는 수직속도 | 마지막 균형점 m/s | 다음 불균형점 m/s | 다음 점 제한 |", "|---|---:|---:|---:|---|"]
    for row in boundaries:
        if row["bank_deg"] or row["requested_turn_deg_s"]:
            continue
        def display(value):
            return "미확정" if value is None else f"{value:.3f}"
        vertical = f"{row['path_angle_deg']}°" if row["mode"] == "wing" else f"{row['vertical_mps']} m/s"
        report.append(f"| {row['model_id']} / {row['mode']} | {vertical} | {display(row['feasible_speed_mps'])} | {display(row['infeasible_speed_mps'])} | {row.get('beyond_active_bounds', '')} |")
    report += ["", "## 해석상 제외", "",
               "- 헬기는 동체 항력·엔진 출력/토크 제한·유도 유입·flapping이 없고 rotor 240 rad/s도 실험 설정이다. 여기서 나온 높은 속력이나 해를 찾지 못한 수치를 헬기의 운용 최고 속력으로 쓰면 안 된다.",
               "- 고정익 bank는 강제 지정 자세이며 회전 모멘트 균형은 검사하지 않는다. 선회율·반경은 옆방향 힘에서 산출한다.",
               "- 전환은 10초 동안 상태가 바뀌는 동작이므로 정상 힘 균형 최고 속력을 정의하지 않았다. VTOL wing/rotor 모드 결과를 전환 가능 속력으로 그대로 쓰지 않는다.",
               "- 최소속력, 수직속도 자체의 최댓값, 최대 선회율 전체를 확정한 표가 아니라 각 지정 기동에서의 속력 상단 탐색이다.", "",
               "## 이전 368개 시험의 미충족 이유", "", "미충족은 요구한 움직임과 실제 결과의 차이가 기존 허용 오차보다 컸다는 뜻이다. 원인별 개수는 중복될 수 있다. 이는 엔진/모터 한계의 직접 증거가 아니다.", ""]
    report += [f"- {reason}: {count}건" for reason, count in counts.items()]
    report += ["", "전체 기동별 경계: `evaluation/boundaries.csv`", "모든 탐색점: `evaluation/trim_probes.csv`", "이전 시험별 수치/실패 이유: `evaluation/prior_tracking_diagnosis.csv`", ""]
    (root / "report.md").write_text("\n".join(report), encoding="utf-8")
    unchanged = all(hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"] for item in hashes)
    write_csv(root / "manifest.csv", [dict(run_id=identifier, code_version="v1", schema_version=options["schema_version"],
              status="complete" if unchanged else "failed_source_changed", evaluation_only=True, training_selectable=False,
              boundary_cases=len(boundaries), probe_count=len(probes), seed="not_applicable_deterministic",
              configuration_path="configuration.json", environment_path="environment.json", input_hashes_path="inputs.csv",
              code_hash=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
              limits_status="conditional_numerical_trim_not_global_or_operational_limits")])
    files = [dict(path=str(p.relative_to(root)), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()]
    write_csv(root / "files.csv", files)
    assert unchanged, "inputs changed during evaluation"
    print(f"COMPLETE {root}: {len(boundaries)} boundaries, {len(probes)} probes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/data_generation/v1"))
    args = parser.parse_args()
    run(args.config, args.output_root)
