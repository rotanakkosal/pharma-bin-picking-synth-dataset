"""
Intel L515-style depth noise simulation.

Pristine synth depth is the most-cited sim-to-real gap in the literature
(Lehrmann et al. 2024, arXiv:2402.16514). UOAIS and most segmentation/grasp
networks were trained on real RealSense / Kinect depth and have learned to
ignore noise patterns; feeding them noise-free synth removes a signal they
rely on. Adding realistic noise closes that gap.

Convention: this module operates on a CLEAN depth map (meters, float32) and
returns a NOISY depth map (meters, float32). The caller is responsible for
converting to uint16 mm for serialization.

Design choices for L515 (solid-state LiDAR, not stereo):
- Axial noise std grows roughly with z² (Lehrmann §III, polynomial fit).
- Edge / depth-discontinuity regions get extra noise (multipath / occlusion bleed).
- Random dropouts mimic the L515's known holes on dark/specular surfaces.
- All noise quantized to L515's effective resolution (~1mm at our distance).
- Default `Mn=1.25` matches Lehrmann's empirical sweet spot; both Mn=0
  (pristine) and Mn≥1.5 (over-noisy) hurt downstream IoU on real data.
"""
from __future__ import annotations

import numpy as np


DEFAULTS = {
    # Noise multiplier per Lehrmann's 2024 sweet spot. Tunable per-sensor;
    # re-calibrate when real L515 captures arrive.
    "mn":              1.25,

    # Axial (along-ray) noise polynomial fit, σ_z(z) in mm:
    #   σ_z(z) = a0 + a1*z + a2*z²    (z in meters)
    # At our camera height z≈1.286m, this gives σ_z ≈ 6.0 mm pre-Mn,
    # ≈ 7.6 mm with default Mn=1.25 — within the L515 datasheet 5–15 mm
    # band and consistent with reported empirical L515 noise studies.
    "axial_a0_mm":     1.0,
    "axial_a1_mm":     2.0,
    "axial_a2_mm":     1.5,

    # Lateral (across-ray) noise smaller than axial for solid-state LiDAR.
    # Modeled as a Gaussian blur with σ in pixels.
    "lateral_sigma_px": 0.5,

    # Depth-discontinuity penalty: extra noise within `edge_band_px` of any
    # large depth jump, scaled by `edge_noise_mult`.
    "edge_band_px":    3,
    "edge_threshold_mm": 20.0,
    "edge_noise_mult": 3.0,

    # Random dropout rate (pixels set to 0). L515 drops 0.5-2% on typical
    # scenes; we use 1% as a midpoint.
    "dropout_rate":    0.01,

    # Quantization step in mm (L515 effective bin size).
    "quant_mm":        1.0,
}


def apply_l515_noise(
    depth_m: np.ndarray,
    seed: int = 0,
    mn: float = DEFAULTS["mn"],
    axial_a0_mm: float = DEFAULTS["axial_a0_mm"],
    axial_a1_mm: float = DEFAULTS["axial_a1_mm"],
    axial_a2_mm: float = DEFAULTS["axial_a2_mm"],
    lateral_sigma_px: float = DEFAULTS["lateral_sigma_px"],
    edge_band_px: int = DEFAULTS["edge_band_px"],
    edge_threshold_mm: float = DEFAULTS["edge_threshold_mm"],
    edge_noise_mult: float = DEFAULTS["edge_noise_mult"],
    dropout_rate: float = DEFAULTS["dropout_rate"],
    quant_mm: float = DEFAULTS["quant_mm"],
) -> np.ndarray:
    """Apply Intel L515-style noise to a clean depth map.

    Args:
        depth_m: HxW float32 depth in meters. 0 = no return.
        seed: rng seed for reproducibility (per-scene).

    Returns:
        depth_noisy_m: HxW float32 depth in meters with noise applied.
    """
    rng = np.random.default_rng(seed)
    H, W = depth_m.shape
    out = depth_m.astype(np.float32).copy()
    valid = out > 0.01

    # 1) Axial Gaussian noise — std grows with z² (Lehrmann polynomial fit)
    z = out
    sigma_z_mm = axial_a0_mm + axial_a1_mm * z + axial_a2_mm * (z ** 2)
    sigma_z_mm = sigma_z_mm * mn
    sigma_z_m = sigma_z_mm * 1e-3
    noise_axial = rng.normal(0.0, 1.0, size=out.shape).astype(np.float32) * sigma_z_m
    out = np.where(valid, out + noise_axial, 0.0)

    # 2) Edge-band extra noise — depth discontinuities bleed across boundaries
    if edge_band_px > 0:
        # Detect pixels near a large depth step using a fast horizontal+vertical
        # gradient; cheaper than a full edge filter and effective enough.
        gx = np.zeros_like(z)
        gy = np.zeros_like(z)
        gx[:, 1:] = np.abs(z[:, 1:] - z[:, :-1])
        gy[1:, :] = np.abs(z[1:, :] - z[:-1, :])
        grad = np.maximum(gx, gy)
        edge_mask = grad > (edge_threshold_mm * 1e-3)
        if edge_mask.any():
            # Dilate edge mask by edge_band_px
            try:
                import cv2
                k = 2 * edge_band_px + 1
                kernel = np.ones((k, k), dtype=np.uint8)
                edge_dilated = cv2.dilate(edge_mask.astype(np.uint8), kernel) > 0
            except ImportError:
                edge_dilated = edge_mask
            extra_sigma = sigma_z_m * (edge_noise_mult - 1.0)
            extra = rng.normal(0.0, 1.0, size=out.shape).astype(np.float32) * extra_sigma
            out = np.where(valid & edge_dilated, out + extra, out)

    # 3) Lateral blur — small, simulates cross-ray smoothing of the LiDAR
    if lateral_sigma_px > 0:
        try:
            import cv2
            ksize = max(3, int(2 * round(3 * lateral_sigma_px) + 1))
            blurred = cv2.GaussianBlur(out, (ksize, ksize), lateral_sigma_px)
            # Only apply blur where original was valid; preserve holes
            out = np.where(valid, blurred, out)
        except ImportError:
            pass

    # 4) Random dropouts — pixels go to 0
    if dropout_rate > 0:
        keep = rng.random(out.shape) > dropout_rate
        out = np.where(keep, out, 0.0)

    # 5) Quantization — round to L515's effective bin size
    if quant_mm > 0:
        out_mm = out * 1000.0
        out_mm = np.round(out_mm / quant_mm) * quant_mm
        out = out_mm * 1e-3

    return out


def make_noise_meta(cfg_overrides: dict | None = None) -> dict:
    """Self-describing metadata embedded in scene_gt.json."""
    meta = {
        "version":         "v1",
        "model":           "L515-style polynomial axial + edge-bleed + dropout",
        "mn":              DEFAULTS["mn"],
        "axial_polynomial_mm": [DEFAULTS["axial_a0_mm"],
                                DEFAULTS["axial_a1_mm"],
                                DEFAULTS["axial_a2_mm"]],
        "lateral_sigma_px": DEFAULTS["lateral_sigma_px"],
        "edge_band_px":    DEFAULTS["edge_band_px"],
        "edge_threshold_mm": DEFAULTS["edge_threshold_mm"],
        "edge_noise_mult": DEFAULTS["edge_noise_mult"],
        "dropout_rate":    DEFAULTS["dropout_rate"],
        "quant_mm":        DEFAULTS["quant_mm"],
        "reference":       "Lehrmann et al. 2024, arXiv:2402.16514",
        "calibration_status": "uncalibrated_to_real_L515 — re-tune mn when real captures arrive",
    }
    if cfg_overrides:
        meta.update(cfg_overrides)
    return meta
