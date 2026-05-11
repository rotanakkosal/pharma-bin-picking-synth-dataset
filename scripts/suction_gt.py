"""
Suction-point ground truth generation for the pharma-bin synthetic benchmark.

V1 — simplified analytical model based on Dex-Net 3.0 (Mahler et al. 2018,
arXiv:1709.06670), SuctionNet-1Billion (Cao et al. 2021, arXiv:2103.12311),
and Sim-Suction (Li & Cappelleri 2023, arXiv:2305.16378). See
docs/suction_gt/suction_gt_design.md for the full design.

For each placed bottle:
  1. Sample ~200 candidate points on the visible surface (FPS).
  2. Drop candidates failing 4 hard filters (edge clearance, normal alignment,
     visibility, collision-free approach).
  3. Score survivors with two analytical scores: Sseal + Swrench, both [0,1].
  4. Keep top-50 by Sseal*Swrench, attach to the instance dict.
"""
from __future__ import annotations

import math
import numpy as np
from typing import Optional


# ---------------------------------------------------------------------------
# Defaults (cited in docs/suction_gt/suction_gt_design.md §"Default parameters")
# ---------------------------------------------------------------------------
DEFAULTS = {
    "cup_radius_mm":           15.0,    # Sim-Suction default 1.5 cm
    "n_candidates":            200,     # post-FPS subsample target
    "top_k":                   50,      # SuctionNet AP_μ uses k=1..50
    "sigma_seal_mm":           1.0,     # Gaussian falloff scale for Sseal
    "normal_angle_deg":        30.0,    # max tilt for filter F2
    "object_mass_kg":          0.1,     # matches generate_scene.py rigidbody mass
    "atmospheric_pressure_Pa": 101325.0,
    "mu_default":              0.5,
    "mu_sweep":                [0.2, 0.4, 0.6, 0.8, 1.0, 1.2],
    "tau_seal":                0.5,
    "tau_wrench":              0.5,
    "match_tolerance_mm":      5.0,
    "g_accel":                 9.81,
    # V1.5 (2026-05-06) additions — see docs/suction_gt/suction_gt_v1_5_refinements.md
    "r_safety_mm":             5.0,     # margin past cup radius from mask boundary
    "nms_dist_mm":             5.0,     # min spacing between exported top-K points
    "plane_fit_max_pixels":    1000,    # cap dense disc-pixel sampling for runtime
}

GRAVITY_CAM = np.array([0.0, 0.0, 1.0])    # camera +Z = world -Z (top-down)
CAMERA_AXIS = np.array([0.0, 0.0, 1.0])    # camera optical axis


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def backproject(uv: np.ndarray, depth_m: np.ndarray, K: np.ndarray) -> np.ndarray:
    """uv: (N, 2) pixel coords (u,v). Returns (N, 3) points in camera frame (m)."""
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    u = uv[:, 0]
    v = uv[:, 1]
    z = depth_m[v.astype(int), u.astype(int)]
    x = (u - cx) * z / fx
    y = (v - cy) * z / fy
    return np.stack([x, y, z], axis=-1)


def project(points_cam: np.ndarray, K: np.ndarray) -> np.ndarray:
    """points_cam: (N, 3) camera-frame points. Returns (N, 2) pixel coords."""
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    z = points_cam[:, 2]
    u = points_cam[:, 0] * fx / z + cx
    v = points_cam[:, 1] * fy / z + cy
    return np.stack([u, v], axis=-1)


def fps_subsample(points: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    """Farthest-point sampling. Returns indices into `points`."""
    N = len(points)
    if N <= n:
        return np.arange(N)
    rng = np.random.default_rng(seed)
    chosen = [int(rng.integers(N))]
    dists = np.linalg.norm(points - points[chosen[0]], axis=1)
    for _ in range(n - 1):
        idx = int(np.argmax(dists))
        chosen.append(idx)
        new_d = np.linalg.norm(points - points[idx], axis=1)
        dists = np.minimum(dists, new_d)
    return np.array(chosen)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def sample_candidates(
    visible_mask: np.ndarray,
    depth_m: np.ndarray,
    K: np.ndarray,
    n: int,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns:
        uv:          (M, 2) pixel coords of M candidates (M ≤ n)
        points_cam:  (M, 3) corresponding 3D points in camera frame (meters)
    """
    vs, us = np.where(visible_mask > 0)
    if len(vs) == 0:
        return np.zeros((0, 2), dtype=np.int32), np.zeros((0, 3), dtype=np.float64)
    z = depth_m[vs, us]
    valid = z > 0.01    # drop pixels with no depth
    if not np.any(valid):
        return np.zeros((0, 2), dtype=np.int32), np.zeros((0, 3), dtype=np.float64)
    vs, us, z = vs[valid], us[valid], z[valid]
    uv_all = np.stack([us, vs], axis=-1).astype(np.int32)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    pts_all = np.stack([
        (us - cx) * z / fx,
        (vs - cy) * z / fy,
        z,
    ], axis=-1)
    idx = fps_subsample(pts_all, n, seed=seed)
    return uv_all[idx], pts_all[idx]


# ---------------------------------------------------------------------------
# Plane fit (used by filter F2 and Sseal)
# ---------------------------------------------------------------------------
def fit_plane(points: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Least-squares plane fit via SVD.
    Returns:
        normal:        (3,) unit normal, oriented toward camera (n_z < 0)
        residual_rms_m: RMS distance from points to plane (meters)
    """
    if len(points) < 3:
        return np.array([0.0, 0.0, -1.0]), 1e9
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    if normal[2] > 0:
        normal = -normal     # orient toward camera (camera looks +Z)
    residuals = centered @ normal
    rms = float(np.sqrt(np.mean(residuals ** 2)))
    return normal, rms


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------
def cup_pixel_radius(z_m: float, K: np.ndarray, cup_radius_mm: float) -> int:
    """Pixel radius of a cup of `cup_radius_mm` at distance z_m from camera."""
    fx = K[0, 0]
    return int(np.ceil(cup_radius_mm * 1e-3 * fx / max(z_m, 1e-3)))


def disc_pixels(uv: np.ndarray, r_px: int, H: int, W: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (vs, us) integer pixel coords of a disc of radius r_px around uv."""
    u0, v0 = int(uv[0]), int(uv[1])
    u_lo, u_hi = max(0, u0 - r_px), min(W, u0 + r_px + 1)
    v_lo, v_hi = max(0, v0 - r_px), min(H, v0 + r_px + 1)
    uu, vv = np.meshgrid(np.arange(u_lo, u_hi), np.arange(v_lo, v_hi))
    inside = (uu - u0) ** 2 + (vv - v0) ** 2 <= r_px ** 2
    return vv[inside], uu[inside]


def filter_edge_clearance(uv: np.ndarray, r_px: int, this_visible_mask: np.ndarray) -> bool:
    """F1: full cup disc lies inside this instance's visible mask."""
    H, W = this_visible_mask.shape
    vs, us = disc_pixels(uv, r_px, H, W)
    if len(vs) == 0:
        return False
    return bool(np.all(this_visible_mask[vs, us] > 0))


def filter_edge_clearance_with_margin(uv: np.ndarray, eroded_mask: np.ndarray) -> bool:
    """V1.5 F1: cup center at least (r_cup + r_safety) from any mask boundary.
    `eroded_mask` is precomputed once per instance (cv2.erode by r_cup_px + r_safety_px).
    See Frontiers bin-picking review on 'distance from cup center to surface center'."""
    u, v = int(uv[0]), int(uv[1])
    H, W = eroded_mask.shape
    if not (0 <= u < W and 0 <= v < H):
        return False
    return bool(eroded_mask[v, u] > 0)


def build_eroded_mask(visible_mask: np.ndarray, r_total_px: int) -> np.ndarray:
    """Erode the visible mask by a circular kernel of radius r_total_px.
    Imported lazily to keep numpy-only behaviour for offline tests."""
    import cv2
    if r_total_px < 1:
        return visible_mask
    diameter = 2 * r_total_px + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))
    return cv2.erode(visible_mask, kernel)


def filter_collision_free(uv: np.ndarray, r_px: int, all_masks: dict, my_id: int) -> bool:
    """F4: cup disc does not overlap any OTHER instance's visible mask."""
    H, W = next(iter(all_masks.values())).shape
    vs, us = disc_pixels(uv, r_px, H, W)
    if len(vs) == 0:
        return False
    for inst_id, m in all_masks.items():
        if inst_id == my_id:
            continue
        if np.any(m[vs, us] > 0):
            return False
    return True


def filter_normal_alignment(normal: np.ndarray, max_angle_deg: float) -> tuple[bool, float]:
    """F2: angle between surface normal and camera ray < max_angle_deg.
    Returns (passed, angle_deg)."""
    cos_angle = abs(float(np.dot(normal, CAMERA_AXIS)))
    cos_angle = max(-1.0, min(1.0, cos_angle))
    angle_deg = math.degrees(math.acos(cos_angle))
    return angle_deg < max_angle_deg, angle_deg


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------
def compute_sseal(residual_rms_m: float, sigma_seal_mm: float = DEFAULTS["sigma_seal_mm"]) -> float:
    """Sseal: how well the cup would seal at this point. SuctionNet §III.A reduced
    to a Gaussian on the plane-fit residual."""
    residual_mm = residual_rms_m * 1000.0
    return float(np.exp(-residual_mm / sigma_seal_mm))


def compute_swrench_components(
    contact_cam: np.ndarray,
    normal: np.ndarray,
    com_cam: np.ndarray,
    cup_radius_mm: float,
    object_mass_kg: float = DEFAULTS["object_mass_kg"],
    atmospheric_pressure_Pa: float = DEFAULTS["atmospheric_pressure_Pa"],
    mu: float = DEFAULTS["mu_default"],
) -> dict:
    """Closed-form wrench scoring (Dex-Net 3.0 §III.C, simplified).
    Returns components used both for Swrench and for evaluator re-computation."""
    g = DEFAULTS["g_accel"]
    F_grav = object_mass_kg * g

    # Tilt angle: angle between surface normal and gravity direction.
    # Gravity in cam frame is +Z (camera looks down). Outward normal n_z < 0.
    # cos(theta) = (n · -gravity) = -n_z (since gravity_cam = +Z).
    cos_theta = float(-normal[2])
    cos_theta = max(0.0, min(1.0, cos_theta))
    sin_theta = math.sqrt(1.0 - cos_theta ** 2)
    theta_deg = math.degrees(math.acos(cos_theta))

    F_normal = F_grav * cos_theta
    F_lateral = F_grav * sin_theta

    cup_radius_m = cup_radius_mm * 1e-3
    F_vacuum = atmospheric_pressure_Pa * math.pi * cup_radius_m ** 2

    # Torque arm: distance from contact point to COM projection along the
    # surface (perpendicular to the normal).
    delta = com_cam - contact_cam
    delta_along_normal = float(np.dot(delta, normal)) * normal
    delta_in_plane = delta - delta_along_normal
    torque_arm_m = float(np.linalg.norm(delta_in_plane))

    return {
        "F_grav_N":     F_grav,
        "F_normal_N":   F_normal,
        "F_lateral_N":  F_lateral,
        "F_vacuum_N":   F_vacuum,
        "torque_arm_m": torque_arm_m,
        "tilt_deg":     theta_deg,
    }


def fit_plane_dense(uv: np.ndarray, r_px: int, depth_m: np.ndarray, K: np.ndarray,
                    visible_mask: np.ndarray,
                    max_pixels: int = DEFAULTS["plane_fit_max_pixels"],
                    rng: Optional[np.random.Generator] = None
                    ) -> tuple[np.ndarray, float, int]:
    """V1.5 dense plane fit: sample every depth pixel within the cup-disc footprint
    that ALSO lies inside the bottle's visible mask. Back-project to 3D camera frame
    and fit a plane. Returns (normal, residual_rms_m, n_points_used).

    See docs/suction_gt/suction_gt_v1_5_refinements.md §"Change 1". Replaces the V1 sparse FPS-cloud
    plane fit which couldn't detect cap-body discontinuities."""
    H, W = depth_m.shape
    vs, us = disc_pixels(uv, r_px, H, W)
    if len(vs) == 0:
        return np.array([0.0, 0.0, -1.0]), 1e9, 0
    in_mask = visible_mask[vs, us] > 0
    vs, us = vs[in_mask], us[in_mask]
    if len(vs) < 3:
        return np.array([0.0, 0.0, -1.0]), 1e9, len(vs)
    z = depth_m[vs, us]
    valid = z > 0.01
    vs, us, z = vs[valid], us[valid], z[valid]
    if len(vs) < 3:
        return np.array([0.0, 0.0, -1.0]), 1e9, len(vs)
    if len(vs) > max_pixels:
        if rng is None:
            rng = np.random.default_rng(0)
        idx = rng.choice(len(vs), size=max_pixels, replace=False)
        vs, us, z = vs[idx], us[idx], z[idx]
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    pts = np.stack([
        (us - cx) * z / fx,
        (vs - cy) * z / fy,
        z,
    ], axis=-1)
    normal, residual_rms = fit_plane(pts)
    return normal, residual_rms, len(pts)


def nms_top_k(candidates: list, nms_dist_mm: float, k: int) -> list:
    """V1.5 non-maximum suppression on suction candidates.
    Greedy: pick highest score, suppress all within nms_dist_mm. Repeat until k kept.

    Citation: SuctionNet §V (NMS before evaluation), GraspNet evaluation page (translation-distance NMS).
    Threshold matches our match_tolerance_mm so suppressed points would have all matched
    the same predictions anyway."""
    if not candidates:
        return []
    nms_dist_m = nms_dist_mm * 1e-3
    sorted_cands = sorted(candidates, key=lambda d: d["S_combined_default"], reverse=True)
    kept: list = []
    kept_pts = np.zeros((0, 3), dtype=np.float64)
    for c in sorted_cands:
        if len(kept) >= k:
            break
        c_pos = np.array(c["point_3d_cam"])
        if len(kept_pts) > 0:
            dists = np.linalg.norm(kept_pts - c_pos, axis=1)
            if np.any(dists < nms_dist_m):
                continue
        kept.append(c)
        kept_pts = np.vstack([kept_pts, c_pos[None, :]])
    return kept


def compute_swrench(comps: dict, cup_radius_mm: float, mu: float) -> float:
    """Combine wrench components into Swrench score in [0, 1]."""
    F_lat_max = mu * comps["F_vacuum_N"]
    if F_lat_max <= 0:
        return 0.0
    force_term = math.exp(-comps["F_lateral_N"] / F_lat_max)
    arm_term = math.exp(-comps["torque_arm_m"] * 1000.0 / cup_radius_mm)
    return float(force_term * arm_term)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def compute_suction_gt(
    placed_bottles: list,
    visible_masks: dict,             # {instance_id: HxW uint8 (0 or 255)}
    depth_m: np.ndarray,             # HxW float32, meters
    camera_K: np.ndarray,            # 3x3 intrinsics
    cup_radius_mm: float = DEFAULTS["cup_radius_mm"],
    n_candidates: int = DEFAULTS["n_candidates"],
    top_k: int = DEFAULTS["top_k"],
    object_mass_kg: float = DEFAULTS["object_mass_kg"],
    mu: float = DEFAULTS["mu_default"],
    r_safety_mm: float = DEFAULTS["r_safety_mm"],
    nms_dist_mm: float = DEFAULTS["nms_dist_mm"],
    seed: int = 0,
) -> dict:
    """Returns {instance_id: [point_dict, ...]} sorted by S_combined desc.
    V1.5 (2026-05-06): dense plane fit, margin-aware F1, NMS top-K."""
    H, W = depth_m.shape
    out = {}
    rng = np.random.default_rng(seed)

    # Precompute COM (camera frame) per instance: image centroid + median depth.
    coms = {}
    for inst_id, mask in visible_masks.items():
        vs, us = np.where(mask > 0)
        if len(vs) == 0:
            coms[inst_id] = None
            continue
        u_med, v_med = float(np.mean(us)), float(np.mean(vs))
        z_med = float(np.median(depth_m[vs, us]))
        if z_med <= 0:
            coms[inst_id] = None
            continue
        fx, fy = camera_K[0, 0], camera_K[1, 1]
        cx, cy = camera_K[0, 2], camera_K[1, 2]
        coms[inst_id] = np.array([
            (u_med - cx) * z_med / fx,
            (v_med - cy) * z_med / fy,
            z_med,
        ])

    for inst_id, mask in visible_masks.items():
        com = coms[inst_id]
        if com is None:
            out[int(inst_id)] = []
            continue

        uv_cands, pts_cands = sample_candidates(mask, depth_m, camera_K, n_candidates, seed=seed)
        if len(pts_cands) == 0:
            out[int(inst_id)] = []
            continue

        # V1.5: precompute eroded mask for margin-aware edge clearance.
        # r_total_px is the typical cup-pixel-radius at the instance median depth
        # plus the safety margin in pixels. We use one erosion per instance (median
        # depth) rather than per-candidate (depth varies <5% across one bottle).
        z_med_inst = float(np.median(depth_m[mask > 0]))
        r_cup_px_typ = cup_pixel_radius(z_med_inst, camera_K, cup_radius_mm)
        r_safety_px_typ = cup_pixel_radius(z_med_inst, camera_K, r_safety_mm)
        eroded_mask = build_eroded_mask(mask, r_cup_px_typ + r_safety_px_typ)

        kept = []
        for i, (uv, p) in enumerate(zip(uv_cands, pts_cands)):
            r_px = cup_pixel_radius(p[2], camera_K, cup_radius_mm)
            if r_px < 1:
                continue

            # F1 (V1.5) — margin-aware edge clearance
            if not filter_edge_clearance_with_margin(uv, eroded_mask):
                continue

            # F4 — collision-free approach
            if not filter_collision_free(uv, r_px, visible_masks, inst_id):
                continue

            # V1.5: dense plane fit on every depth pixel within the cup-disc
            # footprint (replaces V1's sparse FPS-cloud fit which missed step
            # discontinuities like cap-body junctions).
            normal, residual_rms, n_pts = fit_plane_dense(
                uv, r_px, depth_m, camera_K, mask, rng=rng
            )
            if n_pts < 3:
                continue

            # F2 — normal alignment
            ok_n, normal_angle_deg = filter_normal_alignment(
                normal, DEFAULTS["normal_angle_deg"])
            if not ok_n:
                continue

            # F3 — visibility (implicit: candidates are sampled from depth on
            # visible_mask, so the candidate IS the closest surface).

            sseal = compute_sseal(residual_rms)
            comps = compute_swrench_components(p, normal, com, cup_radius_mm,
                                               object_mass_kg=object_mass_kg, mu=mu)
            swrench = compute_swrench(comps, cup_radius_mm, mu)
            s_combined = sseal * swrench

            kept.append({
                "point_3d_cam":         [round(float(p[0]), 6),
                                         round(float(p[1]), 6),
                                         round(float(p[2]), 6)],
                "point_2d_px":          [int(uv[0]), int(uv[1])],
                "normal_cam":           [round(float(normal[0]), 6),
                                         round(float(normal[1]), 6),
                                         round(float(normal[2]), 6)],
                "Sseal":                round(sseal, 4),
                "Swrench_default":      round(swrench, 4),
                "S_combined_default":   round(s_combined, 4),
                "lateral_force_N":      round(comps["F_lateral_N"], 4),
                "normal_force_N":       round(comps["F_normal_N"], 4),
                "vacuum_force_N":       round(comps["F_vacuum_N"], 4),
                "torque_arm_mm":        round(comps["torque_arm_m"] * 1000.0, 3),
                "flatness_residual_mm": round(residual_rms * 1000.0, 3),
                "normal_angle_deg":     round(normal_angle_deg, 2),
                "tilt_deg":             round(comps["tilt_deg"], 2),
            })

        # V1.5: NMS to enforce spatial diversity in the top-K export
        out[int(inst_id)] = nms_top_k(kept, nms_dist_mm, top_k)

    return out


def make_suction_meta(cfg_overrides: Optional[dict] = None) -> dict:
    """Self-describing metadata block embedded in scene_gt.json."""
    meta = {
        "version":              "v1.5",
        "cup_radius_mm":        DEFAULTS["cup_radius_mm"],
        "r_safety_mm":          DEFAULTS["r_safety_mm"],
        "nms_dist_mm":          DEFAULTS["nms_dist_mm"],
        "plane_fit_dense":      True,
        "mu_default":           DEFAULTS["mu_default"],
        "mu_sweep":             DEFAULTS["mu_sweep"],
        "tau_seal":             DEFAULTS["tau_seal"],
        "tau_wrench":           DEFAULTS["tau_wrench"],
        "match_tolerance_mm":   DEFAULTS["match_tolerance_mm"],
        "object_mass_kg":       DEFAULTS["object_mass_kg"],
        "atmospheric_pressure_Pa": DEFAULTS["atmospheric_pressure_Pa"],
        "n_candidates_per_instance": DEFAULTS["n_candidates"],
        "top_k_per_instance":   DEFAULTS["top_k"],
        "filters_applied":      ["edge_clearance", "normal_alignment", "visibility", "collision_free"],
        "scoring":              "Sseal: exp(-residual_mm/sigma_seal_mm); Swrench: exp(-F_lat/(mu*F_vac))*exp(-tau_arm_mm/cup_radius_mm)",
        "references": [
            "Mahler et al. 2018, Dex-Net 3.0, arXiv:1709.06670",
            "Cao et al. 2021, SuctionNet-1Billion, arXiv:2103.12311",
            "Li & Cappelleri 2023, Sim-Suction, arXiv:2305.16378",
        ],
    }
    if cfg_overrides:
        meta.update(cfg_overrides)
    return meta
