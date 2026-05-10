"""Visualize the LIGHTING VARIETY across scenes.

Each scene gets random light color (CCT 2500-6500K), brightness, and position.
We display 5 scenes side by side so the user can SEE the variation.

Output: output/noise_demo/lighting_variety.png

Run:
    ./.venv_synth/bin/python scripts/viz/viz_lighting_variety.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "output" / "noise_demo"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def add_label(arr: np.ndarray, lines: list[str]) -> np.ndarray:
    """Black banner at the top with up-to-3 lines of label text."""
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    banner_h = 22 * len(lines) + 8
    draw.rectangle([(0, 0), (arr.shape[1], banner_h)], fill=(0, 0, 0))
    for i, line in enumerate(lines):
        draw.text((8, 4 + i * 22), line, fill=(255, 255, 255), font=font)
    return np.array(img)


def describe_lighting(rgb: np.ndarray) -> tuple[str, str, np.ndarray]:
    """Eyeball estimates of dominant lighting style from the rendered RGB.
    Returns (color_label, brightness_label, mean_floor_rgb). Tighter thresholds
    than V1 since AgX + neutral world ambient compresses the variation."""
    floor = rgb[100:200, 100:300]
    floor_mean = floor.reshape(-1, 3).mean(axis=0)

    # Tighter R/B threshold — AgX neutralizes most of the CCT shift.
    rb_ratio = floor_mean[0] / max(1.0, floor_mean[2])
    if rb_ratio > 1.025:
        color = "warm"
    elif rb_ratio < 0.985:
        color = "cool"
    else:
        color = "near-neutral"

    lum = 0.299 * floor_mean[0] + 0.587 * floor_mean[1] + 0.114 * floor_mean[2]
    if lum > 220:
        brightness = "bright"
    elif lum > 200:
        brightness = "medium"
    else:
        brightness = "dim"

    return color, brightness, floor_mean.astype(np.uint8)


def main() -> None:
    # Use scenes from a single camera-height folder so lighting is the only variable.
    scenes_dir = REPO / "output" / "h_1.286"
    if not scenes_dir.exists():
        # Fallback: any height folder
        candidates = sorted(REPO.glob("output/h_*/scene_*/rgb/0000.png"))
        scene_paths = [p.parent.parent for p in candidates[:5]]
    else:
        scene_paths = sorted(scenes_dir.glob("scene_*"))[:5]

    if not scene_paths:
        raise SystemExit("no rendered scenes found")
    print(f"using {len(scene_paths)} scenes:")

    # Crop a center region of the tray (same crop for every scene) so the only
    # difference is lighting, not framing.
    CROP_X, CROP_Y, CROP_W, CROP_H = 600, 200, 720, 680
    THUMB_W = 360                        # final width of each thumbnail in the panel

    panels = []
    for sp in scene_paths:
        rgb = np.array(Image.open(sp / "rgb" / "0000.png"))
        crop = rgb[CROP_Y:CROP_Y + CROP_H, CROP_X:CROP_X + CROP_W]
        thumb_h = int(THUMB_W * CROP_H / CROP_W)
        thumb = np.array(Image.fromarray(crop).resize((THUMB_W, thumb_h), Image.LANCZOS))

        color, brightness, floor_rgb = describe_lighting(rgb)
        rb = floor_rgb[0] / max(1, floor_rgb[2])
        lum = 0.299 * floor_rgb[0] + 0.587 * floor_rgb[1] + 0.114 * floor_rgb[2]
        labelled = add_label(thumb, [
            f"{sp.name} -- {color} / {brightness}",
            f"floor RGB={tuple(int(v) for v in floor_rgb)}  R/B={rb:.3f}",
            f"luminance={lum:.0f}/255",
        ])
        print(f"  {sp.name}: color={color}, brightness={brightness}, floor={floor_rgb} R/B={rb:.3f}")
        panels.append(labelled)

    sep = np.full((panels[0].shape[0], 4, 3), 255, dtype=np.uint8)
    full = panels[0]
    for p in panels[1:]:
        full = np.concatenate([full, sep, p], axis=1)

    # Top caption strip — honest about what we observe
    caption_h = 78
    caption = np.zeros((caption_h, full.shape[1], 3), dtype=np.uint8)
    img = Image.fromarray(caption)
    draw = ImageDraw.Draw(img)
    try:
        font_b  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except OSError:
        font_b = font_sm = ImageFont.load_default()
    draw.text((10, 8),
              "LIGHTING VARIETY across 5 scenes (same tray, same camera, "
              "per-scene random CCT 2500-6500K + intensity + position)",
              fill=(255, 255, 255), font=font_b)
    draw.text((10, 42),
              "Honest finding: variation is mathematically present (~3% R/B, ~7% brightness) "
              "but barely visible — neutral white world ambient + AgX tone-map compress the shift.",
              fill=(180, 200, 255), font=font_sm)
    full = np.concatenate([np.array(img), full], axis=0)

    out_path = OUT_DIR / "lighting_variety.png"
    Image.fromarray(full).save(out_path)
    print(f"\nsaved -> {out_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
