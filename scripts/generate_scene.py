import blenderproc as bproc
# BlenderProc cluttered bin-picking scene generator for UOAIS.
# Run: blenderproc run scripts/generate_scene.py --config scripts/config.yaml --scene-id 1
import argparse
import json
import math
import random
import shutil
import sys
from pathlib import Path

# Make sibling scripts importable when launched via `blenderproc run`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import yaml


def load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def ensure_ascii_mesh_copies(src_dir: Path, tmp_dir: Path) -> list[tuple[str, Path]]:
    """Walk the canonical bottle layout (sample_data/bottles/<id>/mesh.obj),
    copy to ascii-named mesh_NN.obj in tmp_dir for Blender, and return
    (id, copied_path) pairs. The id (parent folder name) is the class label."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pairs = []
    for i, obj_path in enumerate(sorted(src_dir.glob("*/mesh.obj"))):
        label = obj_path.parent.name
        dst = tmp_dir / f"mesh_{i:02d}.obj"
        shutil.copy(obj_path, dst)
        pairs.append((label, dst))
    return pairs


def load_label_pool(labels_dir: Path) -> list[Path]:
    paths = sorted(p for p in labels_dir.glob("label_*.png") if "fullcolor" not in p.name)
    if not paths:
        raise FileNotFoundError(
            f"No labels found in {labels_dir}. "
            f"Run `python scripts/gen_fake_labels.py --n 30` first."
        )
    return paths


def bake_y_to_z_rotation(mesh_obj):
    """Bake a +90° rotation around X so a Y-up OBJ becomes Z-up. Required
    regardless of UV strategy because rigidbody physics expects bottles to
    stand on +Z."""
    import bpy
    obj = mesh_obj.blender_obj
    bpy.context.view_layer.objects.active = obj
    obj.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


def smart_unwrap(mesh_obj):
    """Cylinder-project UVs so the label wraps once around the bottle's
    long axis as one continuous piece. Used for procedural-label path
    where the OBJ has no UVs. Bakes Y→Z first."""
    import bpy
    bake_y_to_z_rotation(mesh_obj)
    obj = mesh_obj.blender_obj
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.cylinder_project(
        direction="ALIGN_TO_OBJECT",
        align="POLAR_ZX",
        scale_to_bounds=True,
    )
    bpy.ops.object.mode_set(mode="OBJECT")


def stage_textured_mesh(label: str, src_obj: Path, src_tex: Path, tmp_dir: Path, idx: int) -> Path:
    """Copy a textured OBJ + its MTL + label texture into tmp_dir using
    ASCII-only filenames. Rewrites mtllib (in OBJ) and map_Kd (in MTL)
    so cross-references resolve regardless of source naming.

    Returns the path to the staged OBJ.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tag = f"tex_{idx:02d}"
    dst_obj = tmp_dir / f"{tag}.obj"
    dst_mtl = tmp_dir / f"{tag}.mtl"
    dst_tex = tmp_dir / f"{tag}.png"

    shutil.copy(src_tex, dst_tex)

    src_mtl_name = None
    obj_out_lines = []
    for line in src_obj.read_text().splitlines():
        if line.strip().startswith("mtllib"):
            src_mtl_name = line.split(maxsplit=1)[1].strip()
            obj_out_lines.append(f"mtllib {dst_mtl.name}")
        else:
            obj_out_lines.append(line)
    dst_obj.write_text("\n".join(obj_out_lines) + "\n")

    src_mtl = None
    if src_mtl_name:
        candidate = src_obj.parent / src_mtl_name
        if candidate.exists():
            src_mtl = candidate
    if src_mtl is None:
        # Fallback: pick the first .mtl file in the source dir (handles the
        # L/ case where the OBJ was renamed but its mtllib line still points
        # at the original Korean filename).
        mtl_files = sorted(src_obj.parent.glob("*.mtl"))
        if mtl_files:
            src_mtl = mtl_files[0]
            print(f"[info] textured mesh {label}: mtllib '{src_mtl_name}' missing; "
                  f"using {src_mtl.name} from same dir")

    if src_mtl is not None:
        mtl_out_lines = []
        for line in src_mtl.read_text().splitlines():
            if line.strip().startswith("map_Kd"):
                mtl_out_lines.append(f"map_Kd {dst_tex.name}")
            else:
                mtl_out_lines.append(line)
        dst_mtl.write_text("\n".join(mtl_out_lines) + "\n")
    else:
        print(f"[warn] textured mesh {label}: no .mtl found in {src_obj.parent}; "
              f"texture will not load")

    return dst_obj


def make_label_material(name: str, label_path: Path, body_tint, rng: random.Random):
    """Create a Principled BSDF material with the label PNG as Base Color,
    multiplied by a slight body-color tint so the 'white' parts of the label
    take on the bottle body color."""
    import bpy
    mat = bproc.material.create(name)
    nodes = mat.blender_obj.node_tree.nodes
    links = mat.blender_obj.node_tree.links

    bsdf = nodes.get("Principled BSDF")

    img = bpy.data.images.load(str(label_path))
    img_node = nodes.new("ShaderNodeTexImage")
    img_node.image = img
    img_node.location = (-600, 0)

    # Multiply the label image by a body tint (so the white background of the
    # label becomes the bottle body color).
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs["Fac"].default_value = 1.0
    mix.inputs["Color2"].default_value = [*body_tint, 1.0]
    mix.location = (-300, 0)
    links.new(img_node.outputs["Color"], mix.inputs["Color1"])
    links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])

    # Plastic-like surface properties
    mat.set_principled_shader_value("Roughness", rng.uniform(0.35, 0.55))
    mat.set_principled_shader_value("Metallic", 0.0)
    mat.set_principled_shader_value("Alpha", 1.0)
    return mat


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


def load_and_drop_bottles(mesh_pairs, cfg, label_pool: list[Path], rng: random.Random,
                          repo_root: Path, tmp_dir: Path):
    bottle_cfg = cfg["meshes"]
    drop_cfg = cfg["drop"]
    s = bottle_cfg["unit_scale"]

    # Real Korean pharma bottles are white opaque HDPE — body is white with
    # only tiny variation. Color comes from the LABEL, not the body.
    body_palette = [
        (0.96, 0.96, 0.95),
        (0.95, 0.95, 0.94),
        (0.97, 0.97, 0.96),
        (0.94, 0.94, 0.93),
    ]

    # Textured-override paths are now relative to meshes.dir (the bottle root),
    # since each bottle's photoreal mesh lives in its own per-id folder.
    textured_cfg = bottle_cfg.get("textured") or {}
    textured_bottles = textured_cfg.get("bottles") or {}
    textured_base_path = (repo_root / bottle_cfg["dir"]).resolve()

    placed = []
    for class_id, (label, original_mesh_path) in enumerate(mesh_pairs, start=1):
        textured_entry = textured_bottles.get(label)
        is_textured = textured_entry is not None

        if is_textured:
            src_obj = textured_base_path / textured_entry["obj"]
            src_tex = textured_base_path / textured_entry["label_texture"]
            mesh_path = stage_textured_mesh(label, src_obj, src_tex, tmp_dir, class_id)
            print(f"[info] {label}: using textured OBJ {src_obj.name}")
        else:
            mesh_path = original_mesh_path

        loaded = bproc.loader.load_obj(str(mesh_path))
        if not loaded:
            print(f"[warn] failed to load {mesh_path}")
            continue
        template = loaded[0]
        template.set_scale([s, s, s])
        template.set_name(f"{label}_template")

        if is_textured:
            # OBJ already has UVs; only need the Y→Z rotation bake for physics.
            # Materials (Material_Bottle plain HDPE + Material_Label with texture)
            # come from the MTL via blenderproc's OBJ loader.
            bake_y_to_z_rotation(template)
        else:
            smart_unwrap(template)
            body_tint = rng.choice(body_palette)
            if bottle_cfg.get("use_labels", True):
                label_path = rng.choice(label_pool)
                mat = make_label_material(f"mat_{label}", label_path, body_tint, rng)
            else:
                mat = bproc.material.create(f"mat_{label}_plain")
                mat.set_principled_shader_value("Base Color", [*body_tint, 1.0])
                mat.set_principled_shader_value("Roughness", rng.uniform(0.35, 0.55))
                mat.set_principled_shader_value("Metallic", 0.0)
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
            # Spawn lying on side (X ~ 90°) with small wobble + random spin around
            # the long axis. After physics settles, most bottles stay horizontal
            # so their cylinder labels are visible from the top-down camera.
            dup.set_rotation_euler([
                math.pi / 2 + rng.uniform(-0.3, 0.3),
                rng.uniform(-0.2, 0.2),
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


# Blender camera frame:  +X right,  +Y up,   -Z forward (into scene)
# OpenCV camera frame:    +X right,  -Y down, +Z forward (into scene)
# Suction GT exports points in OpenCV convention (back-projected from depth);
# pose export must match. This 4x4 flips Y and Z to convert.
T_BLENDER_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])


def extract_pose_cam(bottle_obj) -> tuple[np.ndarray, np.ndarray]:
    """Compose object→camera-frame transform from world-frame matrices.
    See docs/pose_export_plan.md for conventions: R is 3x3 right-handed (det=+1),
    t in meters, both in camera frame matching `point_3d_cam` units used by suction GT.

    Two corrections applied:
      1. T_obj_to_world bakes object scale (set_scale([s,s,s]) = unit_scale=0.001)
         into its 3x3 block. We strip uniform scale by normalizing each column —
         valid because all bottle scales are uniform.
      2. Blender camera frame has +Z out-of-scene; OpenCV (and our suction GT)
         has +Z into-scene. We apply T_BLENDER_TO_CV to align."""
    import bpy
    T_obj_to_world = np.array(bottle_obj.get_local2world_mat())
    T_cam_to_world = np.array(bpy.context.scene.camera.matrix_world)
    T_obj_to_cam_blender = np.linalg.inv(T_cam_to_world) @ T_obj_to_world
    T_obj_to_cam = T_BLENDER_TO_CV @ T_obj_to_cam_blender
    R_with_scale = T_obj_to_cam[:3, :3]
    col_norms = np.linalg.norm(R_with_scale, axis=0)
    if np.any(col_norms < 1e-9):
        raise ValueError(f"degenerate scale: column norms {col_norms}")
    R = R_with_scale / col_norms[np.newaxis, :]
    if np.linalg.det(R) < 0:
        R = -R
    t = T_obj_to_cam[:3, 3]
    return R, t


def extract_bbox_3d_mm(bottle_obj) -> list[float]:
    """Local-frame bounding-box dimensions (w, d, h) in mm. Bottle object frame
    has +Z up after bake_y_to_z_rotation; w=x-extent, d=y-extent, h=z-extent."""
    import bpy
    blender_obj = bottle_obj.blender_obj
    bb = np.array([list(c) for c in blender_obj.bound_box])  # 8 corners, local frame
    dims_local = bb.max(axis=0) - bb.min(axis=0)
    # bound_box ignores object scale, so apply it manually; convert m -> mm.
    scale = np.array(blender_obj.scale)
    return [round(float(d * s * 1000.0), 3) for d, s in zip(dims_local, scale)]


def build_pose_lookup(placed: list) -> dict:
    """Returns {blender_object_name: {"R": ..., "t": ..., "bbox_3d_mm": ...}}.
    Built once per scene; reused when assembling per-instance dicts."""
    out = {}
    for obj in placed:
        try:
            R, t = extract_pose_cam(obj)
            bbox_3d_mm = extract_bbox_3d_mm(obj)
        except Exception as e:
            print(f"[warn] pose extraction failed for {obj.get_name()}: {e}")
            continue
        out[obj.get_name()] = {
            "R": [[round(float(R[i, j]), 6) for j in range(3)] for i in range(3)],
            "t": [round(float(t[i]), 6) for i in range(3)],
            "object_up_axis": [0, 0, 1],          # +Z up in object frame after bake_y_to_z_rotation
            "object_frame_unit": "mm",            # OBJ vertex coords are in mm
            "bbox_3d_mm": bbox_3d_mm,
        }
    return out


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
    from suction_gt import compute_suction_gt, make_suction_meta

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

    # Per-instance 6-DOF pose in camera frame. See docs/pose_export_plan.md.
    pose_lookup = build_pose_lookup(placed)

    instances = []
    for entry in attrs:
        inst_id = entry["idx"]
        # `name` is the per-instance Blender object name ("레보진시럽_02"); used
        # as the lookup key into amodal_masks AND pose_lookup. `class_name`
        # (from cp_class_name) is the clean per-class label exposed to
        # consumers of scene_gt.json.
        name = entry.get("name", f"inst_{inst_id}")
        class_name = entry.get("class_name") or name
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
            "class_name": class_name,
            "category_id": int(cat_id),
            "visible_mask": f"visible_masks/{fname}",
            "amodal_mask": f"amodal_masks/{fname}",
            "occlusion_mask": f"occlusion_masks/{fname}",
            "visible_px": visible_px,
            "amodal_px": amodal_px,
            "occlusion_rate": round(occlusion_rate, 4),
            "bbox_xywh_amodal": bbox,
            "pose_cam": pose_lookup.get(name),  # None if pose extraction failed
        })

    # --- Suction-point GT (V1 stub for wiring verification)
    camera_K_np = np.array([
        [cfg["camera"]["fx"], 0, cfg["camera"]["cx"]],
        [0, cfg["camera"]["fy"], cfg["camera"]["cy"]],
        [0, 0, 1],
    ], dtype=np.float64)
    visible_masks_for_gt = {}
    for inst in instances:
        inst_id = inst["instance_id"]
        visible_masks_for_gt[inst_id] = (seg == inst_id).astype(np.uint8) * 255

    import time as _time
    _t0 = _time.perf_counter()
    suction_per_instance = compute_suction_gt(
        placed_bottles=placed,
        visible_masks=visible_masks_for_gt,
        depth_m=depth_m,
        camera_K=camera_K_np,
    )
    print(f"[time] compute_suction_gt: {_time.perf_counter() - _t0:.2f}s "
          f"({sum(len(v) for v in suction_per_instance.values())} points across "
          f"{len(suction_per_instance)} instances)")
    for inst in instances:
        inst["suction_points"] = suction_per_instance.get(inst["instance_id"], [])

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
        "suction_meta": make_suction_meta(),
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

    # 3. load label textures + bottles, physics-drop
    labels_dir = (repo_root / cfg["meshes"].get("labels_dir", "textures/labels")).resolve()
    label_pool = load_label_pool(labels_dir)
    print(f"[info] loaded {len(label_pool)} label textures from {labels_dir}")
    placed = load_and_drop_bottles(mesh_pairs, cfg, label_pool, rng, repo_root, mesh_tmp)
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

    # Full-scene visible instance segmap. Use the cp_ prefix so BlenderProc
    # actually reads our custom properties (set via set_cp); without the prefix
    # it falls back to default_values and every instance gets category_id=-1.
    seg_full = bproc.renderer.render_segmap(
        map_by=["instance", "name", "cp_category_id", "cp_class_name"],
        default_values={"category_id": -1, "class_name": ""},
    )
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
