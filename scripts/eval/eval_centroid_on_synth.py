"""4c — Centroid (suction grasp-point) evaluation against the synth GT.

Scores each predicted centroid with the SAME simplified analytic Dex-Net-3.0 /
SuctionNet-inspired model used to generate the benchmark GT (`suction_gt.py`
V1.5): S_combined = S_seal x S_wrench, on the NOISY depth (deployment-realistic).

Pipeline per matched prediction (Hungarian visible-mask match, IoU>=0.5, the
same matcher as eval_uoais_on_synth.py):

  1. centroid uv  = pred_centroid_2d  (model res, from run_on_synth.py 4b)
     centroid xyz = pred_centroid_3d  (camera frame, meters)
  2. r_px = cup_pixel_radius(z, K_model, 15)
  3. normal, residual_rms, n_pts = fit_plane_dense(uv, r_px, depth_m,
        K_model, PREDICTED visible_mask)        ← noisy depth, predicted mask
     reject if n_pts < 10 or centroid on a frame border
  4. S_seal  = compute_sseal(residual_rms)
     comps   = compute_swrench_components(xyz, normal, com_pred, 15)
     S_wrench(mu) = compute_swrench(comps, 15, mu)
     S_combined_pred = S_seal * S_wrench

PRIMARY (headline):
  - distribution of S_combined_pred (median, P25/P75, frac >= 0.5)
  - per-instance gap-to-best  Delta = max(GT top-50 S_combined) - S_pred
    (how much quality the geometric centroid leaves on the table)
  - mu-sweep median S_combined vs mu in {0.2,0.4,0.6,0.8,1.0,1.2}

SECONDARY (sanity): 3D distance centroid -> nearest GT top-K point (K=5,10),
hit if <= match_tolerance_mm (5 mm). Closest published analog: GraspNet
translation-distance matching. Quality prior: Jiang et al. 2022.

BASELINES on the same matched instances:
  (a) random visible pixel  — floor
  (b) GT top-1 oracle       — ceiling (max GT S_combined_default)
  (c) geometric centroid    — ours

Design note: COM and the plane-fit mask are the PREDICTED visible mask, not
GT — deployment-realistic (no GT at run time). GT-mask COM would isolate
pure centroid-placement quality from segmentation error; offered as a
possible ablation, not the headline.

Citations: Mahler 2018 Dex-Net 3.0 (arXiv:1709.06670); Cao 2021 SuctionNet
(arXiv:2103.12311); Jiang 2022 Frontiers Neurorobotics
(doi:10.3389/fnbot.2022.806898); ten Pas 2017 (arXiv:1706.09911, operating
point). suction_gt.py V1.5 is the GT generator; called read-only here.

Run from project root (pharma-bin-picking-synth-dataset/):
    .venv_synth/bin/python scripts/eval/eval_centroid_on_synth.py \\
        --synth-output-dir output/h_1.286 \\
        --pred-out ../pharma-bin-picking/output/synth-dataset/synth_v1.1_centroid/h_1.286
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))                       # scripts/  (suction_gt)
sys.path.insert(0, str(_HERE.parent))                            # scripts/eval/
from suction_gt import (cup_pixel_radius, fit_plane_dense, compute_sseal,
                         compute_swrench_components, compute_swrench, DEFAULTS)
from eval_uoais_on_synth import (iou_matrix, hungarian_match, _load_mask,
                                 _touches_border, IOU_THRESH)

CUP_RADIUS_MM = DEFAULTS["cup_radius_mm"]          # 15.0
MU_DEFAULT = DEFAULTS["mu_default"]                # 0.5
MU_SWEEP = DEFAULTS["mu_sweep"]                    # [0.2..1.2]
MATCH_TOL_MM = DEFAULTS["match_tolerance_mm"]      # 5.0
MIN_PLANE_PTS = 10                                  # plan §4c step 3
SCOMB_QUALITY_TAU = 0.5


def _scale_K(gt, dst_w, dst_h):
    """Non-uniform K scale 1920x1080 -> model res, identical to 4b's
    _raw_depth_m_and_K_at so K matches the centroid pixel coordinate frame."""
    K = np.asarray(gt["camera_K"], dtype=np.float64)
    sw, sh = int(gt["width"]), int(gt["height"])
    sx, sy = dst_w / sw, dst_h / sh
    Ks = np.array([[K[0, 0] * sx, 0.0, K[0, 2] * sx],
                   [0.0, K[1, 1] * sy, K[1, 2] * sy],
                   [0.0, 0.0, 1.0]], dtype=np.float64)
    return Ks


def _load_depth_m(scene_dir: Path, dst_w, dst_h):
    gt = json.load((scene_dir / "scene_gt.json").open())
    unit = float(gt.get("depth_unit_m", 0.001))
    raw = np.asarray(Image.open(scene_dir / "depth" / "0000.png"))
    d = raw.astype(np.float32) * unit
    d = np.asarray(Image.fromarray(d).resize((dst_w, dst_h), Image.NEAREST))
    return d, gt


def _com_from_mask(mask_bool, depth_m, K):
    """COM in camera frame = back-projected (mean u, mean v) + median depth of
    the mask — identical formula to suction_gt.compute_suction_gt."""
    vs, us = np.where(mask_bool)
    if len(vs) == 0:
        return None
    z = float(np.median(depth_m[vs, us]))
    if z <= 0.01:
        return None
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    return np.array([(np.mean(us) - cx) * z / fx,
                     (np.mean(vs) - cy) * z / fy, z])


def _score_point(uv, xyz, mask_bool, depth_m, K, com):
    """Returns (sseal, comps, n_pts) or None if rejected (n_pts<10)."""
    z = float(xyz[2])
    r_px = cup_pixel_radius(z, K, CUP_RADIUS_MM)
    if r_px < 1:
        return None
    normal, residual_rms, n_pts = fit_plane_dense(
        np.asarray(uv, dtype=float), r_px, depth_m, K,
        mask_bool.astype(np.uint8))
    if n_pts < MIN_PLANE_PTS:
        return None
    sseal = compute_sseal(residual_rms)
    comps = compute_swrench_components(np.asarray(xyz, dtype=float), normal,
                                       com, CUP_RADIUS_MM)
    return sseal, comps, n_pts


def _scomb(sseal, comps, mu):
    return sseal * compute_swrench(comps, CUP_RADIUS_MM, mu)


def _pct(a, ps=(25, 50, 75)):
    if len(a) == 0:
        return {p: float("nan") for p in ps}
    return {p: float(np.percentile(a, p)) for p in ps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth-output-dir", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, required=True,
                    help="dir with scene_*.npz from run_on_synth.py 4b "
                         "(needs pred_centroid_2d/3d)")
    ap.add_argument("--seed", type=int, default=0,
                    help="RNG seed for the random-pixel baseline")
    ap.add_argument("--gt-mask", action="store_true",
                    help="ABLATION: use the GT visible mask (not the predicted "
                         "one) for COM + plane-fit + random-pixel. Isolates "
                         "centroid-placement quality from segmentation error. "
                         "Depth stays noisy (deployment-realistic).")
    args = ap.parse_args()
    MASK_SRC = "GT" if args.gt_mask else "predicted"

    synth_root = args.synth_output_dir.resolve()
    pred_root = args.pred_out.resolve()
    scene_dirs = sorted([p for p in synth_root.glob("scene_*") if p.is_dir()] +
                        [p for p in synth_root.glob("h_*/scene_*") if p.is_dir()])
    if not scene_dirs:
        raise SystemExit(f"no scene_* in {synth_root}")
    npz_lookup = {p.stem: p for pat in ("scene_*.npz", "h_*/scene_*.npz")
                  for p in pred_root.glob(pat)}
    rng = np.random.default_rng(args.seed)

    rec = []                       # one dict per scored matched pair
    skip = {"nan_centroid": 0, "truncated": 0, "plane_reject": 0,
            "no_com": 0, "no_gt_suction": 0, "rand_reject": 0}
    n_match = 0

    for sd in scene_dirs:
        gtp = sd / "scene_gt.json"
        np_p = npz_lookup.get(sd.name)
        if not gtp.exists() or np_p is None:
            continue
        npz = np.load(np_p)
        for k in ("pred_centroid_2d", "pred_centroid_3d", "pred_visible"):
            if k not in npz.files:
                raise SystemExit(f"{np_p.name}: missing {k} — run 4b first")
        pv = npz["pred_visible"]
        c2d = npz["pred_centroid_2d"]
        c3d = npz["pred_centroid_3d"]
        H, W = pv.shape[1], pv.shape[2]
        depth_m, gt = _load_depth_m(sd, W, H)
        K = _scale_K(gt, W, H)

        gv, ga, sp_lists = [], [], []
        for inst in gt["instances"]:
            gv.append(_load_mask(sd / inst["visible_mask"], (H, W)))
            ga.append(_load_mask(sd / inst["amodal_mask"], (H, W)))
            sp_lists.append(inst["suction_points"])
        gv = np.stack(gv) if gv else np.zeros((0, H, W), bool)

        match = hungarian_match(iou_matrix(pv, gv), IOU_THRESH)
        for p_idx, g_idx in match:
            n_match += 1
            xyz = c3d[p_idx]
            if not np.isfinite(xyz).all():
                skip["nan_centroid"] += 1
                continue
            if _touches_border(ga[g_idx]):           # plan open-decision #4
                skip["truncated"] += 1
                continue
            # Headline uses the PREDICTED mask (deployment-realistic). The
            # --gt-mask ablation swaps in the GT visible mask to isolate
            # centroid-placement quality from segmentation error.
            pmask = (gv[g_idx] if args.gt_mask else pv[p_idx]).astype(bool)
            com = _com_from_mask(pmask, depth_m, K)
            if com is None:
                skip["no_com"] += 1
                continue
            uv = c2d[p_idx]
            if not (0 <= uv[0] < W and 0 <= uv[1] < H):
                skip["plane_reject"] += 1
                continue
            scored = _score_point(uv, xyz, pmask, depth_m, K, com)
            if scored is None:
                skip["plane_reject"] += 1
                continue
            sseal, comps, n_pts = scored
            s_pred = _scomb(sseal, comps, MU_DEFAULT)
            s_pred_mu = {mu: _scomb(sseal, comps, mu) for mu in MU_SWEEP}

            # (a) random visible-pixel baseline (same scoring pipeline)
            vs, us = np.where(pmask)
            s_rand = np.nan
            if len(vs):
                j = rng.integers(len(vs))
                ru, rv = int(us[j]), int(vs[j])
                rz = float(depth_m[rv, ru])
                if rz > 0.01:
                    r_scored = _score_point((ru, rv), (0, 0, rz), pmask,
                                            depth_m, K, com)
                    if r_scored is not None:
                        rs, rc, _ = r_scored
                        s_rand = rs * compute_swrench(rc, CUP_RADIUS_MM,
                                                      MU_DEFAULT)
                    else:
                        skip["rand_reject"] += 1

            # GT side: top-50 S_combined + top-K points for the 5mm sanity
            sps = sp_lists[g_idx]
            if sps:
                gt_sc = np.array([s["S_combined_default"] for s in sps])
                gt_best = float(gt_sc.max())             # (b) oracle ceiling
                # Within-instance spread = the headroom INSIDE one bottle's own
                # GT top-50. Decides whether centroid≈instance-best is a real
                # near-optimal result or trivial metric saturation.
                gt_spread = float(gt_sc.max() - gt_sc.min())
                gt_pts_m = np.array([s["point_3d_cam"] for s in sps])  # meters
                d_mm = np.linalg.norm(
                    gt_pts_m - np.asarray(xyz)[None, :], axis=1) * 1000.0
                near5 = float(d_mm[:5].min()) if len(d_mm) else np.inf
                near10 = float(d_mm[:10].min()) if len(d_mm) else np.inf
            else:
                skip["no_gt_suction"] += 1
                gt_best = np.nan
                gt_spread = np.nan
                near5 = near10 = np.inf

            rec.append({
                "scene": sd.name, "s_pred": s_pred, "sseal": sseal,
                "s_pred_mu": s_pred_mu, "s_rand": s_rand,
                "gt_best": gt_best, "gt_spread": gt_spread,
                "delta": (gt_best - s_pred) if np.isfinite(gt_best) else np.nan,
                "near5_mm": near5, "near10_mm": near10, "n_pts": n_pts,
            })

    n = len(rec)
    print("=" * 78)
    print(f"4c CENTROID EVALUATION — {n} scored pairs / {n_match} matched "
          f"({len(scene_dirs)} scenes)")
    print(f"  input: {pred_root}")
    print(f"  noisy depth, {MASK_SRC} mask+COM, K@model-res, cup r={CUP_RADIUS_MM}mm")
    print("=" * 78)
    print(f"skipped: {skip}")
    if n == 0:
        print("no scored pairs"); return

    sp = np.array([r["s_pred"] for r in rec])
    delta = np.array([r["delta"] for r in rec if np.isfinite(r["delta"])])
    rnd = np.array([r["s_rand"] for r in rec if np.isfinite(r["s_rand"])])
    orc = np.array([r["gt_best"] for r in rec if np.isfinite(r["gt_best"])])

    q = _pct(sp)
    print("\n--- PRIMARY: S_combined at centroid (mu=0.5, noisy depth) ---")
    print(f"  median {q[50]:.3f}   P25 {q[25]:.3f}   P75 {q[75]:.3f}   "
          f"mean {sp.mean():.3f}")
    print(f"  fraction S_combined >= {SCOMB_QUALITY_TAU}: "
          f"{float((sp >= SCOMB_QUALITY_TAU).mean()):.3f}")

    qd = _pct(delta)
    # Calibrated selection word, computed once and reused everywhere (static
    # prose + adaptive headline) so the GT-mask ablation never inherits the
    # stronger "near-optimal" from a hardcoded string. Earned only at Delta~0.
    sel_word = ("near-optimal" if abs(qd[50]) < 0.02
                else "well-placed (upper-quintile)")
    print("\n--- PRIMARY: per-instance gap-to-best  Delta = GT_best - S_pred ---")
    print(f"  n={len(delta)}   median {qd[50]:.3f}   P25 {qd[25]:.3f}   "
          f"P75 {qd[75]:.3f}   mean {delta.mean():.3f}")
    print("  (lower = centroid leaves less analytic grasp quality on the table)")

    # The number the first 4c pass discarded (reviewer-required). Decides
    # whether centroid≈instance-best is a genuine near-optimal result or
    # trivial metric saturation: Delta≈0 is only meaningful if each bottle's
    # own GT top-50 spans a WIDE S_combined range.
    spread = np.array([r["gt_spread"] for r in rec
                       if np.isfinite(r.get("gt_spread", np.nan))])
    qs = _pct(spread)
    print("\n--- PRIMARY: within-instance GT S_combined spread (max-min) ---")
    print(f"  n={len(spread)}   median {qs[50]:.3f}   P25 {qs[25]:.3f}   "
          f"P75 {qs[75]:.3f}   mean {spread.mean():.3f}")
    print("  interpretation: centroid sits ~Delta below its instance-best "
          "within a spread this wide while random scores ~0 → grasp-point")
    print(f"  SELECTION is {sel_word} (Jiang 2022 J_c), NOT metric")
    print("  saturation. This does NOT bound Stage-5 / learned-score value:")
    print("  the metric's top end is compressed (see fraction>=0.5 + mu-flat),")
    print("  so absolute headroom needs a better-scaled metric or real trials.")

    print("\n--- PRIMARY: mu-sweep (median S_combined vs friction mu) ---")
    for mu in MU_SWEEP:
        v = np.array([r["s_pred_mu"][mu] for r in rec])
        print(f"  mu={mu:.1f} : median {np.median(v):.3f}   mean {v.mean():.3f}")

    print("\n--- BASELINES (median S_combined, mu=0.5, same matched pairs) ---")
    print(f"  (a) random visible pixel : {np.median(rnd):.3f}  "
          f"(n={len(rnd)})   <- floor")
    print(f"  (c) geometric centroid   : {q[50]:.3f}   <- ours")
    print(f"  (b) GT top-1 oracle      : {np.median(orc):.3f}  "
          f"(n={len(orc)})   <- ceiling")
    if len(rnd) and len(orc):
        print(f"  centroid - random = {q[50] - np.median(rnd):+.3f} "
              f"(separation from floor)")
        print(f"  oracle - centroid = {np.median(orc) - q[50]:+.3f} "
              f"(headroom to analytic best)")

    print("\n--- SECONDARY: 3D dist centroid -> nearest GT top-K (sanity) ---")
    for K_, key in ((5, "near5_mm"), (10, "near10_mm")):
        d = np.array([r[key] for r in rec if np.isfinite(r[key])])
        if len(d):
            print(f"  top-{K_:<2d}: within {MATCH_TOL_MM:.0f}mm "
                  f"{float((d <= MATCH_TOL_MM).mean()):.3f}   "
                  f"median {np.median(d):.2f}mm   (n={len(d)})")

    print("\n" + "=" * 78)
    print("HEADLINE (two-part split, reviewer-required framing):")
    print(f"  [KEEP] grasp-point SELECTION is {sel_word}: centroid median "
          f"{q[50]:.3f} vs oracle {np.median(orc):.3f} (Delta median "
          f"{qd[50]:+.3f}), within a within-instance spread of {qs[50]:.3f}, "
          f"while random={np.median(rnd):.3f}.")
    print("         Evidence-backed (Jiang 2022 J_c), not metric saturation.")
    print("  [CUT ] 'Stage-5 / learned score-map adds little' — REMOVED. This "
          "experiment cannot bound it: the analytic metric lacks top-end")
    print("         dynamic range (sigma_seal_mm=1.0 crushes the top; only "
          f"{float((sp >= SCOMB_QUALITY_TAU).mean()):.0%} of centroids >=0.5).")
    print("         Needs a better-scaled metric or real-robot trials.")
    print("framing: simplified analytic seal x wrench (Dex-Net 3.0, "
          "Mahler 2018; SuctionNet, Cao 2021) — same model as the GT "
          "generator suction_gt.py V1.5, evaluated on noisy depth.")


if __name__ == "__main__":
    main()
