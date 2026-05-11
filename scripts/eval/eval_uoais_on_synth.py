"""Score UOAIS predictions against our synth GT.

Pairs `output/scene_*/scene_gt.json` + `amodal_masks/` (ours) with
`<uoais-out>/scene_*.npz` (UOAIS predictions). Greedy-matches preds
to GT instances by amodal-mask IoU, then reports:

  - precision / recall / F1 / mAP@0.5 across all instances
  - per-class breakdown (using category_id from scene_gt.json)
  - over- and under-segmentation (extra preds vs missed GT)

UOAIS preds are at model (H, W); we resize each GT amodal mask down to
match before IoU. UOAIS doesn't expose per-instance scores in this
fork, so all preds are treated as score=1 — the metric is effectively
precision/recall at IoU=0.5, not full COCO AP.

Run from project root:
    python scripts/eval/eval_uoais_on_synth.py \\
        --uoais-out ../pharma-bin-picking/output/synth_test
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
IOU_THRESH = 0.5


def load_gt_amodal_masks(scene_dir: Path, gt: dict, target_hw: tuple[int, int]) -> tuple[np.ndarray, list[int], list[str]]:
    """Stack all GT amodal masks for a scene into one (N, H, W) bool array, resized
    to UOAIS's input HW. Returns (masks, category_ids, class_names)."""
    H, W = target_hw
    masks, cats, names = [], [], []
    for inst in gt["instances"]:
        m = np.asarray(Image.open(scene_dir / inst["amodal_mask"]).resize((W, H), Image.NEAREST)) > 0
        masks.append(m)
        cats.append(int(inst["category_id"]))
        names.append(inst["class_name"])
    if not masks:
        return np.zeros((0, H, W), dtype=bool), [], []
    return np.stack(masks), cats, names


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU between two stacks of bool masks. a:(M,H,W), b:(N,H,W) -> (M,N)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    a_flat = a.reshape(len(a), -1).astype(np.uint32)
    b_flat = b.reshape(len(b), -1).astype(np.uint32)
    inter = a_flat @ b_flat.T
    a_sum = a_flat.sum(1, keepdims=True)
    b_sum = b_flat.sum(1, keepdims=True)
    union = a_sum + b_sum.T - inter
    return np.where(union > 0, inter / np.maximum(union, 1), 0.0).astype(np.float32)


def greedy_match(iou: np.ndarray, thresh: float) -> list[tuple[int, int, float]]:
    """Greedy 1:1 matching: repeatedly take the highest-IoU pair above threshold,
    remove its row+col, repeat. Returns list of (pred_idx, gt_idx, iou)."""
    matches = []
    iou = iou.copy()
    while iou.size and iou.max() >= thresh:
        p, g = np.unravel_index(iou.argmax(), iou.shape)
        matches.append((int(p), int(g), float(iou[p, g])))
        iou[p, :] = 0
        iou[:, g] = 0
    return matches


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth-output-dir", type=Path, default=REPO / "output")
    ap.add_argument("--uoais-out", type=Path, required=True,
                    help="path to pharma-bin-picking/output/<subdir> containing scene_*.npz")
    args = ap.parse_args()

    synth_root = args.synth_output_dir.resolve()
    uoais_root = args.uoais_out.resolve()

    # Find scenes under both flat (output/scene_*) and bucketed (output/h_*/scene_*) layouts.
    scene_dirs = sorted(
        p for pat in ("scene_*", "h_*/scene_*")
        for p in synth_root.glob(pat) if p.is_dir()
    )
    if not scene_dirs:
        raise SystemExit(f"no scene_* dirs in {synth_root}")

    # Same flexibility on the prediction side.
    npz_lookup = {p.stem: p for pat in ("*.npz", "h_*/*.npz")
                  for p in uoais_root.glob(pat)}

    # Per-bucket aggregation buckets: key = "h_<height>" or "all" for flat layout
    per_bucket = defaultdict(lambda: {
        "tp": Counter(), "fp": Counter(), "fn": Counter(),
        "iou": defaultdict(list), "n_pred": 0, "n_gt": 0, "scenes": 0,
    })

    tp_per_class = Counter()
    fp_per_class = Counter()
    fn_per_class = Counter()
    iou_per_class = defaultdict(list)
    n_pred_total = 0
    n_gt_total = 0

    for sd in scene_dirs:
        gt_path = sd / "scene_gt.json"
        npz_path = npz_lookup.get(sd.name)
        if not gt_path.exists() or npz_path is None:
            print(f"  skip {sd.name}: missing gt or pred")
            continue
        # Bucket = parent dir name if it starts with "h_", else "all".
        bucket = sd.parent.name if sd.parent.name.startswith("h_") else "all"
        b = per_bucket[bucket]

        gt = json.load(gt_path.open())
        npz = np.load(npz_path)
        pred_masks = npz["pred_amodal"]  # (N, H, W) bool
        H, W = pred_masks.shape[1], pred_masks.shape[2]

        gt_masks, gt_cats, gt_names = load_gt_amodal_masks(sd, gt, (H, W))
        n_pred_total += len(pred_masks)
        n_gt_total += len(gt_masks)
        b["n_pred"] += len(pred_masks)
        b["n_gt"] += len(gt_masks)
        b["scenes"] += 1

        iou = iou_matrix(pred_masks, gt_masks)
        matches = greedy_match(iou, IOU_THRESH)

        matched_preds = {p for p, _, _ in matches}
        matched_gts = {g for _, g, _ in matches}

        for p, g, ij in matches:
            cls = gt_cats[g]
            tp_per_class[cls] += 1
            iou_per_class[cls].append(ij)
            b["tp"][cls] += 1
            b["iou"][cls].append(ij)

        # Unmatched predictions: false positives — credit to "unknown" class
        for p in range(len(pred_masks)):
            if p not in matched_preds:
                fp_per_class[-1] += 1
                b["fp"][-1] += 1

        # Unmatched GT: false negatives — credit to GT's class
        for g in range(len(gt_masks)):
            if g not in matched_gts:
                fn_per_class[gt_cats[g]] += 1
                b["fn"][gt_cats[g]] += 1

        print(f"  [{bucket}] {sd.name}: pred={len(pred_masks)} gt={len(gt_masks)} matched={len(matches)}")

    # --- aggregate
    total_tp = sum(tp_per_class.values())
    total_fp = sum(fp_per_class.values())
    total_fn = sum(fn_per_class.values())
    precision = total_tp / max(1, total_tp + total_fp)
    recall = total_tp / max(1, total_tp + total_fn)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)

    print()
    print("=" * 64)
    print(f"UOAIS on synth — IoU threshold = {IOU_THRESH}")
    print("=" * 64)
    print(f"scenes evaluated         : {len(scene_dirs)}")
    print(f"GT instances             : {n_gt_total}")
    print(f"UOAIS predictions        : {n_pred_total}")
    print(f"  matched (TP)           : {total_tp}")
    print(f"  unmatched preds (FP)   : {total_fp}  ← over-segmentation / spurious")
    print(f"  missed GT (FN)         : {total_fn}  ← under-segmentation / missed bottles")
    print()
    print(f"precision (TP / (TP+FP)) : {precision:.3f}")
    print(f"recall    (TP / (TP+FN)) : {recall:.3f}")
    print(f"F1                       : {f1:.3f}")
    if total_tp:
        all_iou = [v for vs in iou_per_class.values() for v in vs]
        print(f"mean IoU on matches      : {np.mean(all_iou):.3f}")

    # --- per-bucket breakdown (only meaningful when scenes were bucketed by camera height)
    if len(per_bucket) > 1 or (len(per_bucket) == 1 and "all" not in per_bucket):
        print()
        print("per-bucket (camera-height) breakdown:")
        print(f"  {'bucket':<10} {'scenes':>6} {'gt':>5} {'pred':>5} {'TP':>4} {'FP':>4} {'FN':>4} {'prec':>6} {'recall':>7} {'F1':>6} {'mIoU':>6}")
        for bucket in sorted(per_bucket):
            b = per_bucket[bucket]
            tp, fp, fn = sum(b["tp"].values()), sum(b["fp"].values()), sum(b["fn"].values())
            p = tp / max(1, tp + fp)
            r = tp / max(1, tp + fn)
            f = 2 * p * r / max(1e-9, p + r)
            ious = [v for vs in b["iou"].values() for v in vs]
            miou = float(np.mean(ious)) if ious else 0.0
            print(f"  {bucket:<10} {b['scenes']:>6} {b['n_gt']:>5} {b['n_pred']:>5} "
                  f"{tp:>4} {fp:>4} {fn:>4} {p:>6.3f} {r:>7.3f} {f:>6.3f} {miou:>6.3f}")
    print()
    print("--- per-class breakdown ---")
    print(f"{'class_id':<10} {'TP':>4} {'FP':>4} {'FN':>4} {'recall':>7} {'mean_IoU':>9}")
    all_classes = sorted(set(list(tp_per_class) + list(fn_per_class)) - {-1})
    for c in all_classes:
        tp, fp, fn = tp_per_class[c], fp_per_class[c], fn_per_class[c]
        r = tp / max(1, tp + fn)
        miou = np.mean(iou_per_class[c]) if iou_per_class[c] else 0.0
        print(f"{c:<10} {tp:>4} {fp:>4} {fn:>4} {r:>7.3f} {miou:>9.3f}")


if __name__ == "__main__":
    main()
