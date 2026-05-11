"""Crop the label region from the capture-team's full-bottle UV textures and
add the crops to the procedural label pool.

Each 4096x4096 photoreal texture is roughly: HDPE band on top, label
rectangle in the middle, HDPE band on bottom. We take the middle 50%
(y = 1024..3072 -> 4096x2048) and resample to 1024x512 to match the
existing pool's aspect ratio. The procedural-label path (cylinder UV
unwrap with `scale_to_bounds=True`) then wraps the crop once around the
bottle body just like any synthetic label.

Run from project root:
    python scripts/archive/crop_photoreal_labels.py
"""
from pathlib import Path
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
SRC_BASE = REPO / "sample_data/Phama Bottle 3D Object/2026-04-29"
DST = REPO / "textures/labels"

CROPS = [
    (SRC_BASE / "ver7/콜민A_texture.png", DST / "label_040_photoreal_kolmin.png"),
    (SRC_BASE / "L/L_texture.png",        DST / "label_041_photoreal_levozin.png"),
]

OUT_SIZE = (1024, 512)


def crop_label(src: Path, dst: Path) -> None:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    band_h = h // 2
    top = (h - band_h) // 2
    band = im.crop((0, top, w, top + band_h))
    band.resize(OUT_SIZE, Image.LANCZOS).save(dst)
    print(f"  {src.name:40s} -> {dst.name}  ({im.size} -> {OUT_SIZE})")


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    print(f"Writing photoreal label crops to {DST}/")
    for src, dst in CROPS:
        if not src.exists():
            raise FileNotFoundError(src)
        crop_label(src, dst)
    n_pool = len(sorted(DST.glob("label_*.png")))
    print(f"Pool size now: {n_pool}")


if __name__ == "__main__":
    main()
