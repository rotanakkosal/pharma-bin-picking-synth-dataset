"""Overlay class_name on each instance in a rendered scene so we can
visually map mesh shapes to class labels.

Run from project root:
    python scripts/visualize_classes.py --scene scene_000999
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]

# Distinct color per class for the bbox/text background
PALETTE = [
    (255, 60, 60), (60, 180, 60), (60, 120, 255), (255, 200, 0),
    (200, 60, 200), (0, 200, 200), (255, 140, 0),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, help="e.g. scene_000999")
    ap.add_argument("--output-dir", type=Path, default=REPO / "output")
    ap.add_argument("--out", type=Path, default=None,
                    help="output png path (default: <scene>_labelled.png next to scene_gt.json)")
    args = ap.parse_args()

    scene_dir = (args.output_dir / args.scene).resolve()
    gt = json.load((scene_dir / "scene_gt.json").open())

    rgb = Image.open(scene_dir / "rgb" / "0000.png").convert("RGB")
    draw = ImageDraw.Draw(rgb)

    # Try to load a font; fall back to default if not available.
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    classes_seen = []
    class_to_color = {}

    for inst in gt["instances"]:
        cls = inst["class_name"]
        cat_id = inst["category_id"]
        if cls not in class_to_color:
            class_to_color[cls] = PALETTE[len(classes_seen) % len(PALETTE)]
            classes_seen.append(cls)
        color = class_to_color[cls]

        mask = np.asarray(Image.open(scene_dir / inst["visible_mask"])) > 0
        if not mask.any():
            continue
        ys, xs = np.where(mask)
        cx, cy = int(xs.mean()), int(ys.mean())

        # Draw bounding box from amodal bbox in JSON
        x, y, w, h = inst["bbox_xywh_amodal"]
        draw.rectangle([x, y, x + w, y + h], outline=color, width=3)

        label = f"{cat_id}:{cls}"
        # Background box for legibility
        bbox = draw.textbbox((cx, cy), label, font=font, anchor="mm")
        pad = 4
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
            fill=color,
        )
        draw.text((cx, cy), label, fill="white", font=font, anchor="mm")

    out = args.out or (scene_dir / f"{args.scene}_labelled.png")
    rgb.save(out)
    print(f"saved {out}")
    print(f"\nclass legend (color = bbox/text bg):")
    for cls in classes_seen:
        c = class_to_color[cls]
        print(f"  rgb{c}  {cls}")


if __name__ == "__main__":
    main()
