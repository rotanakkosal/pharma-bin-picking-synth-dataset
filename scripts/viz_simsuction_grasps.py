"""Overlay Sim-Suction predicted grasp points on the scene's RGB image.
Reads the pickled results and reprojects world-frame 3D points back to 2D."""
import argparse
import json
import pickle
from pathlib import Path

import cv2
import numpy as np


def world_to_camera_cm(xyz_world_cm, cam_height_cm):
    # Inverse of our world conversion: world (x, -y, h-z_cam) -> camera (x, y, z)
    # world_x = cam_x
    # world_y = -cam_y
    # world_z = h - cam_z  =>  cam_z = h - world_z
    x_cam = xyz_world_cm[:, 0]
    y_cam = -xyz_world_cm[:, 1]
    z_cam = cam_height_cm - xyz_world_cm[:, 2]
    return np.stack([x_cam, y_cam, z_cam], axis=1)


def project_to_pixels(xyz_cam_cm, K):
    # cm -> m, then project
    xyz_m = xyz_cam_cm / 100.0
    u = K[0, 0] * xyz_m[:, 0] / xyz_m[:, 2] + K[0, 2]
    v = K[1, 1] * xyz_m[:, 1] / xyz_m[:, 2] + K[1, 2]
    return np.stack([u, v], axis=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-dir", type=Path, required=True)
    ap.add_argument("--result-pkl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--which", default="top_10%",
                    choices=["top_1", "top_1%", "top_5%", "top_10%"])
    ap.add_argument("--cam-height-cm", type=float, default=None,
                    help="override camera height used during conversion")
    args = ap.parse_args()

    gt = json.loads((args.scene_dir / "scene_gt.json").read_text())
    K = np.array(gt["camera_K"], dtype=np.float64)

    with open(args.result_pkl, "rb") as f:
        results = pickle.load(f)

    # Full candidate pool = top_10% (collision-free filtered)
    pool = results["top_10%"]
    if not pool or "t_ori" not in pool:
        print("No candidates in result.")
        return

    ori = np.asarray(pool["t_ori"])
    rel_idx = results[args.which]["relative_indices"]
    if len(rel_idx) == 0:
        print(f"No candidates for subset {args.which}.")
        return

    pts_world = ori[rel_idx]
    print(f"visualizing {len(pts_world)} candidates ({args.which})")

    # Figure out cam height: from conversion we used max(z_cam) which ≈ max(depth).
    # For scene_000033 we empirically saw ~128.6 cm. Re-derive from depth image.
    depth = cv2.imread(str(args.scene_dir / "depth/0000.png"), cv2.IMREAD_UNCHANGED)
    cam_h_cm = args.cam_height_cm or (depth[depth > 0].max() / 10.0)
    print(f"cam height used: {cam_h_cm:.1f} cm")

    xyz_cam = world_to_camera_cm(pts_world, cam_h_cm)
    uv = project_to_pixels(xyz_cam, K)

    rgb = cv2.imread(str(args.scene_dir / "rgb/0000.png"))
    H, W = rgb.shape[:2]

    for (u, v), xyz_w in zip(uv, pts_world):
        if not (0 <= u < W and 0 <= v < H):
            continue
        cv2.circle(rgb, (int(u), int(v)), 10, (0, 255, 0), 2)      # green outer ring
        cv2.circle(rgb, (int(u), int(v)), 2,  (0, 255, 0), -1)     # green center dot

    # Highlight top_1 in red if present
    if results.get("top_1") and "relative_indices" in results["top_1"]:
        ti = results["top_1"]["relative_indices"]
        if ti:
            top_world = ori[ti[0]]
            top_cam = world_to_camera_cm(top_world[None, :], cam_h_cm)
            top_uv = project_to_pixels(top_cam, K)[0]
            cv2.circle(rgb, (int(top_uv[0]), int(top_uv[1])), 18, (0, 0, 255), 3)
            cv2.putText(rgb, "top_1", (int(top_uv[0]) + 20, int(top_uv[1])),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), rgb)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
