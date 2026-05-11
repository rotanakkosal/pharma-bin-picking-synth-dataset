#!/usr/bin/env python3
"""
Dataset QC for pharma-bin-picking-synth-dataset.

Runs annotation-quality + coverage checks across output/scene_*/ and prints a
one-page report. Does NOT require blenderproc — plain numpy + Pillow + json.

Usage:
    python scripts/eval/dataset_qc.py
    python scripts/eval/dataset_qc.py --output-dir output --depth-range 800 1500
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

# Centralized depth-unit handling — see scripts/depth_io.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from depth_io import load_depth_m


CLASS_RE = re.compile(r"^(.*?)_\d+$")


def load_scene(scene_dir: Path) -> dict | None:
    gt_path = scene_dir / "scene_gt.json"
    if not gt_path.exists():
        return None
    with gt_path.open() as f:
        return json.load(f)


def load_mask(scene_dir: Path, rel: str) -> np.ndarray:
    return np.asarray(Image.open(scene_dir / rel)) > 0


def check_scene(scene_dir: Path, gt: dict, depth_range_mm: tuple[int, int]) -> dict:
    """Per-scene checks; returns dict of counters."""
    r = defaultdict(int)
    r["n_instances"] = len(gt["instances"])
    r["category_id_minus1"] = sum(1 for i in gt["instances"] if i.get("category_id", -1) == -1)

    # Depth — load via centralized helper that honors depth_unit_m from
    # scene_gt.json (BOP convention; falls back to 0.001 m for legacy v1).
    # We then convert to mm for the legacy depth_range_mm comparison.
    depth_m = load_depth_m(scene_dir)
    depth_mm = depth_m * 1000.0
    r["depth_median_mm_all"] = int(np.median(depth_mm[depth_mm > 0])) if (depth_mm > 0).any() else 0

    occlusion_rates = []
    for inst in gt["instances"]:
        vis = load_mask(scene_dir, inst["visible_mask"])
        amo = load_mask(scene_dir, inst["amodal_mask"])
        occ = load_mask(scene_dir, inst["occlusion_mask"])

        # (1) visible ⊆ amodal
        if not np.all(amo[vis]):
            r["mask_containment_violations"] += 1

        # (2) occlusion_mask == amodal & ~visible
        expected_occ = amo & ~vis
        if not np.array_equal(occ, expected_occ):
            r["occlusion_mask_mismatch"] += 1

        # (3) pixel-count consistency
        if int(vis.sum()) != inst["visible_px"]:
            r["visible_px_mismatch"] += 1
        if int(amo.sum()) != inst["amodal_px"]:
            r["amodal_px_mismatch"] += 1

        # (4) occlusion_rate sanity
        amo_px = inst["amodal_px"]
        vis_px = inst["visible_px"]
        expected_rate = 0.0 if amo_px == 0 else (amo_px - vis_px) / amo_px
        if abs(expected_rate - inst["occlusion_rate"]) > 1e-3:
            r["occlusion_rate_mismatch"] += 1
        occlusion_rates.append(inst["occlusion_rate"])

        # (5) depth inside visible mask in plausible range (mm)
        if vis.any():
            z_med = float(np.median(depth_mm[vis]))
            if not (depth_range_mm[0] <= z_med <= depth_range_mm[1]):
                r["depth_out_of_range"] += 1

    r["_occlusion_rates"] = occlusion_rates  # will be popped before printing
    r["_class_names"] = [i["class_name"] for i in gt["instances"]]

    # --- Suction-point GT checks (added 2026-05-04, V1) -----------------------
    suction_meta = gt.get("suction_meta")
    if suction_meta is None:
        r["suction_meta_missing"] = 1
    else:
        for need in ("version", "cup_radius_mm", "mu_default", "tau_seal", "tau_wrench"):
            if need not in suction_meta:
                r["suction_meta_field_missing"] += 1

    s_combined_all: list[float] = []
    sseal_all: list[float] = []
    swrench_all: list[float] = []
    n_with_points = 0

    for inst in gt["instances"]:
        sp = inst.get("suction_points")
        if sp is None:
            r["suction_points_field_missing"] += 1
            continue
        if len(sp) > 0:
            n_with_points += 1

        # Sortedness: descending S_combined_default
        prev = float("inf")
        for p in sp:
            cur = p["S_combined_default"]
            if cur > prev + 1e-6:
                r["suction_points_unsorted"] += 1
                break
            prev = cur

        for p in sp:
            for k in ("Sseal", "Swrench_default", "S_combined_default"):
                if not (0.0 <= p[k] <= 1.0):
                    r["suction_score_out_of_range"] += 1
            if p["lateral_force_N"] < 0 or p["normal_force_N"] < 0:
                r["suction_negative_force"] += 1
            if p["torque_arm_mm"] < 0:
                r["suction_negative_torque_arm"] += 1
            n = p["normal_cam"]
            if abs(n[0] ** 2 + n[1] ** 2 + n[2] ** 2 - 1.0) > 1e-3:
                r["suction_normal_not_unit"] += 1
            sseal_all.append(p["Sseal"])
            swrench_all.append(p["Swrench_default"])
            s_combined_all.append(p["S_combined_default"])

    r["_suction_n_total"] = len(s_combined_all)
    r["_suction_n_with_points"] = n_with_points
    r["_suction_sseal"] = sseal_all
    r["_suction_swrench"] = swrench_all
    r["_suction_combined"] = s_combined_all

    # --- 3D pose checks (added 2026-05-05; see docs/pose_export/pose_export_design.md) -----
    n_pose_present = 0
    t_norms: list[float] = []
    for inst in gt["instances"]:
        pose = inst.get("pose_cam")
        if pose is None:
            r["pose_cam_field_missing"] += 1
            continue
        n_pose_present += 1
        for need in ("R", "t", "object_up_axis", "object_frame_unit", "bbox_3d_mm"):
            if need not in pose:
                r["pose_field_missing"] += 1
        R = np.array(pose["R"])
        t = np.array(pose["t"])
        if R.shape != (3, 3):
            r["pose_R_wrong_shape"] += 1
            continue
        if np.linalg.norm(R @ R.T - np.eye(3)) > 1e-3:
            r["pose_R_not_orthogonal"] += 1
        if abs(np.linalg.det(R) - 1.0) > 1e-3:
            r["pose_R_det_not_one"] += 1
        if np.linalg.norm(t) > 5.0 or t[2] <= 0:
            # t[2] must be positive in OpenCV convention (camera looks +Z)
            r["pose_t_implausible"] += 1
        t_norms.append(float(np.linalg.norm(t)))
    r["_pose_n_present"] = n_pose_present
    r["_pose_t_norms"] = t_norms
    return r


def bucket_occlusions(rates: list[float]) -> dict[str, int]:
    edges = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.8), (0.8, 1.01)]
    labels = ["0-10%", "10-30%", "30-50%", "50-80%", "80-100%"]
    out = dict.fromkeys(labels, 0)
    for r in rates:
        for (lo, hi), lbl in zip(edges, labels):
            if lo <= r < hi:
                out[lbl] += 1
                break
    return out


def class_from_name(name: str) -> str:
    m = CLASS_RE.match(name)
    return m.group(1) if m else name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    ap.add_argument("--depth-range", type=int, nargs=2, default=(800, 1500), metavar=("MIN", "MAX"),
                    help="plausible median depth per visible instance in mm")
    args = ap.parse_args()

    root = args.output_dir.resolve()
    scene_dirs = sorted(p for p in root.glob("scene_*") if p.is_dir())
    if not scene_dirs:
        print(f"no scenes found in {root}")
        return

    totals = Counter()
    all_occlusion_rates: list[float] = []
    all_class_names: list[str] = []
    per_scene_instance_counts: list[int] = []
    scene_reports = []

    for sd in scene_dirs:
        gt = load_scene(sd)
        if gt is None:
            totals["missing_scene_gt"] += 1
            continue
        r = check_scene(sd, gt, tuple(args.depth_range))
        scene_reports.append((sd.name, r))
        per_scene_instance_counts.append(r["n_instances"])
        all_occlusion_rates.extend(r.pop("_occlusion_rates"))
        all_class_names.extend(r.pop("_class_names"))
        for k, v in r.items():
            # Underscore-prefixed keys carry per-scene non-int payloads
            # (lists, etc.) that aggregate later. Skip them here.
            if k.startswith("_") or not isinstance(v, int):
                continue
            totals[k] += v

    n_scenes = len(scene_reports)
    n_inst = sum(per_scene_instance_counts)

    print("=" * 64)
    print(f"Dataset QC Report — {root}")
    print("=" * 64)
    print(f"scenes found          : {n_scenes}")
    print(f"total instances       : {n_inst}")
    if n_scenes:
        print(f"instances/scene       : min={min(per_scene_instance_counts)} "
              f"median={int(np.median(per_scene_instance_counts))} "
              f"max={max(per_scene_instance_counts)}")
    print()
    print("--- Annotation integrity (want all zeros) ---")
    for k in ("mask_containment_violations", "occlusion_mask_mismatch",
              "visible_px_mismatch", "amodal_px_mismatch",
              "occlusion_rate_mismatch", "depth_out_of_range"):
        print(f"  {k:<34} {totals[k]}")
    print()
    print("--- Known bugs ---")
    frac = totals["category_id_minus1"] / max(1, n_inst)
    print(f"  category_id == -1              : {totals['category_id_minus1']}/{n_inst} ({frac:.0%})")
    print()
    print("--- Coverage / distribution ---")
    print("  occlusion-rate histogram:")
    for lbl, n in bucket_occlusions(all_occlusion_rates).items():
        bar = "#" * int(40 * n / max(1, n_inst))
        print(f"    {lbl:<10} {n:>5}  {bar}")
    print()
    print("  class balance:")
    cls_counts = Counter(class_from_name(n) for n in all_class_names)
    for cls, n in cls_counts.most_common():
        print(f"    {cls:<20} {n}")
    print()
    # --- Suction-point GT report (added 2026-05-04) ---
    print("--- Suction-point GT (V1) ---")
    suction_int_keys = (
        "suction_meta_missing", "suction_meta_field_missing",
        "suction_points_field_missing", "suction_points_unsorted",
        "suction_score_out_of_range", "suction_negative_force",
        "suction_negative_torque_arm", "suction_normal_not_unit",
    )
    print("  integrity (want all zeros):")
    for k in suction_int_keys:
        print(f"    {k:<34} {totals[k]}")

    suction_total = sum(r.get("_suction_n_total", 0) for _, r in scene_reports)
    suction_with_points = sum(r.get("_suction_n_with_points", 0) for _, r in scene_reports)
    print(f"  total suction points       : {suction_total}")
    print(f"  instances with ≥1 point    : {suction_with_points}/{n_inst}")

    sseal_pool = [v for _, r in scene_reports for v in r.get("_suction_sseal", [])]
    swr_pool   = [v for _, r in scene_reports for v in r.get("_suction_swrench", [])]
    sc_pool    = [v for _, r in scene_reports for v in r.get("_suction_combined", [])]
    if sc_pool:
        print(f"  Sseal              : min={min(sseal_pool):.3f} mean={np.mean(sseal_pool):.3f} max={max(sseal_pool):.3f}")
        print(f"  Swrench (μ=default): min={min(swr_pool):.3f} mean={np.mean(swr_pool):.3f} max={max(swr_pool):.3f}")
        print(f"  S_combined         : min={min(sc_pool):.3f} mean={np.mean(sc_pool):.3f} max={max(sc_pool):.3f}")
    print()

    # --- 3D pose report (added 2026-05-05) ---
    print("--- 3D pose (per-instance pose_cam) ---")
    pose_int_keys = (
        "pose_cam_field_missing", "pose_field_missing",
        "pose_R_wrong_shape", "pose_R_not_orthogonal",
        "pose_R_det_not_one", "pose_t_implausible",
    )
    print("  integrity (want all zeros):")
    for k in pose_int_keys:
        print(f"    {k:<34} {totals[k]}")
    pose_present = sum(r.get("_pose_n_present", 0) for _, r in scene_reports)
    print(f"  instances with pose_cam    : {pose_present}/{n_inst}")
    t_norm_pool = [v for _, r in scene_reports for v in r.get("_pose_t_norms", [])]
    if t_norm_pool:
        print(f"  ||t|| range (m)            : min={min(t_norm_pool):.3f} mean={np.mean(t_norm_pool):.3f} max={max(t_norm_pool):.3f}")
    print()

    print("--- Known limitations (not checked) ---")
    print("  - suction GT physical correctness not validated against real grasps (V3 future)")
    print("  - rendering realism (FID vs real L515 captures) not measured here")
    print("  - pose-variety / yaw-pitch-roll histograms not aggregated here (raw R/t exported)")


if __name__ == "__main__":
    main()
