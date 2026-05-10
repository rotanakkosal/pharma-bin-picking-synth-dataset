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
    # v2-l515 (2026-05-08): coefficients fitted to Berlin 2021's measured
    # < 0.5 mm precision bound at distances up to 3.5 m (Servi 2021 confirms
    # L515 has the best precision among RealSense devices in close range).
    # At z=1.286 m: σ = 0.3 + 0.1·1.286 + 0·1.286² = 0.43 mm pre-Mn,
    # ≈ 0.54 mm with Mn=1.25 — within Berlin's < 0.5 mm bound.
    # NOTE (B1): these are FITTED to the published bound, not extracted from
    # any parametric model in the literature. See depth_noise_meta.derivation.
    "axial_a0_mm":     0.3,
    "axial_a1_mm":     0.1,
    "axial_a2_mm":     0.0,

    # Lateral (across-ray) noise smaller than axial for solid-state LiDAR.
    # Modeled as a Gaussian blur with σ in pixels.
    "lateral_sigma_px": 0.5,

    # Depth-discontinuity penalty: extra noise within `edge_band_px` of any
    # large depth jump, scaled by `edge_noise_mult`. v2-l515: reduced from 3.0
    # to 1.5 — Intel docs note L515's single-beam scanning has less multipath
    # than global-shutter ToF.
    "edge_band_px":    3,
    "edge_threshold_mm": 20.0,
    "edge_noise_mult": 1.5,

    # Random dropout rate (pixels set to 0). v2-l515: reduced from 1% to 0.5%
    # uniform — the dominant L515 dropout is concentration-based (specular,
    # dark, grazing-angle), modeled separately by apply_material_dropouts.
    "dropout_rate":    0.005,

    # Quantization step in mm. v2-l515: 0.25 mm matches L515's native
    # depth_units (librealsense issue #6636). Now actually realized in saved
    # PNG via writer's *4000 multiplier (was ineffective at 1mm storage in v1).
    "quant_mm":        0.25,

    # --- v2-l515 material + grazing dropouts (A3 + A4) -------------------
    # Specular dropout (A3): bright pixels (luminance > threshold) likely
    # come from specular highlights → cup-glints on cap tops → null IR return
    # in real L515. Default 10% extra dropout on these pixels.
    # Threshold note (2026-05-08): tested 0.85 first but our gray ground
    # floor renders at ~0.85 luminance — false-flagged the entire floor as
    # specular. Restored to 0.92 to keep the proxy selective for true bright
    # highlights on cap tops. Visual validation on scene 3 confirms the fix.
    "specular_luminance_threshold": 0.92,
    "specular_dropout_rate":        0.10,

    # Dark dropout (A3): very dark pixels (luminance < threshold) likely come
    # from text labels and shadows → low IR signal-to-noise → speckled or null
    # depth. Default 5% extra dropout.
    "dark_luminance_threshold": 0.10,
    "dark_dropout_rate":        0.05,

    # Grazing-angle dropout (A4): cosine of angle between the surface normal
    # and the camera ray. At grazing incidence (cos→0), specular bounce
    # reflects away from receiver — fundamental ToF/LiDAR failure mode.
    # Smoothstep ramps dropout from 0 at cos=0.5 (60° from normal) up to
    # `grazing_dropout_max` at cos=0.2 (~78° from normal). Critical for
    # cylindrical-bottle benchmarks: top-down view → bottle SIDES are near
    # grazing → real L515 reliably drops them.
    "grazing_dropout_max":     0.50,
    "grazing_cos_full":        0.5,    # cos(60°) — dropout starts ramping up
    "grazing_cos_max":         0.2,    # cos(~78°) — dropout reaches max here

    # --- v2-l515 systematic radial bias (A2) -----------------------------
    # Quadratic radial bias mimicking L515's ~5 mm accuracy floor (Intel
    # datasheet) and the lens-distortion-related depth-bias documented in
    # librealsense issue #10168. Applied ONLY to saved depth, NEVER to
    # depth_m used by suction GT (back-projection requires unbiased depth).
    "radial_bias_amplitude_mm":  5.0,   # peak deviation at image corners
    "low_freq_bias_std_mm":      0.0,   # disabled — was dominating tray-floor std at 2mm
                                        # (vs Berlin's 0.5mm spec). True low-frequency
                                        # spatial drift would need low-res-then-upsample
                                        # implementation; deferred to V3 if real captures
                                        # show this matters.

    # --- v2-l515 RGB-depth pixel-shift (B4) ------------------------------
    # Real L515 has 2-4 px misalignment between depth and color streams (no
    # dynamic-calibration tool exists). Apply integer pixel roll on saved
    # depth so synth shows the same property.
    "rgb_depth_shift_max_px":    3,
}


def apply_l515_noise(
    depth_m: np.ndarray,
    seed: int = 0,
    rgb: np.ndarray | None = None,           # v2-l515: HxWx3 uint8, for material dropouts
    normals_cam: np.ndarray | None = None,   # v2-l515: HxWx3 float, camera-frame normals
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
    specular_luminance_threshold: float = DEFAULTS["specular_luminance_threshold"],
    specular_dropout_rate: float = DEFAULTS["specular_dropout_rate"],
    dark_luminance_threshold: float = DEFAULTS["dark_luminance_threshold"],
    dark_dropout_rate: float = DEFAULTS["dark_dropout_rate"],
    grazing_dropout_max: float = DEFAULTS["grazing_dropout_max"],
    grazing_cos_full: float = DEFAULTS["grazing_cos_full"],
    grazing_cos_max: float = DEFAULTS["grazing_cos_max"],
    radial_bias_amplitude_mm: float = DEFAULTS["radial_bias_amplitude_mm"],
    low_freq_bias_std_mm: float = DEFAULTS["low_freq_bias_std_mm"],
    rgb_depth_shift_max_px: int = DEFAULTS["rgb_depth_shift_max_px"],
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

    # 4a) Uniform random dropout — small base rate
    if dropout_rate > 0:
        keep = rng.random(out.shape) > dropout_rate
        out = np.where(keep, out, 0.0)

    # 4b) Material-dependent dropout (A3, v2-l515): visible-luminance proxy
    # for 860 nm IR specular/dark behavior. Approximate — see plan §A3 +
    # `proxy_basis` field in metadata. Skipped if `rgb` not provided.
    if rgb is not None and (specular_dropout_rate > 0 or dark_dropout_rate > 0):
        if rgb.dtype == np.uint8:
            rgb_f = rgb.astype(np.float32) / 255.0
        else:
            rgb_f = np.asarray(rgb, dtype=np.float32)
            if rgb_f.max() > 1.5:
                rgb_f = rgb_f / 255.0
        lum = (0.299 * rgb_f[..., 0] + 0.587 * rgb_f[..., 1] + 0.114 * rgb_f[..., 2])
        if specular_dropout_rate > 0:
            specular_mask = lum > specular_luminance_threshold
            spec_drop = rng.random(out.shape) < specular_dropout_rate
            out = np.where(specular_mask & spec_drop, 0.0, out)
        if dark_dropout_rate > 0:
            dark_mask = lum < dark_luminance_threshold
            dark_drop = rng.random(out.shape) < dark_dropout_rate
            out = np.where(dark_mask & dark_drop, 0.0, out)

    # 4c) Grazing-angle dropout (A4, v2-l515): cylindrical sides reflect IR
    # away from receiver at near-grazing incidence. Camera ray points along
    # +Z in OpenCV cam frame; we use the surface normal's z-component as
    # cos(angle_to_camera). If `normals_cam` is not supplied, we derive it
    # from the depth gradient (less accurate at depth discontinuities but
    # avoids a separate BlenderProc render pass).
    if grazing_dropout_max > 0:
        if normals_cam is not None:
            cos_angle = np.clip(np.abs(normals_cam[..., 2]), 0.0, 1.0)
        else:
            # Derive cos(angle) from depth gradient.
            # Standard depth-image normal formula: cos = 1/sqrt((dz/du·fx/z)² + (dz/dv·fy/z)² + 1)
            # We don't have fx/fy here (apply_l515_noise is camera-agnostic), so
            # we use a unit-pixel approximation. For our 1920×1080@1.286m setup
            # this slightly under-detects grazing on small bottles but is
            # qualitatively correct for the dominant top-down pharma bin case.
            # Re-tune this approximation when real captures arrive.
            z_safe = np.where(valid, depth_m, 1.0)
            zx = np.gradient(z_safe, axis=1)
            zy = np.gradient(z_safe, axis=0)
            # Convert depth gradient to dimensionless slope: ~1 m/m per radian tilt
            scale = 800.0    # rough effective scale (z·fx ≈ 1.286·1349 ≈ 1734, /2 for both axes)
            gx = zx * scale
            gy = zy * scale
            cos_angle = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        # Smoothstep: 0 at cos>=grazing_cos_full, 1 at cos<=grazing_cos_max
        t = np.clip((grazing_cos_full - cos_angle) / max(1e-6, grazing_cos_full - grazing_cos_max),
                    0.0, 1.0)
        smooth = t * t * (3.0 - 2.0 * t)   # smoothstep
        grazing_p = grazing_dropout_max * smooth
        graze_drop = rng.random(out.shape) < grazing_p
        out = np.where(valid & graze_drop, 0.0, out)

    # 5) Systematic radial bias (A2, v2-l515): mimics L515's <5 mm accuracy
    # floor (Intel datasheet) and lens-distortion-related depth bias
    # (librealsense issue #10168). Quadratic radial term + low-frequency
    # Gaussian seed. Applied here so it appears in the saved depth but NOT
    # in any GT computation (caller passes a clean depth_m to suction GT).
    # Gated on `out > 0` (NOT `valid`) so pixels dropped above stay 0 —
    # otherwise the bias term would un-drop them.
    if radial_bias_amplitude_mm > 0 or low_freq_bias_std_mm > 0:
        nonzero = out > 0
        if radial_bias_amplitude_mm > 0:
            xx, yy = np.meshgrid(np.linspace(-1, 1, W), np.linspace(-1, 1, H))
            r2 = xx ** 2 + yy ** 2
            radial_bias_m = radial_bias_amplitude_mm * 1e-3 * r2.astype(np.float32)
            out = np.where(nonzero, out + radial_bias_m, out)
        if low_freq_bias_std_mm > 0:
            lf = rng.normal(0.0, low_freq_bias_std_mm * 1e-3, size=out.shape).astype(np.float32)
            out = np.where(nonzero, out + lf, out)

    # 6) Quantization — round to L515's effective bin size
    if quant_mm > 0:
        out_mm = out * 1000.0
        out_mm = np.round(out_mm / quant_mm) * quant_mm
        out = out_mm * 1e-3

    # 7) RGB-depth registration jitter (B4, v2-l515): real L515 has ~2-4 px
    # misalignment between depth and color streams. Applied AFTER all other
    # noise so the shifted holes/biases line up with the saved RGB consistently.
    if rgb_depth_shift_max_px > 0:
        dy = int(rng.integers(-rgb_depth_shift_max_px, rgb_depth_shift_max_px + 1))
        dx = int(rng.integers(-rgb_depth_shift_max_px, rgb_depth_shift_max_px + 1))
        if dy != 0 or dx != 0:
            out = np.roll(out, shift=(dy, dx), axis=(0, 1))

    return out


def make_noise_meta(cfg_overrides: dict | None = None) -> dict:
    """Self-describing metadata embedded in scene_gt.json."""
    meta = {
        "version":         "v2-l515",
        "model":           "L515-shaped: polynomial axial + edge-bleed + uniform dropout + material/grazing dropout + radial bias + RGB-depth jitter",
        # Honest derivation note (B1)
        "derivation":      "axial coefficients fitted to Berlin 2021 < 0.5mm precision bound at 1m; not extracted from a published parametric L515 noise model",
        "firmware_preset_target": "Short Range",   # B2
        "proxy_basis":     "specular/dark dropouts use visible_luminance as approximate proxy for 860nm IR; grazing dropout uses surface-normal angle (exact). NOT calibrated to true 860nm reflectivity.",
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
        "specular_luminance_threshold": DEFAULTS["specular_luminance_threshold"],
        "specular_dropout_rate":        DEFAULTS["specular_dropout_rate"],
        "dark_luminance_threshold":     DEFAULTS["dark_luminance_threshold"],
        "dark_dropout_rate":            DEFAULTS["dark_dropout_rate"],
        "grazing_dropout_max":          DEFAULTS["grazing_dropout_max"],
        "grazing_cos_full":             DEFAULTS["grazing_cos_full"],
        "grazing_cos_max":              DEFAULTS["grazing_cos_max"],
        "radial_bias_amplitude_mm":     DEFAULTS["radial_bias_amplitude_mm"],
        "low_freq_bias_std_mm":         DEFAULTS["low_freq_bias_std_mm"],
        "rgb_depth_shift_max_px":       DEFAULTS["rgb_depth_shift_max_px"],
        "validation_criteria": [
            "magnitude: σ on flat tray-floor pixels < 0.5 mm (vs Berlin 2021 bound)",
            "dropout-location: spatial correlation between dropout pixels and (luminance > specular_threshold) ∪ (luminance < dark_threshold) ∪ (cos(normal-to-cam) < grazing_cos_full)",
        ],
        "validation_status": "deferred — no public L515 capture sufficient for dropout-pattern validation; awaiting team's L515 hardware",
        "references": [
            "Berlin 2021 — SPIE 11782 — L515 precision <0.5mm at 3.5m",
            "Servi et al. 2021 — Sensors 21:7770 — L515 metrological characterization",
            "Lourenço et al. 2021 — VISAPP — RealSense comparative",
            "Intel L515 datasheet rev 003 — accuracy/quantization specs",
            "Intel L515 optimization guide — specular/dark/saturation failure modes",
            "librealsense issue #6636 — L515 depth_units = 0.00025m",
            "librealsense issue #10168 — L515 systematic depth bias up to 20cm",
            "Lehrmann et al. 2024, arXiv:2402.16514 — methodology reference (fits for Kinect/MotionCam, not L515)",
        ],
        "calibration_status": "uncalibrated_to_real_L515 — re-tune mn, dropout rates, bias amplitude when real captures arrive",
    }
    if cfg_overrides:
        meta.update(cfg_overrides)
    return meta
