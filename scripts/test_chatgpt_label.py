import blenderproc as bproc
# One-off preview render. Force-applies label_048_chatgpt_clearcough.png
# to one procedural bottle (white_pill_bottle) and renders a side view so
# the full label is visible. Output: /tmp/test_chatgpt_label.png
#
# Run:
#     blenderproc run scripts/test_chatgpt_label.py
import math
import random
import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).parent))
from generate_scene import bake_y_to_z_rotation, make_label_material, smart_unwrap

REPO = Path(__file__).resolve().parents[1]
BOTTLE_OBJ = REPO / "sample_data" / "bottles" / "white_pill_bottle" / "mesh.obj"
LABEL_PNG = REPO / "textures" / "labels" / "label_048_chatgpt_clearcough.png"
OUT = Path("/tmp/test_chatgpt_label.png")


def main():
    bproc.init()

    loaded = bproc.loader.load_obj(str(BOTTLE_OBJ))
    bottle = loaded[0]
    bottle.set_scale([0.001, 0.001, 0.001])
    bottle.set_name("test_bottle")

    smart_unwrap(bottle)

    rng = random.Random(0)
    body_tint = (0.96, 0.96, 0.95)
    mat = make_label_material("test_mat", LABEL_PNG, body_tint, rng)
    bottle.replace_materials(mat)

    bb = [bottle.blender_obj.matrix_world @ mathutils.Vector(c)
          for c in bottle.blender_obj.bound_box]
    zs = [v.z for v in bb]
    bottle.set_location([0, 0, -min(zs)])

    bproc.renderer.set_world_background([1, 1, 1], strength=0.4)

    for loc in [(0.4, -0.4, 0.4), (-0.4, -0.25, 0.3), (0, -0.1, 0.5)]:
        light = bproc.types.Light()
        light.set_type("AREA")
        light.set_location(list(loc))
        light.set_energy(80)
        light.set_color([1, 1, 1])

    import numpy as np
    fx = fy = 1200.0
    cx = cy = 512.0
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
    bproc.camera.set_intrinsics_from_K_matrix(K, 1024, 1024)
    h = max(zs) - min(zs)
    cam_pose = bproc.math.build_transformation_mat(
        [0, -0.30, h / 2 + 0.02], [math.radians(90), 0, 0]
    )
    bproc.camera.add_camera_pose(cam_pose)

    data = bproc.renderer.render()
    from PIL import Image
    Image.fromarray(data["colors"][0]).save(OUT)
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
