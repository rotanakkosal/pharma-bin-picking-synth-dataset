"""Visualize the top-K suction GT points overlaid on the scene RGB.

Run: python scripts/viz_suction.py --scene output/scene_000999 [--top 5]
Saves: <scene>/suction_overlay.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def score_to_color(s: float) -> tuple[int, int, int]:
    """Green for high score, red for low. 0->red, 1->green."""
    s = max(0.0, min(1.0, s))
    r = int(255 * (1 - s))
    g = int(255 * s)
    return (r, g, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--top", type=int, default=5,
                    help="Top-K points per instance to draw")
    ap.add_argument("--cup-radius", action="store_true",
                    help="Draw the actual cup-pixel-radius circle (not just a dot)")
    args = ap.parse_args()

    with open(args.scene / "scene_gt.json") as f:
        gt = json.load(f)

    rgb = Image.open(args.scene / gt["rgb"]).convert("RGB")
    draw = ImageDraw.Draw(rgb)

    K = np.array(gt["camera_K"])
    cup_radius_mm = gt["suction_meta"]["cup_radius_mm"]

    n_drawn = 0
    for inst in gt["instances"]:
        for p in inst["suction_points"][:args.top]:
            u, v = p["point_2d_px"]
            s = p["S_combined_default"]
            color = score_to_color(s)

            if args.cup_radius:
                z_m = p["point_3d_cam"][2]
                r_px = int(cup_radius_mm * 1e-3 * K[0, 0] / max(z_m, 1e-3))
                draw.ellipse([u - r_px, v - r_px, u + r_px, v + r_px],
                             outline=color, width=2)
            else:
                r = 6
                draw.ellipse([u - r, v - r, u + r, v + r],
                             outline=color, fill=color, width=1)
            n_drawn += 1

    out_path = args.scene / "suction_overlay.png"
    rgb.save(out_path)
    print(f"[ok] drew {n_drawn} points across {len(gt['instances'])} instances")
    print(f"[ok] saved -> {out_path}")


if __name__ == "__main__":
    main()
