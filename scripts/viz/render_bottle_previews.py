# Build a preview.png for each bottle in sample_data/bottles/<id>/ by cropping
# the least-occluded instance of that class out of an existing scene render.
#
# Why this approach: rendering studio shots of each mesh in a fresh Blender
# session repeatedly produced washed-out / blown-out / mis-scaled previews. The
# scene renders we already have are the ground truth of what the bottle looks
# like in this pipeline — cropping from those is honest and cheap.
#
# Run:
#     python scripts/viz/render_bottle_previews.py

import json
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "output"
BOTTLES_DIR = REPO / "sample_data" / "bottles"
PAD_PX = 24
BG_RGB = (245, 245, 245)


def collect_instances() -> dict[str, list[dict]]:
    """Walk every output/scene_*/scene_gt.json and bucket instances by class_name."""
    by_class: dict[str, list[dict]] = {}
    for gt_path in sorted(OUTPUT_DIR.glob("scene_*/scene_gt.json")):
        scene_dir = gt_path.parent
        with open(gt_path) as f:
            gt = json.load(f)
        for inst in gt["instances"]:
            by_class.setdefault(inst["class_name"], []).append(
                {"scene_dir": scene_dir, **inst}
            )
    return by_class


def pick_best(insts: list[dict]) -> dict:
    """Lowest occlusion wins; break ties by largest visible_px (bigger = clearer)."""
    return min(insts, key=lambda i: (i["occlusion_rate"], -i["visible_px"]))


def crop_with_mask(scene_dir: Path, inst: dict) -> Image.Image:
    """Crop the bottle's amodal bbox with padding; composite onto a flat
    background using the amodal mask so neighbouring bottles don't bleed in."""
    rgb = np.array(Image.open(scene_dir / "rgb" / "0000.png"))
    mask = np.array(Image.open(scene_dir / inst["amodal_mask"])) > 0

    h, w = rgb.shape[:2]
    x, y, bw, bh = inst["bbox_xywh_amodal"]
    x0 = max(0, x - PAD_PX)
    y0 = max(0, y - PAD_PX)
    x1 = min(w, x + bw + PAD_PX)
    y1 = min(h, y + bh + PAD_PX)

    crop_rgb = rgb[y0:y1, x0:x1].copy()
    crop_mask = mask[y0:y1, x0:x1]

    out = np.full_like(crop_rgb, BG_RGB, dtype=np.uint8)
    out[crop_mask] = crop_rgb[crop_mask]
    return Image.fromarray(out)


def main() -> None:
    if not OUTPUT_DIR.exists():
        raise SystemExit(f"missing {OUTPUT_DIR} — render at least one scene first")

    by_class = collect_instances()
    if not by_class:
        raise SystemExit("no scene_gt.json files found under output/")

    expected = sorted(p.name for p in BOTTLES_DIR.iterdir() if p.is_dir())
    print(f"found {sum(len(v) for v in by_class.values())} instances across "
          f"{len(by_class)} classes; bottles dir has {len(expected)} folders")

    for class_name in expected:
        insts = by_class.get(class_name, [])
        if not insts:
            print(f"  {class_name:25s} -- no instances in any scene, skipping")
            continue
        best = pick_best(insts)
        preview = crop_with_mask(best["scene_dir"], best)
        out_path = BOTTLES_DIR / class_name / "preview.png"
        preview.save(out_path)
        print(f"  {class_name:25s} <- {best['scene_dir'].name} "
              f"id={best['instance_id']:>3d} occ={best['occlusion_rate']:.2f} "
              f"px={best['visible_px']:>5d} -> {out_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
