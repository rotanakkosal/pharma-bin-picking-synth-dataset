"""Visualize all 5 depth-noise components, one PNG each.

Each PNG is a 4-panel side-by-side:
    [ CLEAN ]  [ + real noise ]  [ + amplified noise ]  [ difference ]

Outputs (under output/noise_demo/):
    1_wiggle.png       — axial Gaussian noise
    2_edge_fuzz.png    — extra noise at depth discontinuities
    3_slight_blur.png  — small lateral smoothing
    4_random_holes.png — uniform random dropouts
    5_steps.png        — quantization to 0.25 mm bins

Run from project root:
    ./.venv_synth/bin/python scripts/viz/viz_noise_all.py
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

# All v2-l515 components disabled by default. Each demo turns ON exactly one.
ALL_OFF = dict(
    mn=1.25,
    axial_a0_mm=0, axial_a1_mm=0, axial_a2_mm=0,
    lateral_sigma_px=0,
    edge_band_px=0, edge_threshold_mm=10000, edge_noise_mult=1.0,
    dropout_rate=0, quant_mm=0,
    specular_dropout_rate=0, dark_dropout_rate=0,
    grazing_dropout_max=0,
    radial_bias_amplitude_mm=0, low_freq_bias_std_mm=0,
    rgb_depth_shift_max_px=0,
)


def pick_bottle(scene_dir: Path) -> dict:
    with open(scene_dir / "scene_gt.json") as f:
        gt = json.load(f)
    return min(gt["instances"],
               key=lambda i: (i["occlusion_rate"], -i["visible_px"]))


def load_mask_crop(scene_dir: Path, inst: dict, pad: int = 16) -> np.ndarray:
    mask = np.array(Image.open(scene_dir / inst["amodal_mask"])) > 0
    h, w = mask.shape
    x, y, bw, bh = inst["bbox_xywh_amodal"]
    x0 = max(0, x - pad); y0 = max(0, y - pad)
    x1 = min(w, x + bw + pad); y1 = min(h, y + bh + pad)
    return mask[y0:y1, x0:x1]


def depth_to_rgb(depth_m: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    valid = depth_m > 0
    norm = np.clip((depth_m - vmin) / (vmax - vmin), 0, 1)
    r = np.clip(1.5 - 4 * np.abs(norm - 0.75), 0, 1)
    g = np.clip(1.5 - 4 * np.abs(norm - 0.5),  0, 1)
    b = np.clip(1.5 - 4 * np.abs(norm - 0.25), 0, 1)
    out = np.stack([r, g, b], axis=-1) * 255
    out[~valid] = 0   # holes appear as black
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
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle([(0, 0), (arr.shape[1], 22)], fill=(0, 0, 0))
    draw.text((6, 4), text, fill=(255, 255, 255), font=font)
    return np.array(img)


def build_panel(clean: np.ndarray, noisy: np.ndarray, amplification: float,
                title_real: str, title_amp: str, title_diff: str,
                vmin: float = 1.235, vmax: float = 1.290) -> np.ndarray:
    """Build the standard 4-panel side-by-side: clean | real | amplified | diff."""
    diff_m = noisy - clean
    diff_mm = diff_m * 1000.0
    amp = clean + diff_m * amplification

    clean_rgb = depth_to_rgb(clean, vmin, vmax)
    noisy_rgb = depth_to_rgb(noisy, vmin, vmax)
    amp_rgb   = depth_to_rgb(amp, vmin, vmax)
    diff_vmax = max(2.0, 3 * np.abs(diff_mm).std())
    diff_rgb  = diff_to_rgb(diff_mm, vmax_mm=diff_vmax)

    clean_rgb = add_label(clean_rgb, "1. CLEAN (no noise)")
    noisy_rgb = add_label(noisy_rgb, title_real)
    amp_rgb   = add_label(amp_rgb,   title_amp)
    diff_rgb  = add_label(diff_rgb,  title_diff)

    sep = np.full((clean_rgb.shape[0], 4, 3), 255, dtype=np.uint8)
    return np.concatenate([clean_rgb, sep, noisy_rgb, sep, amp_rgb, sep, diff_rgb], axis=1)


def viz_wiggle(clean: np.ndarray) -> None:
    """Axial Gaussian noise — uses current L515 polynomial defaults."""
    noisy = apply_l515_noise(
        clean, seed=42,
        **{**ALL_OFF, "axial_a0_mm": 0.3, "axial_a1_mm": 0.1, "axial_a2_mm": 0.0},
    )
    diff_mm = (noisy - clean) * 1000.0
    panel = build_panel(
        clean, noisy, amplification=20,
        title_real=f"2. + REAL L515 wiggle (std={diff_mm.std():.2f} mm)",
        title_amp="3. + WIGGLE x20 (exaggerated)",
        title_diff="4. DIFFERENCE (random pixel jitter)",
    )
    Image.fromarray(panel).save(OUT_DIR / "1_wiggle.png")
    print(f"  wiggle: std={diff_mm.std():.2f} mm")


def viz_edge_fuzz(clean: np.ndarray) -> None:
    """Edge-band extra noise. Needs axial baseline > 0 since it multiplies."""
    noisy = apply_l515_noise(
        clean, seed=42,
        **{**ALL_OFF,
           "axial_a0_mm": 0.3, "axial_a1_mm": 0.1, "axial_a2_mm": 0.0,
           "edge_band_px": 3, "edge_threshold_mm": 20.0, "edge_noise_mult": 3.0},
    )
    diff_mm = (noisy - clean) * 1000.0

    # Find which pixels are in the edge band
    grad_x = np.abs(np.diff(clean, axis=1, prepend=clean[:, :1]))
    grad_y = np.abs(np.diff(clean, axis=0, prepend=clean[:1, :]))
    edge = (grad_x > 0.001) | (grad_y > 0.001)
    from scipy.ndimage import binary_dilation
    edge_band = binary_dilation(edge, iterations=4)
    interior_std = diff_mm[~edge_band].std()
    edge_std     = diff_mm[edge_band].std()

    panel = build_panel(
        clean, noisy, amplification=10,
        title_real=f"2. + REAL edge fuzz (interior {interior_std:.1f}mm vs edge {edge_std:.1f}mm)",
        title_amp="3. + EDGE FUZZ x10 (boundary noise visible)",
        title_diff="4. DIFFERENCE (concentrated on the bottle outline)",
    )
    Image.fromarray(panel).save(OUT_DIR / "2_edge_fuzz.png")
    print(f"  edge_fuzz: interior={interior_std:.2f} mm, edge={edge_std:.2f} mm")


def viz_blur(clean: np.ndarray) -> None:
    """Lateral Gaussian blur — softens the whole image."""
    noisy = apply_l515_noise(
        clean, seed=42, **{**ALL_OFF, "lateral_sigma_px": 0.5},
    )
    diff_mm = (noisy - clean) * 1000.0
    panel = build_panel(
        clean, noisy, amplification=20,
        title_real=f"2. + REAL slight blur (sigma=0.5 px)",
        title_amp="3. + BLUR x20 (softening visible at edges)",
        title_diff="4. DIFFERENCE (only at boundaries — interior unchanged)",
    )
    Image.fromarray(panel).save(OUT_DIR / "3_slight_blur.png")
    print(f"  blur: max abs diff={np.abs(diff_mm).max():.2f} mm")


def viz_holes(clean: np.ndarray) -> None:
    """Uniform random dropout — pixels go to 0."""
    # Bump dropout from 0.5% to 5% for visibility (real L515 defaults are tiny)
    noisy = apply_l515_noise(
        clean, seed=42, **{**ALL_OFF, "dropout_rate": 0.05},
    )
    diff_mm = (noisy - clean) * 1000.0
    n_holes = (noisy == 0).sum()
    pct = 100.0 * n_holes / noisy.size
    panel = build_panel(
        clean, noisy, amplification=1,
        title_real=f"2. RANDOM HOLES at 5% rate ({n_holes} holes, {pct:.1f}% of pixels)",
        title_amp="3. SAME (no amplification needed — black dots are visible)",
        title_diff="4. DIFFERENCE (each blue dot = one dead pixel)",
    )
    Image.fromarray(panel).save(OUT_DIR / "4_random_holes.png")
    print(f"  holes: {n_holes} pixels = {pct:.2f}%")


def viz_steps(clean: np.ndarray) -> None:
    """Quantization — depths snap to 0.25 mm bins. Visible only on smooth slopes."""
    # Build a sloped ground (not flat) so quantization shows as banding stripes.
    # At 1.286m floor in our scene depth gradient is essentially 0, so we add
    # a synthetic gentle slope across the floor to show how steps look on a
    # smoothly-varying surface.
    H, W = clean.shape
    yy = np.linspace(0, 0.020, H)[:, None]   # 20mm slope across image height
    sloped = clean + np.where(clean > 1.27, yy, 0.0).astype(np.float32)
    # Use a coarser quant (5 mm) so steps are visible by eye — real L515 is 0.25 mm
    noisy = apply_l515_noise(
        sloped, seed=42, **{**ALL_OFF, "quant_mm": 5.0},
    )
    diff_mm = (noisy - sloped) * 1000.0
    panel = build_panel(
        sloped, noisy, amplification=1,
        title_real=f"2. + STEPS at 5mm bins (banding visible on slope)",
        title_amp="3. SAME (no amplification — bands are big enough to see)",
        title_diff="4. DIFFERENCE (sawtooth pattern from rounding)",
    )
    Image.fromarray(panel).save(OUT_DIR / "5_steps.png")
    print(f"  steps: max abs diff={np.abs(diff_mm).max():.2f} mm (at 5mm quant)")
    print(f"    NOTE: real L515 quant is 0.25 mm — invisible by eye; we use 5 mm here for demo")


def main() -> None:
    candidates = sorted(REPO.glob("output/**/scene_*/scene_gt.json"))
    if not candidates:
        raise SystemExit("no rendered scenes — render at least one first")
    scene_dir = candidates[0].parent
    print(f"using scene: {scene_dir.relative_to(REPO)}")

    inst = pick_bottle(scene_dir)
    print(f"chose: {inst['class_name']}")
    mask = load_mask_crop(scene_dir, inst)
    clean = np.where(mask, 1.24, 1.286).astype(np.float32)

    print("\nrendering noise demos:")
    viz_wiggle(clean)
    viz_edge_fuzz(clean)
    viz_blur(clean)
    viz_holes(clean)
    viz_steps(clean)
    print(f"\nall PNGs in: {OUT_DIR.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
