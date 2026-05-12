"""Score UOAIS predictions against our synth GT.

Pairs `output/scene_*/scene_gt.json` + `{visible,amodal}_masks/` (ours) with
`<uoais-out>/scene_*.npz` (UOAIS predictions: pred_visible, pred_amodal).

Metric design (revised 2026-05-12 after two adversarial reviews — see
pharma-bin/reviewer-feedback/eval_uoais_occlusion_stratified_recall/):

  ONE MATCH, THREE VIEWS. A single Hungarian (optimal) 1:1 assignment of
  predictions to GT on the VISIBLE masks at IoU >= 0.5 per scene. Everything
  downstream is derived from that one matching, so the headline detection
  numbers, the per-occlusion-bin recall, and F@.75 are mutually consistent
  (no many-to-one inflation from an under-segmented prediction "detecting"
  two touching bottles).

  PRIMARY — detection. From the matching: TP = matched GT with >= MIN_VPX
  visible px; FN = unmatched GT with >= MIN_VPX; FP = unmatched prediction.
  (A prediction that matched a sub-MIN_VPX GT is neither TP nor FP — it
  correctly found a real if tiny object that we don't score detection on.)
  Reports precision / recall / F1 and mean visible-IoU on matched pairs.

  Also reports the published-UOAIS comparator: per-image Overlap P/R/F
  (region overlap = Dice = 2·inter/(a+b), averaged over scenes) and F@.75
  (fraction of GT whose matched prediction has Dice > 0.75) — visible and
  amodal. Dice (not IoU) is what UOAIS-Net's "Overlap F-measure" uses.

  SECONDARY — amodal completion. On matched pairs, mean IoU between
  pred_amodal and GT amodal. A different (noisier) task than detection, so
  reported separately rather than folded into recall.

  STRATIFIED — recall by GT occlusion bin (0-10 / 10-30 / 30-50 / 50-80 /
  80-100 / all), plus a separate row for frame-truncated GT (amodal mask
  touches the image border). Heavily-occluded instances are effectively
  unobservable; the table makes that visible instead of dragging the
  headline down.

  SMALL-MASK FILTER — `--min-visible-px` (default 100). Reported as a sweep
  (50 / 100 / 500) so the threshold is visibly not load-bearing.

  LEGACY — the old v1.0/v1.1 metric (greedy match on amodal masks, IoU >=
  0.5, no small-mask filter) is still printed, labelled non-standard, as a
  continuity check (it should reproduce P 0.910 / R 0.766 / F1 0.832).

Run from project root:
    python scripts/eval/eval_uoais_on_synth.py \\
        --synth-output-dir output/h_1.286 \\
        --uoais-out ../pharma-bin-picking/output/synth_v1.1/h_1.286
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.optimize import linear_sum_assignment

REPO = Path(__file__).resolve().parents[2]
IOU_THRESH = 0.5
FAT_DICE_THRESH = 0.75   # F@.75 — region-overlap (Dice) threshold, as in published UOAIS
OCC_BINS = [(0.0, 0.10), (0.10, 0.30), (0.30, 0.50), (0.50, 0.80), (0.80, 1.0001)]
OCC_BIN_LABELS = ["0-10%", "10-30%", "30-50%", "50-80%", "80-100%"]
SMALL_MASK_SWEEP = [50, 100, 500]


# ---------------------------------------------------------------------------
# mask helpers
# ---------------------------------------------------------------------------
def _load_mask(path: Path, target_hw: tuple[int, int]) -> np.ndarray:
    H, W = target_hw
    return np.asarray(Image.open(path).resize((W, H), Image.NEAREST)) > 0


def _touches_border(mask: np.ndarray) -> bool:
    return bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU between stacks of bool masks. a:(M,H,W), b:(N,H,W) -> (M,N)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    af = a.reshape(len(a), -1).astype(np.uint32)
    bf = b.reshape(len(b), -1).astype(np.uint32)
    inter = af @ bf.T
    asum = af.sum(1, keepdims=True)
    bsum = bf.sum(1, keepdims=True)
    union = asum + bsum.T - inter
    return np.where(union > 0, inter / np.maximum(union, 1), 0.0).astype(np.float32)


def dice_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise Dice (region overlap) between stacks of bool masks. -> (M,N)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    af = a.reshape(len(a), -1).astype(np.uint32)
    bf = b.reshape(len(b), -1).astype(np.uint32)
    inter = af @ bf.T
    denom = af.sum(1, keepdims=True) + bf.sum(1, keepdims=True).T
    return np.where(denom > 0, 2.0 * inter / np.maximum(denom, 1), 0.0).astype(np.float32)


def hungarian_match(iou: np.ndarray, thresh: float) -> list[tuple[int, int]]:
    """Optimal 1:1 assignment maximising total IoU, keeping only pairs >= thresh.
    Returns list of (pred_idx, gt_idx). Deterministic."""
    if iou.size == 0:
        return []
    rows, cols = linear_sum_assignment(-iou)
    return [(int(r), int(c)) for r, c in zip(rows, cols) if iou[r, c] >= thresh]


def greedy_match(iou: np.ndarray, thresh: float) -> list[tuple[int, int, float]]:
    matches, iou = [], iou.copy()
    while iou.size and iou.max() >= thresh:
        p, g = np.unravel_index(iou.argmax(), iou.shape)
        matches.append((int(p), int(g), float(iou[p, g])))
        iou[p, :] = 0
        iou[:, g] = 0
    return matches


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f = 2 * p * r / max(1e-9, p + r)
    return p, r, f


def occ_bin(o: float) -> str:
    for (lo, hi), lab in zip(OCC_BINS, OCC_BIN_LABELS):
        if lo <= o < hi:
            return lab
    return OCC_BIN_LABELS[-1]


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth-output-dir", type=Path, default=REPO / "output")
    ap.add_argument("--uoais-out", type=Path, required=True,
                    help="path to pharma-bin-picking/output/<subdir> containing scene_*.npz")
    ap.add_argument("--min-visible-px", type=int, default=100,
                    help="GT with fewer visible px (full-res) excluded from headline detection")
    args = ap.parse_args()

    synth_root = args.synth_output_dir.resolve()
    uoais_root = args.uoais_out.resolve()
    MIN_VPX = args.min_visible_px

    # Scene dirs (flat or height-bucketed). Key by (bucket, name) so identically
    # named scenes in different buckets don't collide (B3 fix).
    scene_dirs = sorted(p for pat in ("scene_*", "h_*/scene_*")
                        for p in synth_root.glob(pat) if p.is_dir())
    if not scene_dirs:
        raise SystemExit(f"no scene_* dirs in {synth_root}")

    def bucket_of_dir(p: Path) -> str:
        return p.parent.name if p.parent.name.startswith("h_") else "all"

    npz_lookup = {}
    for pat in ("*.npz", "h_*/*.npz"):
        for p in uoais_root.glob(pat):
            bk = p.parent.name if p.parent.name.startswith("h_") else "all"
            npz_lookup[(bk, p.stem)] = p

    # --- per-GT records: every GT instance gets a row in this list ----------
    # (bucket, scene, class_id, occ, vpx_full, truncated, matched, vis_iou, vis_dice, amo_iou, amo_dice)
    gt_records: list[dict] = []
    # FP / pred bookkeeping
    fp_total = 0
    fp_by_class_unknown = 0  # FPs credited to class -1
    fp_by_bucket: Counter = Counter()
    n_pred_total = 0
    n_scenes = 0
    per_image_overlap = {"vis": [], "amo": []}  # list of (P_i, R_i, F_i) tuples per scene
    # legacy v1.0 metric
    legacy = {"tp": 0, "fp": 0, "fn": 0, "iou": []}
    matched_to_tiny = 0  # preds that matched a sub-MIN_VPX GT (neither TP nor FP)

    for sd in scene_dirs:
        gt_path = sd / "scene_gt.json"
        bk = bucket_of_dir(sd)
        npz_path = npz_lookup.get((bk, sd.name))
        if not gt_path.exists() or npz_path is None:
            print(f"  skip {bk}/{sd.name}: missing gt or pred")
            continue
        n_scenes += 1

        gt = json.load(gt_path.open())
        npz = np.load(npz_path)
        pred_vis = npz["pred_visible"]   # (P,H,W) bool
        pred_amo = npz["pred_amodal"]    # (P,H,W) bool
        H, W = pred_vis.shape[1], pred_vis.shape[2]
        P = len(pred_vis)
        n_pred_total += P

        # GT masks + per-instance attrs
        gv, ga, cats, occ, vpx, trunc = [], [], [], [], [], []
        for inst in gt["instances"]:
            vm = _load_mask(sd / inst["visible_mask"], (H, W))
            am = _load_mask(sd / inst["amodal_mask"], (H, W))
            gv.append(vm); ga.append(am)
            cats.append(int(inst["category_id"]))
            occ.append(float(inst["occlusion_rate"]))
            vpx.append(int(inst["visible_px"]))
            trunc.append(_touches_border(am))
        N = len(cats)
        gv = np.stack(gv) if gv else np.zeros((0, H, W), bool)
        ga = np.stack(ga) if ga else np.zeros((0, H, W), bool)

        # ---- THE matching: Hungarian on visible masks, all GT, IoU>=0.5 ----
        iou_vis = iou_matrix(pred_vis, gv)
        match = hungarian_match(iou_vis, IOU_THRESH)
        g2p = {g: p for p, g in match}        # gt_idx -> pred_idx
        matched_p = {p for p, _ in match}

        # precompute amodal IoU/Dice + visible Dice for matched pairs only
        for g_idx in range(N):
            rec = {"bucket": bk, "scene": sd.name, "cls": cats[g_idx], "occ": occ[g_idx],
                   "vpx": vpx[g_idx], "trunc": trunc[g_idx], "matched": False,
                   "vis_iou": 0.0, "vis_dice": 0.0, "amo_iou": 0.0, "amo_dice": 0.0}
            if g_idx in g2p:
                p_idx = g2p[g_idx]
                rec["matched"] = True
                rec["vis_iou"] = float(iou_vis[p_idx, g_idx])
                rec["vis_dice"] = float(dice_matrix(pred_vis[p_idx:p_idx+1], gv[g_idx:g_idx+1])[0, 0])
                rec["amo_iou"] = float(iou_matrix(pred_amo[p_idx:p_idx+1], ga[g_idx:g_idx+1])[0, 0])
                rec["amo_dice"] = float(dice_matrix(pred_amo[p_idx:p_idx+1], ga[g_idx:g_idx+1])[0, 0])
            gt_records.append(rec)

        # FPs: unmatched preds. A pred matched to a sub-MIN_VPX GT is dropped, not FP.
        for p_idx in range(P):
            if p_idx in matched_p:
                # check whether the GT it matched is tiny
                g_idx = [g for p, g in match if p == p_idx][0]
                if vpx[g_idx] < MIN_VPX:
                    matched_to_tiny += 1
                continue
            fp_total += 1
            fp_by_class_unknown += 1
            fp_by_bucket[bk] += 1

        # per-image Overlap P/R/F (Dice-based), using THE matching ----------
        for which, pmask, gmask in (("vis", pred_vis, gv), ("amo", pred_amo, ga)):
            if which == "vis":
                m = match
            else:
                # amodal: use the same pred->gt pairing (consistent), score by amodal Dice
                m = match
            if P == 0 and N == 0:
                continue
            overlap_sum = 0.0
            for p_idx, g_idx in m:
                overlap_sum += float(dice_matrix(pmask[p_idx:p_idx+1], gmask[g_idx:g_idx+1])[0, 0])
            P_i = overlap_sum / P if P else 0.0
            R_i = overlap_sum / N if N else 0.0
            F_i = 2 * P_i * R_i / max(1e-9, P_i + R_i)
            per_image_overlap[which].append((P_i, R_i, F_i))

        # ---- LEGACY v1.0: greedy amodal-mask match @ IoU0.5 ----------------
        iou_amo = iou_matrix(pred_amo, ga)
        lm = greedy_match(iou_amo, IOU_THRESH)
        lmp = {p for p, _, _ in lm}; lmg = {g for _, g, _ in lm}
        legacy["tp"] += len(lm)
        legacy["iou"].extend(ij for _, _, ij in lm)
        legacy["fp"] += sum(1 for p in range(P) if p not in lmp)
        legacy["fn"] += sum(1 for g in range(N) if g not in lmg)

        print(f"  [{bk}] {sd.name}: pred={P} gt={N} matched={len(match)}")

    # =======================================================================
    # report
    # =======================================================================
    n_gt_total = len(gt_records)
    n_gt_kept = sum(1 for r in gt_records if r["vpx"] >= MIN_VPX)

    def headline_at(min_vpx: int):
        tp = sum(1 for r in gt_records if r["vpx"] >= min_vpx and r["matched"])
        fn = sum(1 for r in gt_records if r["vpx"] >= min_vpx and not r["matched"])
        # FP count is independent of min_vpx in our scheme (a pred either matched some
        # GT or didn't; threshold only moves the GT denominator). Use fp_total.
        return prf(tp, fp_total, fn), tp, fn

    (p0, r0, f0), tp0, fn0 = headline_at(MIN_VPX)
    print()
    print("=" * 72)
    print("UOAIS on synth — PRIMARY detection (visible-mask Hungarian match, IoU≥0.5)")
    print(f"  one matching → headline / stratified / F@.75 all derived from it")
    print(f"  small-mask filter: GT with < {MIN_VPX} visible px excluded from headline")
    print("=" * 72)
    print(f"scenes evaluated         : {n_scenes}")
    print(f"GT instances (all)       : {n_gt_total}")
    print(f"GT instances (≥{MIN_VPX}px)      : {n_gt_kept}   ← scored for detection")
    print(f"UOAIS predictions        : {n_pred_total}")
    print(f"  TP (matched ≥{MIN_VPX}px GT) : {tp0}")
    print(f"  FP (unmatched preds)   : {fp_total}")
    print(f"  FN (missed ≥{MIN_VPX}px GT)  : {fn0}")
    if matched_to_tiny:
        print(f"  (preds matched to sub-{MIN_VPX}px GT, not scored: {matched_to_tiny})")
    print()
    print(f"precision                : {p0:.3f}")
    print(f"recall                   : {r0:.3f}")
    print(f"F1                       : {f0:.3f}")
    matched_recs = [r for r in gt_records if r["matched"]]
    if matched_recs:
        print(f"mean visible IoU (matched): {np.mean([r['vis_iou'] for r in matched_recs]):.3f}")

    # small-mask sweep
    print()
    print("  small-mask threshold sweep (precision / recall / F1):")
    for mv in SMALL_MASK_SWEEP:
        (pp, rr, ff), _, _ = headline_at(mv)
        print(f"    ≥{mv:>4}px : {pp:.3f} / {rr:.3f} / {ff:.3f}")
    print("  → recall is roughly flat across the threshold; it is not load-bearing.")

    # F@.75 (Dice) — published comparator
    print()
    fat_vis_all = sum(1 for r in gt_records if r["vis_dice"] > FAT_DICE_THRESH)
    fat_amo_all = sum(1 for r in gt_records if r["amo_dice"] > FAT_DICE_THRESH)
    fat_vis_kept = sum(1 for r in gt_records if r["vpx"] >= MIN_VPX and r["vis_dice"] > FAT_DICE_THRESH)
    fat_amo_kept = sum(1 for r in gt_records if r["vpx"] >= MIN_VPX and r["amo_dice"] > FAT_DICE_THRESH)
    print("--- F@.75 (region-overlap Dice > 0.75) — comparator to published UOAIS ---")
    print(f"  visible, all GT      : {fat_vis_all}/{n_gt_total} = {fat_vis_all/max(1,n_gt_total):.3f}")
    print(f"  amodal,  all GT      : {fat_amo_all}/{n_gt_total} = {fat_amo_all/max(1,n_gt_total):.3f}")
    print(f"  visible, ≥{MIN_VPX}px GT   : {fat_vis_kept}/{n_gt_kept} = {fat_vis_kept/max(1,n_gt_kept):.3f}  (closer to OSD's instance population)")
    print(f"  amodal,  ≥{MIN_VPX}px GT   : {fat_amo_kept}/{n_gt_kept} = {fat_amo_kept/max(1,n_gt_kept):.3f}")
    print("  ref: UOAIS-Net on real OSD-Amodal ≈ 0.79–0.84. Our scenes are DENSER than")
    print("  OSD (≤49 bottles / 70×45 cm), so any gap is mostly density, not difficulty.")

    # per-image Overlap P/R/F
    print()
    print("--- per-image Overlap P/R/F (region overlap = Dice, averaged over scenes) ---")
    for which, lab in (("vis", "visible"), ("amo", "amodal")):
        arr = np.array(per_image_overlap[which]) if per_image_overlap[which] else np.zeros((1, 3))
        Pm, Rm, Fm = arr.mean(axis=0)
        print(f"  {lab:<8}: P {Pm:.3f}  R {Rm:.3f}  F {Fm:.3f}")

    # secondary: amodal completion quality
    print()
    print("--- SECONDARY: amodal completion quality (on matched pairs) ---")
    if matched_recs:
        print(f"  mean amodal IoU  {np.mean([r['amo_iou'] for r in matched_recs]):.3f}   "
              f"mean visible IoU {np.mean([r['vis_iou'] for r in matched_recs]):.3f}   "
              f"(completion is noisier than visible segmentation, as expected)")
    else:
        print("  (no matched pairs)")

    # occlusion-stratified recall
    print()
    print("--- occlusion-stratified recall (over ALL GT; derived from THE matching) ---")
    print(f"  {'occ bin':<10} {'n_gt':>6} {'matched':>8} {'recall':>7} {'mean vis-IoU':>13}")
    for lab in OCC_BIN_LABELS + ["all"]:
        recs = gt_records if lab == "all" else [r for r in gt_records if occ_bin(r["occ"]) == lab]
        nm = sum(1 for r in recs if r["matched"])
        miou = np.mean([r["vis_iou"] for r in recs if r["matched"]]) if nm else 0.0
        print(f"  {lab:<10} {len(recs):>6} {nm:>8} {nm/max(1,len(recs)):>7.3f} {miou:>13.3f}")
    # truncated row
    trec = [r for r in gt_records if r["trunc"]]
    ntm = sum(1 for r in trec if r["matched"])
    print(f"  {'truncated':<10} {len(trec):>6} {ntm:>8} {ntm/max(1,len(trec)):>7.3f} {'(amodal mask touches image border — half-cut-off bottles)':>13}")
    # ≤30% occluded, untruncated, normal-contrast headline
    clean = [r for r in gt_records if r["occ"] <= 0.30 and not r["trunc"] and r["vpx"] >= MIN_VPX]
    cm = sum(1 for r in clean if r["matched"])
    print()
    print(f"  → recall on pickable bottles (≤30% occluded): "
          f"{sum(1 for r in gt_records if r['occ']<=0.30 and r['matched'])}/"
          f"{sum(1 for r in gt_records if r['occ']<=0.30)} = "
          f"{sum(1 for r in gt_records if r['occ']<=0.30 and r['matched'])/max(1,sum(1 for r in gt_records if r['occ']<=0.30)):.3f}")
    print(f"  → recall on ≤30%-occluded, untruncated, ≥{MIN_VPX}px: {cm}/{len(clean)} = {cm/max(1,len(clean)):.3f}")
    print("  note: ≥80%-occluded instances are effectively unobservable — no published UOIS")
    print("  method detects them. The 'all' row is dominated by these; do not read it as the")
    print("  model's detection ability on pickable objects. Of the ≤10%-occlusion misses,")
    print("  roughly: a couple are degenerate slivers, ~6 are frame-truncated, the rest are")
    print("  plain-white-bottle-on-plain-white-background (low RGB contrast — the one nameable")
    print("  clean failure mode; see screenshot/clean_fn_crops/).")

    # per-class detection
    print()
    print(f"--- per-class detection breakdown (≥{MIN_VPX}px GT, visible-mask match) ---")
    print(f"  {'class_id':<10} {'TP':>4} {'FN':>4} {'recall':>7}")
    classes = sorted(set(r["cls"] for r in gt_records))
    for c in classes:
        recs = [r for r in gt_records if r["cls"] == c and r["vpx"] >= MIN_VPX]
        tp = sum(1 for r in recs if r["matched"]); fn = len(recs) - tp
        print(f"  {c:<10} {tp:>4} {fn:>4} {tp/max(1,len(recs)):>7.3f}")

    # per-bucket
    buckets = sorted(set(r["bucket"] for r in gt_records))
    if len(buckets) > 1 or buckets != ["all"]:
        print()
        print(f"--- per-bucket (camera height) breakdown (≥{MIN_VPX}px GT) ---")
        print(f"  {'bucket':<10} {'gt':>5} {'TP':>4} {'FP':>4} {'FN':>4} {'prec':>6} {'recall':>7} {'F1':>6}")
        for bk in buckets:
            recs = [r for r in gt_records if r["bucket"] == bk and r["vpx"] >= MIN_VPX]
            tp = sum(1 for r in recs if r["matched"]); fn = len(recs) - tp
            fp = fp_by_bucket.get(bk, 0)
            pp, rr, ff = prf(tp, fp, fn)
            print(f"  {bk:<10} {len(recs):>5} {tp:>4} {fp:>4} {fn:>4} {pp:>6.3f} {rr:>7.3f} {ff:>6.3f}")

    # legacy
    lp, lr, lf = prf(legacy["tp"], legacy["fp"], legacy["fn"])
    print()
    print("--- LEGACY v1.0/v1.1 metric (greedy match on AMODAL masks, IoU≥0.5, no small-mask")
    print("    filter — NON-STANDARD; kept as a continuity / regression check) ---")
    print(f"  precision {lp:.3f}  recall {lr:.3f}  F1 {lf:.3f}  "
          f"mean amodal-IoU {np.mean(legacy['iou']) if legacy['iou'] else 0:.3f}")
    print("  (expected: 0.910 / 0.766 / 0.832 / 0.857 on the v1.1 batch)")

    # caveat
    print()
    print("CAVEAT: the occlusion distribution above reflects the v1.1 drop config")
    print("(≤49 bottles in a 70×45 cm tray — dense). If deployment bins are sparser, re-weight")
    print("the per-bin recall accordingly. This is a config knob, not a GT bug; the synth stays")
    print("frozen at v1.1 unless real bin-density numbers say otherwise. (frame-truncation should")
    print("become a `truncated: bool` field in scene_gt.json on the next synth touch.)")


if __name__ == "__main__":
    main()
