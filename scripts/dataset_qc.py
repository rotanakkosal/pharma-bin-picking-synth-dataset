#!/usr/bin/env python3
"""
Dataset QC for pharma-bin-picking-synth-dataset.

Runs annotation-quality + coverage checks across output/scene_*/ and prints a
one-page report. Does NOT require blenderproc — plain numpy + Pillow + json.

Usage:
    python scripts/dataset_qc.py
    python scripts/dataset_qc.py --output-dir output --depth-range 800 1500
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image


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

    # Depth — PIL reads 16-bit PNG as int32 in "I" mode on some versions, even
    # though we save it as uint16. Both are fine; values are mm regardless.
    depth_path = scene_dir / gt["depth"]
    depth = np.asarray(Image.open(depth_path))
    assert depth.dtype in (np.uint16, np.int32), f"depth dtype {depth.dtype}, expected uint16/int32"
    r["depth_median_mm_all"] = int(np.median(depth[depth > 0])) if (depth > 0).any() else 0

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

        # (5) depth inside visible mask in plausible range
        if vis.any():
            z_med = float(np.median(depth[vis]))
            if not (depth_range_mm[0] <= z_med <= depth_range_mm[1]):
                r["depth_out_of_range"] += 1

    r["_occlusion_rates"] = occlusion_rates  # will be popped before printing
    r["_class_names"] = [i["class_name"] for i in gt["instances"]]
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
    print("--- Known limitations (not checked) ---")
    print("  - 3D bottle pose not exported → pose-variety coverage untestable")
    print("  - suction-point GT not exported → benchmark not yet usable")
    print("  - rendering realism (FID vs real L515 captures) not measured here")


if __name__ == "__main__":
    main()
