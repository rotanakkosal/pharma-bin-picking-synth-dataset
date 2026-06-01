import argparse
import shutil
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SRC = PROJECT_DIR / "output"
DEFAULT_DEST = PROJECT_DIR / "output_dropbox"


def export(src_root: Path, dest_root: Path, use_link: bool) -> tuple[int, int, int]:
    scene_dirs = sorted(src_root.glob("h_*/scene_*"))
    if not scene_dirs:
        print(f"No scenes found under {src_root}/h_*/scene_*", file=sys.stderr)
        return 0, 0, 0

    transfer = (lambda s, d: d.hardlink_to(s)) if use_link else shutil.copy2
    n_scenes = n_rgb = n_depth = 0

    for scene in scene_dirs:
        rgb_dir = scene / "rgb"
        depth_dir = scene / "depth"
        if not rgb_dir.exists() or not depth_dir.exists():
            print(f"  skip {scene.name}: missing rgb/ or depth/")
            continue

        out_rgb = dest_root / scene.name / "RGB"
        out_depth = dest_root / scene.name / "Depth"
        out_rgb.mkdir(parents=True, exist_ok=True)
        out_depth.mkdir(parents=True, exist_ok=True)

        for f in sorted(rgb_dir.iterdir()):
            if f.is_file():
                target = out_rgb / f.name
                if target.exists():
                    target.unlink()
                transfer(f, target)
                n_rgb += 1

        for f in sorted(depth_dir.iterdir()):
            if f.is_file():
                target = out_depth / f.name
                if target.exists():
                    target.unlink()
                transfer(f, target)
                n_depth += 1

        n_scenes += 1
        print(f"  {scene.name}: {len(list(out_rgb.iterdir()))} RGB / {len(list(out_depth.iterdir()))} Depth")

    return n_scenes, n_rgb, n_depth


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help=f"source root (default: {DEFAULT_SRC})")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"destination root (default: {DEFAULT_DEST})")
    ap.add_argument("--link", action="store_true", help="hardlink files instead of copying (saves disk)")
    args = ap.parse_args()

    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"Exporting from {args.src} -> {args.dest}  ({'hardlink' if args.link else 'copy'})")
    n_scenes, n_rgb, n_depth = export(args.src, args.dest, args.link)
    print(f"\nDone: {n_scenes} scenes, {n_rgb} RGB files, {n_depth} Depth files")
    print(f"Output: {args.dest}")


if __name__ == "__main__":
    main()
