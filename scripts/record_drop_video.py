import blenderproc as bproc
# Side-view physics-drop video. Plain white bottles (no textures, no labels)
# so we can render fast and clearly see WHY some bottles escape the tray.
#
# Run:
#     blenderproc run scripts/record_drop_video.py
#
# Output: output/drop_video.mp4 (~2-3 min on A6000)

import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

import bpy
import mathutils

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from generate_scene import (  # noqa: E402
    load_cfg,
    build_ground,
    build_tray,
    bake_y_to_z_rotation,
)

VIDEO_OUT   = REPO / "output" / "drop_video.mp4"
FRAMES_DIR  = REPO / "output" / "_drop_frames"
FPS         = 24
DURATION_S  = 4
RES         = (640, 360)
SAMPLES     = 16
SKIP        = 2          # render every 2nd frame -> 12 fps effective playback


def setup_side_camera() -> None:
    """High-angle 3/4 view: camera ~1m up and to the front-right, looking
    DOWN into the tray at ~50° pitch. Avoids the side-view problem where the
    near wall blocks everything inside."""
    cam_data = bpy.data.cameras.new("DropCam")
    cam_data.lens = 28          # wider lens — fits the whole tray
    cam_obj = bpy.data.objects.new("DropCam", cam_data)
    bpy.context.collection.objects.link(cam_obj)

    cam_loc = mathutils.Vector((0.7, -0.9, 1.0))   # was (0.65, -0.8, 0.45) — much higher
    target  = mathutils.Vector((0.0, 0.0, 0.05))
    cam_obj.location = cam_loc
    direction = target - cam_loc
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    bpy.context.scene.camera = cam_obj


def add_light() -> None:
    light = bproc.types.Light()
    light.set_type("AREA")
    light.set_location([0.5, -0.6, 1.5])
    light.set_energy(300)
    light.set_color([1.0, 0.98, 0.95])

    fill = bproc.types.Light()
    fill.set_type("AREA")
    fill.set_location([-0.4, -0.3, 1.0])
    fill.set_energy(120)


def load_plain_bottles(cfg: dict, rng: random.Random) -> list:
    """Plain white HDPE bottles, no labels. Same drop spawn pattern as
    generate_scene.py so the physics behavior is faithful."""
    s = cfg["meshes"]["unit_scale"]
    drop_cfg = cfg["drop"]
    mesh_dir = (REPO / cfg["meshes"]["dir"]).resolve()

    mesh_pairs = []
    for sub in sorted(mesh_dir.iterdir()):
        if sub.is_dir() and (sub / "mesh.obj").exists():
            mesh_pairs.append((sub.name, sub / "mesh.obj"))

    placed = []
    for class_id, (label, mesh_path) in enumerate(mesh_pairs, start=1):
        loaded = bproc.loader.load_obj(str(mesh_path))
        if not loaded:
            print(f"[warn] failed to load {mesh_path}")
            continue
        template = loaded[0]
        template.set_scale([s, s, s])
        bake_y_to_z_rotation(template)

        mat = bproc.material.create(f"mat_{label}_plain")
        mat.set_principled_shader_value("Base Color", [0.95, 0.95, 0.94, 1.0])
        mat.set_principled_shader_value("Roughness", 0.45)
        template.replace_materials(mat)

        # Park template far away; only duplicates participate in physics.
        template.set_location([10, 10, 10])

        for i in range(cfg["meshes"]["copies_per_mesh"]):
            dup = template.duplicate()
            dup.set_name(f"{label}_{i:02d}")
            dup.set_location([
                rng.uniform(*drop_cfg["x_range"]),
                rng.uniform(*drop_cfg["y_range"]),
                rng.uniform(*drop_cfg["z_range"]),
            ])
            dup.set_rotation_euler([
                math.pi / 2 + rng.uniform(-0.3, 0.3),
                rng.uniform(-0.2, 0.2),
                rng.uniform(0, 2 * math.pi),
            ])
            dup.enable_rigidbody(
                active=True, mass=0.1, friction=0.8, linear_damping=0.1
            )
            placed.append(dup)

    return placed


def bake_physics(end_frame: int) -> None:
    """Bake the rigidbody cache so frame_set(N) reads cached positions instead
    of re-simulating. Important: we match generate_scene.py's solver settings
    (high substeps + iters) so the physics behavior in the video matches the
    real dataset renders. Without this, escape rates differ wildly because
    Blender's default substeps=10 gives unstable wall collisions."""
    scene = bpy.context.scene
    if scene.rigidbody_world is None:
        bpy.ops.rigidbody.world_add()
    scene.rigidbody_world.point_cache.frame_start = 1
    scene.rigidbody_world.point_cache.frame_end = end_frame
    # BlenderProc's simulate_physics() defaults — keeps wall collisions stable.
    scene.rigidbody_world.substeps_per_frame = 25
    scene.rigidbody_world.solver_iterations = 30

    print(f"baking physics (frames 1..{end_frame}, substeps=25)...")
    bpy.ops.ptcache.bake_all(bake=True)


def render_animation_frames() -> None:
    scene = bpy.context.scene
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True)

    n_frames = (scene.frame_end - scene.frame_start) // SKIP + 1
    print(f"rendering {n_frames} frames at {RES[0]}x{RES[1]}, {SAMPLES} samples...")

    for idx, f in enumerate(range(scene.frame_start, scene.frame_end + 1, SKIP), 1):
        scene.frame_set(f)
        scene.render.filepath = str(FRAMES_DIR / f"frame_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
        print(f"  [{idx:2d}/{n_frames}] frame {f}")


def stitch_video() -> None:
    """Frames are rendered. Skip stitching inside Blender (its bundled Python
    can't import imageio from .venv_synth). Print the one-liner for the user
    to run with the venv Python after this script exits."""
    eff_fps = FPS // SKIP
    venv_py = REPO / ".venv_synth" / "bin" / "python"
    print()
    print("=" * 60)
    print(f"frames rendered to: {FRAMES_DIR}")
    print(f"to stitch into video, run:")
    print()
    print(f"  {venv_py} -c \"import imageio.v2 as iio; from pathlib import Path; "
          f"files = sorted(Path('{FRAMES_DIR}').glob('frame_*.png')); "
          f"w = iio.get_writer('{VIDEO_OUT}', fps={eff_fps}, codec='libx264', "
          f"quality=8, pixelformat='yuv420p'); "
          f"[w.append_data(iio.imread(f)) for f in files]; w.close()\"")
    print("=" * 60)


def main() -> None:
    bproc.init()
    cfg = load_cfg(REPO / "scripts" / "config.yaml")

    build_ground(cfg["ground"])
    build_tray(cfg["tray"])

    rng = random.Random(42)
    placed = load_plain_bottles(cfg, rng)
    print(f"loaded {len(placed)} bottles")

    setup_side_camera()
    add_light()

    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = FPS * DURATION_S
    scene.render.resolution_x = RES[0]
    scene.render.resolution_y = RES[1]
    scene.render.engine = "CYCLES"
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.view_settings.view_transform = "AgX"
    scene.render.image_settings.file_format = "PNG"

    bake_physics(scene.frame_end)
    render_animation_frames()
    stitch_video()

    print(f"\ndone -> {VIDEO_OUT}")


if __name__ == "__main__":
    main()
