"""Stage our synth scenes into the flat folder layout UOAIS expects.

UOAIS's run_on_OSD.py loads:
    {dataset_path}/image_color/*.png    (RGB)
    {dataset_path}/disparity/*.png      (depth in mm — see utils.normalize_depth)

Despite the name `disparity/`, this fork's normalize_depth() takes raw mm
values and clamps to [250, 1500]mm. Our synth depth is already uint16 mm,
so no conversion needed — we just symlink scene_NNNNNN/rgb/0000.png and
scene_NNNNNN/depth/0000.png into the flat layout under names that sort
identically (scene_NNNNNN.png).

Run from project root:
    python scripts/build_uoais_input.py
"""
from pathlib import Path
import shutil

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "output"
DST = REPO / "synth_for_uoais"


def main() -> None:
    if not SRC.exists():
        raise FileNotFoundError(SRC)

    rgb_dir = DST / "image_color"
    depth_dir = DST / "disparity"
    if DST.exists():
        shutil.rmtree(DST)
    rgb_dir.mkdir(parents=True)
    depth_dir.mkdir(parents=True)

    scene_dirs = sorted(p for p in SRC.glob("scene_*") if p.is_dir())
    n = 0
    for sd in scene_dirs:
        rgb_src = sd / "rgb" / "0000.png"
        depth_src = sd / "depth" / "0000.png"
        if not rgb_src.exists() or not depth_src.exists():
            print(f"  skip {sd.name}: missing rgb or depth")
            continue
        # Symlink into flat layout so both folders sort to the same scene order.
        (rgb_dir / f"{sd.name}.png").symlink_to(rgb_src.resolve())
        (depth_dir / f"{sd.name}.png").symlink_to(depth_src.resolve())
        n += 1

    print(f"staged {n} scenes")
    print(f"  RGB   : {rgb_dir}/")
    print(f"  depth : {depth_dir}/")
    print(f"\nRun UOAIS:")
    print(f"  cd ../pharma-bin-picking")
    print(f"  python tools/run_on_OSD.py --dataset-path {DST} --output-subdir synth_test")


if __name__ == "__main__":
    main()
