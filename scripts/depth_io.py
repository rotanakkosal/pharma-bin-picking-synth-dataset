"""
Centralized depth I/O for the pharma-bin synthetic dataset.

Single source of truth for converting saved `depth/<frame>.png` raw uint16
values into meters. Reads the per-scene `depth_unit_m` field from
`scene_gt.json` (BOP convention: meters_value = png_value * depth_unit_m).

If `depth_unit_m` is absent from `scene_gt.json`, falls back to 0.001 (the
v1 mm-based assumption used by all scenes rendered before 2026-05-08). This
keeps legacy v1 scenes readable without re-rendering. Future format bumps
beyond v2-l515 (depth_unit_m=0.00025) MUST set this field explicitly — do
NOT default it on the writer side.

Why a centralized helper: the v1→v2 storage migration (`*1000` → `*4000`,
i.e. mm → 0.25mm bins) silently breaks any consumer that hardcodes
`depth_png_value / 1000.0`. Routing all readers through `load_depth_m()`
keeps unit handling explicit and migration-safe.

Usage:
    from depth_io import load_depth_m
    depth_m = load_depth_m(scene_dir)        # always returns float32 meters
    depth_mm = depth_m * 1000.0              # convert if you need mm
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


# Legacy v1 default: depth was saved in millimeters (raw_value * 0.001 = m).
# Used when scene_gt.json lacks an explicit depth_unit_m field.
LEGACY_DEPTH_UNIT_M = 0.001


def load_depth_m(scene_dir: Path | str, frame: str = "0000") -> np.ndarray:
    """Load depth from `<scene_dir>/depth/<frame>.png` and return as float32 meters.

    Reads `depth_unit_m` from `<scene_dir>/scene_gt.json` (BOP convention) to
    determine the unit scale. Falls back to LEGACY_DEPTH_UNIT_M (0.001) if the
    field is absent — this preserves readability of v1 scenes (which used
    `depth_unit: "mm"` but not the numeric `depth_unit_m` field).

    Args:
        scene_dir: path to a scene directory containing `depth/<frame>.png`
                   and (preferably) `scene_gt.json`.
        frame: frame name without extension; defaults to "0000".

    Returns:
        depth_m: HxW float32 array of depth values in meters. Pixels with no
                 depth (raw 0) remain 0.0.
    """
    scene_dir = Path(scene_dir)
    gt_path = scene_dir / "scene_gt.json"
    depth_png = scene_dir / "depth" / f"{frame}.png"

    if not depth_png.exists():
        raise FileNotFoundError(f"depth image not found: {depth_png}")

    depth_unit_m = LEGACY_DEPTH_UNIT_M
    if gt_path.exists():
        with gt_path.open() as f:
            gt = json.load(f)
        depth_unit_m = float(gt.get("depth_unit_m", LEGACY_DEPTH_UNIT_M))

    # Lazy import to keep this module dependency-light when only the constant
    # is needed.
    from PIL import Image
    raw = np.asarray(Image.open(depth_png))
    depth_m = raw.astype(np.float32) * depth_unit_m
    return depth_m


def load_depth_unit_m(scene_dir: Path | str) -> float:
    """Return the depth_unit_m for a scene without loading the depth array.
    Useful when consumers need the scale factor for their own I/O paths
    (e.g., cv2-based readers that prefer to do the multiplication themselves)."""
    scene_dir = Path(scene_dir)
    gt_path = scene_dir / "scene_gt.json"
    if not gt_path.exists():
        return LEGACY_DEPTH_UNIT_M
    with gt_path.open() as f:
        gt = json.load(f)
    return float(gt.get("depth_unit_m", LEGACY_DEPTH_UNIT_M))
