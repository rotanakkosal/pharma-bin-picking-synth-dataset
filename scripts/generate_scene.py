import blenderproc as bproc
# BlenderProc cluttered bin-picking scene generator for UOAIS.
# Run: blenderproc run scripts/generate_scene.py --config scripts/config.yaml --scene-id 1
import argparse
import json
import math
import random
import shutil
from pathlib import Path

import numpy as np
import yaml


def load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def ensure_ascii_mesh_copies(src_dir: Path, tmp_dir: Path) -> list[tuple[str, Path]]:
    """Blender's OBJ importer can stumble on non-ASCII filenames (Korean).
    Copy to ascii names and keep the original name as the class label."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pairs = []
    for i, obj_path in enumerate(sorted(src_dir.glob("*.obj"))):
        label = obj_path.stem
        dst = tmp_dir / f"mesh_{i:02d}.obj"
        shutil.copy(obj_path, dst)
        pairs.append((label, dst))
    return pairs


def build_tray(cfg: dict) -> list:
    w, d, h, t = cfg["inner_w"], cfg["inner_d"], cfg["wall_h"], cfg["wall_t"]
    color = cfg["color"]

    parts = []
    # floor
    floor = bproc.object.create_primitive("CUBE", scale=[w / 2, d / 2, t / 2])
    floor.set_location([0, 0, t / 2])
    parts.append(floor)
    # +x wall
    parts.append(_wall([t / 2, d / 2 + t, h / 2], [w / 2 + t / 2, 0, h / 2]))
    # -x wall
    parts.append(_wall([t / 2, d / 2 + t, h / 2], [-(w / 2 + t / 2), 0, h / 2]))
    # +y wall
    parts.append(_wall([w / 2 + t, t / 2, h / 2], [0, d / 2 + t / 2, h / 2]))
    # -y wall
    parts.append(_wall([w / 2 + t, t / 2, h / 2], [0, -(d / 2 + t / 2), h / 2]))

    mat = bproc.material.create("tray_material")
    mat.set_principled_shader_value("Base Color", color)
    mat.set_principled_shader_value("Roughness", 0.6)
    mat.set_principled_shader_value("Metallic", 0.0)
    mat.set_principled_shader_value("Alpha", 1.0)
    for p in parts:
        p.replace_materials(mat)
        p.set_cp("category_id", 0)  # 0 = background / tray
        p.enable_rigidbody(active=False, collision_shape="MESH")
    return parts


def build_ground(cfg: dict):
    size = cfg["size"]
    color = cfg["color"]
    ground = bproc.object.create_primitive("PLANE", scale=[size / 2, size / 2, 1])
    ground.set_location([0, 0, 0])
    mat = bproc.material.create("ground_material")
    mat.set_principled_shader_value("Base Color", color)
    mat.set_principled_shader_value("Roughness", 0.9)
    mat.set_principled_shader_value("Alpha", 1.0)
    ground.replace_materials(mat)
    ground.set_cp("category_id", 0)
    ground.enable_rigidbody(active=False, collision_shape="BOX")
    return ground


def _wall(scale_xyz, location_xyz):
    w = bproc.object.create_primitive("CUBE", scale=scale_xyz)
    w.set_location(location_xyz)
    return w


def load_and_drop_bottles(mesh_pairs, cfg, rng: random.Random):
    bottle_cfg = cfg["meshes"]
    drop_cfg = cfg["drop"]
    s = bottle_cfg["unit_scale"]

    placed = []
    for class_id, (label, mesh_path) in enumerate(mesh_pairs, start=1):
        # Load once, then duplicate
        loaded = bproc.loader.load_obj(str(mesh_path))
        if not loaded:
            print(f"[warn] failed to load {mesh_path}")
            continue
        template = loaded[0]
        template.set_scale([s, s, s])
        template.set_name(f"{label}_template")

        # Assign a random opaque plastic-like material per class
        mat = bproc.material.create(f"mat_{label}")
        mat.set_principled_shader_value(
            "Base Color",
            [rng.uniform(0.4, 0.95), rng.uniform(0.4, 0.95), rng.uniform(0.4, 0.95), 1.0],
        )
        mat.set_principled_shader_value("Roughness", rng.uniform(0.35, 0.7))
        mat.set_principled_shader_value("Metallic", 0.0)
        mat.set_principled_shader_value("Transmission Weight", 0.0)
        mat.set_principled_shader_value("Alpha", 1.0)
        template.replace_materials(mat)

        # Move template far away; we'll duplicate instances for the actual scene
        template.set_location([10, 10, 10])

        for i in range(bottle_cfg["copies_per_mesh"]):
            dup = template.duplicate()
            dup.set_name(f"{label}_{i:02d}")
            dup.set_cp("category_id", class_id)
            dup.set_cp("class_name", label)

            dup.set_location([
                rng.uniform(*drop_cfg["x_range"]),
                rng.uniform(*drop_cfg["y_range"]),
                rng.uniform(*drop_cfg["z_range"]),
            ])
            dup.set_rotation_euler([
                rng.uniform(0, 2 * math.pi),
                rng.uniform(0, 2 * math.pi),
                rng.uniform(0, 2 * math.pi),
            ])
            dup.enable_rigidbody(active=True, mass=0.1, friction=0.8, linear_damping=0.1)
            placed.append(dup)

        # Hide the template from rendering
        template.hide(True)
        template.disable_rigidbody() if hasattr(template, "disable_rigidbody") else None

    return placed


def setup_camera(c: dict, rng: random.Random):
    K = np.array([
        [c["fx"], 0, c["cx"]],
        [0, c["fy"], c["cy"]],
        [0, 0, 1],
    ])
    bproc.camera.set_intrinsics_from_K_matrix(K, c["width"], c["height"])

    jx = rng.uniform(-c["jitter_xy_m"], c["jitter_xy_m"])
    jy = rng.uniform(-c["jitter_xy_m"], c["jitter_xy_m"])
    jyaw = math.radians(rng.uniform(-c["jitter_rot_deg"], c["jitter_rot_deg"]))

    # Camera at (jx, jy, h) looking straight down (-Z). Blender camera
    # default points -Z, so identity rotation already faces down when the
    # camera is above the scene. Add tiny yaw jitter.
    cam_pose = bproc.math.build_transformation_mat(
        [jx, jy, c["height_m"]], [0, 0, jyaw]
    )
    bproc.camera.add_camera_pose(cam_pose)


def setup_lights(lc: dict, rng: random.Random):
    bproc.renderer.set_world_background([1, 1, 1], strength=lc.get("world_strength", 0.3))
    for _ in range(lc["n_lights"]):
        light = bproc.types.Light()
        light.set_type("POINT")
        light.set_location([
            rng.uniform(-0.5, 0.5),
            rng.uniform(-0.5, 0.5),
            rng.uniform(*lc["height_range"]),
        ])
        light.set_energy(rng.uniform(*lc["energy_range"]))


def render_amodal_masks(placed) -> dict:
    """Render each bottle in isolation (all others hidden) to get amodal masks.
    Returns dict: instance_name -> HxW uint8 mask (0/255)."""
    amodal = {}
    # Remember each object's current hide state so we can restore it.
    original_hidden = {p.get_name(): _is_hidden(p) for p in placed}

    for target in placed:
        for other in placed:
            if other is target:
                other.hide(False)
            else:
                other.hide(True)
        seg_data = bproc.renderer.render_segmap(map_by=["instance", "name"], default_values={"name": ""})
        seg = seg_data["instance_segmaps"][0]
        attrs = seg_data["instance_attribute_maps"][0]
        target_id = None
        for a in attrs:
            if a.get("name") == target.get_name():
                target_id = a["idx"]
                break
        if target_id is None:
            mask = np.zeros_like(seg, dtype=np.uint8)
        else:
            mask = ((seg == target_id).astype(np.uint8)) * 255
        amodal[target.get_name()] = mask

    # Restore visibility
    for p in placed:
        p.hide(original_hidden.get(p.get_name(), False))
    return amodal


def _is_hidden(obj) -> bool:
    try:
        return bool(obj.blender_obj.hide_render)
    except Exception:
        return False


def save_outputs(data: dict, amodal_masks: dict, scene_dir: Path, placed, cfg: dict):
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "rgb").mkdir(exist_ok=True)
    (scene_dir / "depth").mkdir(exist_ok=True)
    (scene_dir / "visible_masks").mkdir(exist_ok=True)
    (scene_dir / "amodal_masks").mkdir(exist_ok=True)
    (scene_dir / "occlusion_masks").mkdir(exist_ok=True)

    from PIL import Image

    # --- RGB
    rgb = data["colors"][0]
    Image.fromarray(rgb).save(scene_dir / "rgb" / "0000.png")

    # --- Depth: convert meters -> uint16 mm to match sample_data format
    depth_m = data["depth"][0]
    depth_mm = np.clip(depth_m * 1000.0, 0, 65535).astype(np.uint16)
    Image.fromarray(depth_mm).save(scene_dir / "depth" / "0000.png")

    # --- Visible / amodal / occlusion per instance
    seg = data["instance_segmaps"][0]
    attrs = data["instance_attribute_maps"][0]

    instances = []
    for entry in attrs:
        inst_id = entry["idx"]
        name = entry.get("name", f"inst_{inst_id}")
        cat_id = entry.get("category_id", -1)
        if cat_id == 0:
            continue  # tray / ground

        visible = (seg == inst_id).astype(np.uint8) * 255
        amodal = amodal_masks.get(name, np.zeros_like(visible))
        # occlusion = amodal but hidden (not visible)
        occlusion = ((amodal > 0) & (visible == 0)).astype(np.uint8) * 255

        if amodal.sum() == 0:
            continue  # off-screen or tiny

        fname = f"0000_{inst_id:04d}.png"
        Image.fromarray(visible).save(scene_dir / "visible_masks" / fname)
        Image.fromarray(amodal).save(scene_dir / "amodal_masks" / fname)
        Image.fromarray(occlusion).save(scene_dir / "occlusion_masks" / fname)

        amodal_px = int((amodal > 0).sum())
        visible_px = int((visible > 0).sum())
        occlusion_rate = 0.0 if amodal_px == 0 else 1.0 - (visible_px / amodal_px)

        ys, xs = np.where(amodal > 0)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]

        instances.append({
            "instance_id": int(inst_id),
            "class_name": name,
            "category_id": int(cat_id),
            "visible_mask": f"visible_masks/{fname}",
            "amodal_mask": f"amodal_masks/{fname}",
            "occlusion_mask": f"occlusion_masks/{fname}",
            "visible_px": visible_px,
            "amodal_px": amodal_px,
            "occlusion_rate": round(occlusion_rate, 4),
            "bbox_xywh_amodal": bbox,
        })

    # --- scene_gt.json
    meta = {
        "image_id": 0,
        "rgb": "rgb/0000.png",
        "depth": "depth/0000.png",
        "depth_unit": "mm",
        "width": cfg["camera"]["width"],
        "height": cfg["camera"]["height"],
        "camera_K": [
            [cfg["camera"]["fx"], 0, cfg["camera"]["cx"]],
            [0, cfg["camera"]["fy"], cfg["camera"]["cy"]],
            [0, 0, 1],
        ],
        "instances": instances,
    }
    with open(scene_dir / "scene_gt.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    n_occluded = sum(1 for i in instances if i["occlusion_rate"] > 0.05)
    print(f"[ok] scene saved to {scene_dir}")
    print(f"     {len(instances)} instances total, {n_occluded} partially occluded")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scene-id", type=int, default=1)
    args = parser.parse_args()

    cfg = load_cfg(args.config)
    rng = random.Random(cfg["output"]["seed"] + args.scene_id)

    bproc.init()

    # 1. copy Korean-named meshes to ASCII temp path
    repo_root = args.config.resolve().parent.parent  # synthetic_dataset_generate/
    mesh_src = (repo_root / cfg["meshes"]["dir"]).resolve()
    mesh_tmp = repo_root / "output" / "_tmp_meshes"
    mesh_pairs = ensure_ascii_mesh_copies(mesh_src, mesh_tmp)
    print(f"[info] loaded {len(mesh_pairs)} mesh classes from {mesh_src}")

    # 2. build ground + tray
    build_ground(cfg["ground"])
    build_tray(cfg["tray"])

    # 3. load bottles + physics-drop
    placed = load_and_drop_bottles(mesh_pairs, cfg, rng)
    print(f"[info] placed {len(placed)} bottle instances")

    # 4. simulate physics
    bproc.object.simulate_physics_and_fix_final_poses(
        min_simulation_time=cfg["drop"]["min_sim_time"],
        max_simulation_time=cfg["drop"]["max_sim_time"],
        check_object_interval=1,
    )

    # 5. camera + lights
    setup_camera(cfg["camera"], rng)
    setup_lights(cfg["lighting"], rng)

    # 6. render passes — RGB + depth from render(), segmap from render_segmap()
    bproc.renderer.set_max_amount_of_samples(cfg["render"]["samples"])
    bproc.renderer.enable_depth_output(activate_antialiasing=False)
    data = bproc.renderer.render()

    # Full-scene visible instance segmap
    seg_full = bproc.renderer.render_segmap(map_by=["instance", "name", "category_id"], default_values={"category_id": -1, "name": ""})
    data["instance_segmaps"] = seg_full["instance_segmaps"]
    data["instance_attribute_maps"] = seg_full["instance_attribute_maps"]

    # 7. render amodal masks (one extra segmap pass per instance)
    print(f"[info] rendering amodal masks for {len(placed)} instances...")
    amodal_masks = render_amodal_masks(placed)

    # 8. save
    scene_dir = (repo_root / cfg["output"]["dir"] / f"scene_{args.scene_id:06d}").resolve()
    save_outputs(data, amodal_masks, scene_dir, placed, cfg)


if __name__ == "__main__":
    main()
