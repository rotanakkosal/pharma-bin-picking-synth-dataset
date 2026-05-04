import blenderproc as bproc
# Studio-style preview render for each bottle in sample_data/bottles/.
# Each preview lands at sample_data/bottles/<id>/preview.png — quick visual
# reference for what each canonical bottle looks like.
#
# Run:
#     blenderproc run scripts/render_bottle_previews.py

import math
import random
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parents[1]
BOTTLES_DIR = REPO / "sample_data" / "bottles"
LABELS_DIR = REPO / "textures" / "labels"
RES = 1024
SAMPLES = 256


def reset_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)
    for img in list(bpy.data.images):
        if img.users == 0:
            bpy.data.images.remove(img, do_unlink=True)


def setup_world() -> None:
    world = bpy.context.scene.world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (0.82, 0.82, 0.82, 1.0)
    bg.inputs["Strength"].default_value = 0.6


def add_seamless_backdrop() -> None:
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 0.5, 0))
    floor = bpy.context.object

    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 1.5, 1.5))
    wall = bpy.context.object
    wall.rotation_euler = (math.radians(90), 0, 0)

    mat = bpy.data.materials.new("preview_backdrop")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.93, 0.93, 0.93, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.95
    for o in (floor, wall):
        o.data.materials.clear()
        o.data.materials.append(mat)


def add_lights() -> None:
    """Low-energy three-point — calibrated for AgX view transform on a small
    (~10cm) subject. Higher energies caused total whiteout."""
    bpy.ops.object.light_add(type="AREA", location=(0.3, -0.4, 0.4))
    key = bpy.context.object
    key.data.size = 0.5
    key.data.energy = 4
    key.rotation_euler = (math.radians(45), 0, math.radians(35))

    bpy.ops.object.light_add(type="AREA", location=(-0.4, -0.25, 0.3))
    fill = bpy.context.object
    fill.data.size = 0.6
    fill.data.energy = 2
    fill.rotation_euler = (math.radians(60), 0, math.radians(-35))

    bpy.ops.object.light_add(type="AREA", location=(0, -0.1, 0.5))
    top = bpy.context.object
    top.data.size = 0.6
    top.data.energy = 1.5


def add_camera(target_z: float, bottle_extent: float) -> None:
    """Frame the bottle to fill ~60% of vertical frame with a 50mm lens."""
    # 50mm vertical FOV ~27°; for the bottle to fill 60% of frame, we need
    # distance ≈ bottle_extent / (2 * tan(13.5°) * 0.6) ≈ bottle_extent * 3.1
    distance = max(0.30, bottle_extent * 3.5)
    cam_y = -distance
    cam_z = target_z

    bpy.ops.object.camera_add(location=(0, cam_y, cam_z))
    cam = bpy.context.object
    cam.data.lens = 50

    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0, 0, target_z))
    target = bpy.context.object

    constraint = cam.constraints.new(type="TRACK_TO")
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"

    bpy.context.scene.camera = cam


def make_procedural_label_material(name: str, label_path: Path) -> bpy.types.Material:
    """Label PNG used directly as Base Color — no body-tint multiplication.
    Multiplication was making labels look ghostly; for preview clarity we
    show the label at full saturation."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes["Principled BSDF"]

    img = bpy.data.images.load(str(label_path))
    img_node = nodes.new("ShaderNodeTexImage")
    img_node.image = img
    img_node.location = (-400, 0)
    links.new(img_node.outputs["Color"], bsdf.inputs["Base Color"])

    bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def cylinder_unwrap_aligned(obj: bpy.types.Object) -> None:
    """Cylinder-unwrap, then offset UVs so the texture's center (where the
    main label content typically sits) lands on the camera-facing side."""
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.cylinder_project(
        direction="ALIGN_TO_OBJECT",
        align="POLAR_ZX",
        scale_to_bounds=True,
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    # Shift UVs by 0.5 in U so the texture seam lands behind, label content faces front.
    me = obj.data
    uv_layer = me.uv_layers.active.data
    for loop in uv_layer:
        loop.uv[0] = (loop.uv[0] + 0.5) % 1.0


def pick_procedural_label(bottle_id: str) -> Path | None:
    pool = sorted(p for p in LABELS_DIR.glob("label_*.png") if "fullcolor" not in p.name)
    if not pool:
        return None
    rng = random.Random(bottle_id)
    return rng.choice(pool)


def fix_photoreal_material(obj: bpy.types.Object, label_path: Path) -> None:
    """For photoreal bottles, replace the auto-imported MTL material with a
    clean Principled BSDF using label.png directly. The MTL-imported material
    sometimes has Kd/Ka values that desaturate the texture; bypassing it
    guarantees the photoreal label renders at full saturation."""
    img = bpy.data.images.load(str(label_path))

    mat = bpy.data.materials.new(f"{obj.name}_photo")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]

    img_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    img_node.image = img
    img_node.location = (-400, 0)
    mat.node_tree.links.new(img_node.outputs["Color"], bsdf.inputs["Base Color"])

    bsdf.inputs["Roughness"].default_value = 0.45

    obj.data.materials.clear()
    obj.data.materials.append(mat)


def load_bottle(bottle_dir: Path) -> tuple[bpy.types.Object, float, float]:
    uv_path = bottle_dir / "mesh_uv.obj"
    plain_path = bottle_dir / "mesh.obj"
    label_path = bottle_dir / "label.png"
    use_textured = uv_path.exists() and label_path.exists()
    mesh_path = uv_path if use_textured else plain_path

    bpy.ops.wm.obj_import(filepath=str(mesh_path))
    obj = bpy.context.selected_objects[0]
    obj.scale = (0.001, 0.001, 0.001)
    obj.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY")
    obj.location = (0, 0, 0)

    zs = [(obj.matrix_world @ __import__("mathutils").Vector(c)).z for c in obj.bound_box]
    xs = [(obj.matrix_world @ __import__("mathutils").Vector(c)).x for c in obj.bound_box]
    ys = [(obj.matrix_world @ __import__("mathutils").Vector(c)).y for c in obj.bound_box]
    half_h = (max(zs) - min(zs)) / 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    obj.location = (0, 0, half_h)
    target_z = half_h

    if use_textured:
        fix_photoreal_material(obj, label_path)
    else:
        label = pick_procedural_label(bottle_dir.name)
        if label is not None:
            cylinder_unwrap_aligned(obj)
            mat = make_procedural_label_material(f"{bottle_dir.name}_proc", label)
        else:
            mat = bpy.data.materials.new(f"{bottle_dir.name}_hdpe")
            mat.use_nodes = True
            mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.95, 0.95, 0.94, 1.0)
        obj.data.materials.clear()
        obj.data.materials.append(mat)

    return obj, target_z, extent


def configure_render(out_path: Path) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = RES
    scene.render.resolution_y = RES
    scene.render.engine = "CYCLES"
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    # AgX (Blender 4.x default) — has highlight rolloff so bright lights
    # don't blow out into pure white. Standard view transform was a mistake;
    # without rolloff, any moderately bright light = whiteout.
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Base Contrast"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(out_path)


def main() -> None:
    bproc.init()
    if not BOTTLES_DIR.exists():
        raise SystemExit(f"missing {BOTTLES_DIR}")

    bottle_dirs = sorted(p for p in BOTTLES_DIR.iterdir() if p.is_dir() and (p / "mesh.obj").exists())
    print(f"Rendering studio previews for {len(bottle_dirs)} bottles -> {RES}x{RES} @ {SAMPLES} samples")

    for bd in bottle_dirs:
        reset_scene()
        setup_world()
        add_seamless_backdrop()
        _, target_z, extent = load_bottle(bd)
        add_lights()
        add_camera(target_z, extent)
        out = bd / "preview.png"
        configure_render(out)
        bpy.ops.render.render(write_still=True)
        print(f"  {bd.name:<28s} -> {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
