"""Visualize the WIGGLE noise component on one real bottle from our synth.

Loads a least-occluded bottle from output/scene_000001, builds a clean
synthetic depth in its silhouette, then applies axial noise ONLY (all other
noise components disabled), and saves a 3-panel side-by-side image:

    [ clean depth ]  [ + wiggle ]  [ difference (noise only) ]

Output: output/noise_demo/wiggle.png

Run from project root:
    ./.venv_synth/bin/python scripts/viz/viz_noise_wiggle.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Allow importing depth_noise from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depth_noise import apply_l515_noise

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "output" / "noise_demo"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def pick_bottle(scene_dir: Path) -> dict:
    """Least-occluded, largest-visible bottle in the scene."""
    with open(scene_dir / "scene_gt.json") as f:
        gt = json.load(f)
    return min(gt["instances"],
               key=lambda i: (i["occlusion_rate"], -i["visible_px"]))


def load_mask_crop(scene_dir: Path, inst: dict, pad: int = 16) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Return (cropped_mask_bool, bbox_xywh_padded)."""
    mask = np.array(Image.open(scene_dir / inst["amodal_mask"])) > 0
    h, w = mask.shape
    x, y, bw, bh = inst["bbox_xywh_amodal"]
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(w, x + bw + pad)
    y1 = min(h, y + bh + pad)
    return mask[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)


def depth_to_rgb(depth_m: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """Map a depth array to a JET-like colormap PNG. Zero pixels stay black."""
    valid = depth_m > 0
    norm = np.clip((depth_m - vmin) / (vmax - vmin), 0, 1)
    # Simple JET-ish: blue (close) -> green -> yellow -> red (far)
    r = np.clip(1.5 - 4 * np.abs(norm - 0.75), 0, 1)
    g = np.clip(1.5 - 4 * np.abs(norm - 0.5),  0, 1)
    b = np.clip(1.5 - 4 * np.abs(norm - 0.25), 0, 1)
    out = np.stack([r, g, b], axis=-1) * 255
    out[~valid] = 0
    return out.astype(np.uint8)


def diff_to_rgb(diff_mm: np.ndarray, vmax_mm: float = 15.0) -> np.ndarray:
    """Diverging colormap: blue (negative) - white (zero) - red (positive)."""
    norm = np.clip(diff_mm / vmax_mm, -1, 1)
    out = np.zeros((*diff_mm.shape, 3), dtype=np.float32)
    pos = norm > 0
    neg = norm < 0
    out[pos, 0] = 1.0
    out[pos, 1] = 1.0 - norm[pos]
    out[pos, 2] = 1.0 - norm[pos]
    out[neg, 0] = 1.0 + norm[neg]
    out[neg, 1] = 1.0 + norm[neg]
    out[neg, 2] = 1.0
    out[~(pos | neg)] = 1.0
    return (out * 255).astype(np.uint8)


def add_label(arr: np.ndarray, text: str) -> np.ndarray:
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    # Black box for legibility
    draw.rectangle([(0, 0), (arr.shape[1], 26)], fill=(0, 0, 0))
    draw.text((6, 4), text, fill=(255, 255, 255), font=font)
    return np.array(img)


def main() -> None:
    # Find any rendered scene; output/ now has camera-height subfolders.
    candidates = sorted(REPO.glob("output/**/scene_*/scene_gt.json"))
    if not candidates:
        raise SystemExit("no rendered scenes found under output/ — render one first")
    scene_dir = candidates[0].parent
    print(f"using scene: {scene_dir.relative_to(REPO)}")

    inst = pick_bottle(scene_dir)
    print(f"chose: {inst['class_name']} (id={inst['instance_id']}, "
          f"occlusion={inst['occlusion_rate']:.2f}, visible_px={inst['visible_px']})")

    mask, (x0, y0, w, h) = load_mask_crop(scene_dir, inst)

    # Build a CLEAN synthetic depth: bottle silhouette at ~1.24m (just above
    # tray floor at 1.286m), background at tray floor depth.
    bottle_depth_m = 1.24
    floor_depth_m  = 1.286
    clean = np.where(mask, bottle_depth_m, floor_depth_m).astype(np.float32)

    # Apply WIGGLE ONLY — disable every other noise component.
    # depth_noise.py is now v2-l515 with extra components beyond v1; we have
    # to explicitly zero them all out so only axial wiggle remains.
    noisy = apply_l515_noise(
        clean,
        seed=42,
        mn=1.25,
        # axial polynomial (the WIGGLE) — keep defaults
        # v1 components — disable
        lateral_sigma_px=0,
        edge_band_px=0,
        edge_threshold_mm=10000,
        edge_noise_mult=1.0,
        dropout_rate=0,
        quant_mm=0,
        # v2-l515 components — disable
        specular_dropout_rate=0,
        dark_dropout_rate=0,
        grazing_dropout_max=0,
        radial_bias_amplitude_mm=0,
        low_freq_bias_std_mm=0,
        rgb_depth_shift_max_px=0,
    )

    diff_mm = (noisy - clean) * 1000.0   # noise pattern in mm

    # ALSO build a 20× exaggerated version so the user can SEE what the
    # wiggle would look like if it were eye-visible. Real L515 wiggle is
    # ~0.5 mm — far too small to perceive at any normal display scale.
    noisy_x20 = clean + (noisy - clean) * 20.0

    # TIGHT depth color range so even mm-scale noise becomes visible.
    vmin, vmax = 1.235, 1.290
    clean_rgb   = depth_to_rgb(clean, vmin, vmax)
    noisy_rgb   = depth_to_rgb(noisy, vmin, vmax)
    noisy20_rgb = depth_to_rgb(noisy_x20, vmin, vmax)
    diff_vmax = max(0.5, 3 * diff_mm.std())
    diff_rgb  = diff_to_rgb(diff_mm, vmax_mm=diff_vmax)

    clean_rgb   = add_label(clean_rgb,   "1. CLEAN (no noise)")
    noisy_rgb   = add_label(noisy_rgb,   f"2. + REAL L515 wiggle (std={diff_mm.std():.2f} mm)")
    noisy20_rgb = add_label(noisy20_rgb, "3. + WIGGLE x20 (exaggerated)")
    diff_rgb    = add_label(diff_rgb,    "4. DIFFERENCE (the noise pattern)")

    sep = np.full((clean_rgb.shape[0], 4, 3), 255, dtype=np.uint8)
    panel = np.concatenate([clean_rgb, sep, noisy_rgb, sep, noisy20_rgb, sep, diff_rgb], axis=1)

    out_path = OUT_DIR / "wiggle.png"
    Image.fromarray(panel).save(out_path)
    print(f"saved -> {out_path.relative_to(REPO)}")
    print(f"crop size: {w}×{h} px (cropped from full 1920×1080 RGB)")
    print(f"noise std: {diff_mm.std():.2f} mm (target: ~5-7 mm at z=1.24m)")


if __name__ == "__main__":
    main()
