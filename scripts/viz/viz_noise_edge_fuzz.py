"""Visualize the EDGE FUZZ noise component.

Edge fuzz = where one object ends and another begins, the depth gets messy
because real depth sensors can't cleanly resolve depth discontinuities.

Output: output/noise_demo/edge_fuzz.png

Run from project root:
    ./.venv_synth/bin/python scripts/viz/viz_noise_edge_fuzz.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from depth_noise import apply_l515_noise

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "output" / "noise_demo"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def pick_bottle(scene_dir: Path) -> dict:
    with open(scene_dir / "scene_gt.json") as f:
        gt = json.load(f)
    return min(gt["instances"],
               key=lambda i: (i["occlusion_rate"], -i["visible_px"]))


def load_mask_crop(scene_dir: Path, inst: dict, pad: int = 16) -> tuple[np.ndarray, tuple]:
    mask = np.array(Image.open(scene_dir / inst["amodal_mask"])) > 0
    h, w = mask.shape
    x, y, bw, bh = inst["bbox_xywh_amodal"]
    x0 = max(0, x - pad); y0 = max(0, y - pad)
    x1 = min(w, x + bw + pad); y1 = min(h, y + bh + pad)
    return mask[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)


def depth_to_rgb(depth_m: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    valid = depth_m > 0
    norm = np.clip((depth_m - vmin) / (vmax - vmin), 0, 1)
    r = np.clip(1.5 - 4 * np.abs(norm - 0.75), 0, 1)
    g = np.clip(1.5 - 4 * np.abs(norm - 0.5),  0, 1)
    b = np.clip(1.5 - 4 * np.abs(norm - 0.25), 0, 1)
    out = np.stack([r, g, b], axis=-1) * 255
    out[~valid] = 0
    return out.astype(np.uint8)


def diff_to_rgb(diff_mm: np.ndarray, vmax_mm: float) -> np.ndarray:
    norm = np.clip(diff_mm / vmax_mm, -1, 1)
    out = np.zeros((*diff_mm.shape, 3), dtype=np.float32)
    pos = norm > 0; neg = norm < 0
    out[pos, 0] = 1.0; out[pos, 1] = 1.0 - norm[pos]; out[pos, 2] = 1.0 - norm[pos]
    out[neg, 0] = 1.0 + norm[neg]; out[neg, 1] = 1.0 + norm[neg]; out[neg, 2] = 1.0
    out[~(pos | neg)] = 1.0
    return (out * 255).astype(np.uint8)


def add_label(arr: np.ndarray, text: str) -> np.ndarray:
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle([(0, 0), (arr.shape[1], 26)], fill=(0, 0, 0))
    draw.text((6, 4), text, fill=(255, 255, 255), font=font)
    return np.array(img)


def main() -> None:
    candidates = sorted(REPO.glob("output/**/scene_*/scene_gt.json"))
    if not candidates:
        raise SystemExit("no rendered scenes found under output/")
    scene_dir = candidates[0].parent
    print(f"using scene: {scene_dir.relative_to(REPO)}")

    inst = pick_bottle(scene_dir)
    print(f"chose: {inst['class_name']}")
    mask, _ = load_mask_crop(scene_dir, inst)

    # Same synthetic clean depth: bottle silhouette at 1.24m, floor at 1.286m.
    # Edge fuzz needs a sharp depth jump to demonstrate, which we have here.
    clean = np.where(mask, 1.24, 1.286).astype(np.float32)

    # Apply EDGE FUZZ ONLY — everything else off, including axial wiggle.
    # Edge fuzz = extra noise around depth discontinuities. We use the
    # default edge_band_px=3, edge_threshold_mm=20, edge_noise_mult=3.
    noisy = apply_l515_noise(
        clean, seed=42, mn=1.0,
        # turn axial wiggle OFF so we only see edge fuzz
        axial_a0_mm=0, axial_a1_mm=0, axial_a2_mm=0,
        # edge fuzz — keep at v1 defaults
        edge_band_px=3, edge_threshold_mm=20.0, edge_noise_mult=3.0,
        # disable all other components
        lateral_sigma_px=0, dropout_rate=0, quant_mm=0,
        specular_dropout_rate=0, dark_dropout_rate=0,
        grazing_dropout_max=0,
        radial_bias_amplitude_mm=0, low_freq_bias_std_mm=0,
        rgb_depth_shift_max_px=0,
    )

    # Edge fuzz only fires NEAR edges; pixels far from edges should be unchanged.
    # We confirm this by checking the diff is concentrated at the boundary.
    diff_mm = (noisy - clean) * 1000.0

    # Hmm — with axial=0 and only edge enabled, sigma_z=0 so edge_extra=0.
    # The edge code multiplies by sigma_z * (mult - 1). To actually visualize
    # edge fuzz, we need axial > 0 too (edge fuzz is multiplicative on axial).
    # Re-render with small axial baseline so edge fuzz has something to scale:
    noisy = apply_l515_noise(
        clean, seed=42, mn=1.25,
        # axial polynomial — keep small baseline so edge can multiply it
        # (using current L515 defaults: 0.3 + 0.1*z)
        # edge fuzz at default 3× multiplier
        edge_band_px=3, edge_threshold_mm=20.0, edge_noise_mult=3.0,
        # disable everything else
        lateral_sigma_px=0, dropout_rate=0, quant_mm=0,
        specular_dropout_rate=0, dark_dropout_rate=0,
        grazing_dropout_max=0,
        radial_bias_amplitude_mm=0, low_freq_bias_std_mm=0,
        rgb_depth_shift_max_px=0,
    )
    diff_mm = (noisy - clean) * 1000.0
    print(f"overall diff std: {diff_mm.std():.2f} mm")

    # Compute std specifically inside vs outside the edge band
    from scipy.ndimage import binary_dilation
    edge_pixels = (np.abs(np.gradient(clean.astype(np.float32))[0]) > 0.001) | \
                  (np.abs(np.gradient(clean.astype(np.float32))[1]) > 0.001)
    edge_band = binary_dilation(edge_pixels, iterations=4)
    print(f"std in EDGE band: {diff_mm[edge_band].std():.2f} mm")
    print(f"std in INTERIOR (away from edges): {diff_mm[~edge_band].std():.2f} mm")

    # Build amplified version so the difference is eye-visible
    noisy_x10 = clean + (noisy - clean) * 10.0

    # TIGHT depth color range
    vmin, vmax = 1.235, 1.290
    clean_rgb    = depth_to_rgb(clean, vmin, vmax)
    noisy_rgb    = depth_to_rgb(noisy, vmin, vmax)
    noisy10_rgb  = depth_to_rgb(noisy_x10, vmin, vmax)
    diff_vmax = max(2.0, 3 * diff_mm.std())
    diff_rgb     = diff_to_rgb(diff_mm, vmax_mm=diff_vmax)

    clean_rgb    = add_label(clean_rgb,   "1. CLEAN (sharp bottle/floor edge)")
    noisy_rgb    = add_label(noisy_rgb,   f"2. + REAL edge fuzz (interior std={diff_mm[~edge_band].std():.1f} mm, edge std={diff_mm[edge_band].std():.1f} mm)")
    noisy10_rgb  = add_label(noisy10_rgb, "3. + EDGE FUZZ x10 (boundary noise visible)")
    diff_rgb     = add_label(diff_rgb,    "4. DIFFERENCE (concentrated on edges)")

    sep = np.full((clean_rgb.shape[0], 4, 3), 255, dtype=np.uint8)
    panel = np.concatenate([clean_rgb, sep, noisy_rgb, sep, noisy10_rgb, sep, diff_rgb], axis=1)

    out_path = OUT_DIR / "edge_fuzz.png"
    Image.fromarray(panel).save(out_path)
    print(f"saved -> {out_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
