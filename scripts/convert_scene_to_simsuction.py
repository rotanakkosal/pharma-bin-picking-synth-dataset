"""Convert a rendered scene from our BlenderProc pipeline into the
Sim-Suction inference input format (N x 10 npz).

Column layout expected by sim_suction_inference.py:
  [0:3] xyz (centimeters)
  [3:6] surface normals (unit vectors)
  [6:9] rgb (0..1 floats)
  [9]   instance segmentation id (0=background, 1..N=objects)

Usage:
  python convert_scene_to_simsuction.py \
      --scene-dir /path/to/scene_000033 \
      --out-dir   /path/to/simsuction_in \
      --stage 0 --frame 0
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--stage", type=int, default=0)
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--max-depth-mm", type=float, default=2500,
                    help="drop points beyond this depth (filters OOB pixels)")
    ap.add_argument("--min-depth-mm", type=float, default=100)
    ap.add_argument("--target-points", type=int, default=20000,
                    help="random-downsample the cloud to this size")
    args = ap.parse_args()

    scene = args.scene_dir
    gt = json.loads((scene / "scene_gt.json").read_text())
    K = np.array(gt["camera_K"], dtype=np.float64)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    rgb = cv2.cvtColor(cv2.imread(str(scene / "rgb/0000.png")), cv2.COLOR_BGR2RGB)
    depth_mm = cv2.imread(str(scene / "depth/0000.png"), cv2.IMREAD_UNCHANGED).astype(np.float32)
    H, W = depth_mm.shape

    # Build per-pixel instance-id map from visible masks
    seg = np.zeros((H, W), dtype=np.int32)
    for inst in gt["instances"]:
        m_path = scene / inst["visible_mask"]
        if not m_path.exists():
            continue
        mask = cv2.imread(str(m_path), cv2.IMREAD_GRAYSCALE)
        seg[mask > 0] = inst["instance_id"]

    # Unproject depth to 3D points (camera frame, meters → centimeters)
    us, vs = np.meshgrid(np.arange(W), np.arange(H))
    valid = (depth_mm >= args.min_depth_mm) & (depth_mm <= args.max_depth_mm)
    z_m = depth_mm[valid] / 1000.0
    x_m = (us[valid] - cx) * z_m / fx
    y_m = (vs[valid] - cy) * z_m / fy
    xyz_cam_cm = np.stack([x_m, y_m, z_m], axis=1) * 100.0

    # ---- Convert camera frame → world frame ----
    # Our scene: camera at world (0,0,h_cm) looking straight down (-Z_world).
    # Camera convention: +Z forward (into scene), +X right, +Y down.
    # World: +Z up (ground plane at z=0), +X right, +Y forward.
    # Transform: rotate 180° around X-axis, then translate by +h_cm on Z.
    cam_height_cm = gt["camera_K"] and 128.6  # fallback; override below if available
    # Use a robust estimate: camera height ≈ max observed z in camera frame
    cam_height_cm = float(xyz_cam_cm[:, 2].max())
    xyz_cm = np.stack([
         xyz_cam_cm[:, 0],
        -xyz_cam_cm[:, 1],
        cam_height_cm - xyz_cam_cm[:, 2],
    ], axis=1)

    # Colors (0..1) and seg ids
    rgb_f = rgb[valid].astype(np.float32) / 255.0
    seg_ids = seg[valid].astype(np.int32)

    # Downsample to target size for PointNet++ (5120 minimum, more is fine)
    if len(xyz_cm) > args.target_points:
        idx = np.random.choice(len(xyz_cm), args.target_points, replace=False)
        xyz_cm, rgb_f, seg_ids = xyz_cm[idx], rgb_f[idx], seg_ids[idx]

    # Estimate normals via Open3D
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz_cm)
    pcd.estimate_normals(
        search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=2.0, max_nn=30)
    )
    # Orient normals toward world camera location (above the tray, looking down)
    pcd.orient_normals_towards_camera_location(
        camera_location=np.array([0, 0, cam_height_cm])
    )
    normals = np.asarray(pcd.normals).astype(np.float32)

    # Pack N x 10
    arr = np.concatenate(
        [xyz_cm.astype(np.float32), normals, rgb_f, seg_ids[:, None].astype(np.float32)],
        axis=1,
    )
    assert arr.shape[1] == 10

    # Save
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{args.stage}_{args.frame}.npz"
    np.savez_compressed(out_path, arr_0=arr)
    print(f"saved {out_path}  shape={arr.shape}")
    print(f"  xyz range (cm): x={xyz_cm[:,0].min():.1f}..{xyz_cm[:,0].max():.1f}, "
          f"y={xyz_cm[:,1].min():.1f}..{xyz_cm[:,1].max():.1f}, "
          f"z={xyz_cm[:,2].min():.1f}..{xyz_cm[:,2].max():.1f}")
    print(f"  instances present: {sorted(set(int(i) for i in seg_ids))[:10]}... "
          f"(total {len(set(seg_ids))})")


if __name__ == "__main__":
    main()
