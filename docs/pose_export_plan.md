# 3D Bottle Pose Export — Design + Implementation Plan

Date: 2026-05-05
Companion docs:
- [suction_point_gt_plan.md](suction_point_gt_plan.md) — suction GT (already implemented as V1)
- [v1_implementation_plan.md](v1_implementation_plan.md) — V1 scope and acceptance criteria

Status: Ready to implement. Small change (~30 LoC), no new dependencies.

---

## Why this matters (alignment with project goals)

From `project_synthetic_dataset.md` (memory) **known gaps**:
> *"No 3D bottle pose export — pose-variety coverage untestable."*

From `suction_point_gt_plan.md` §"Implementation phases":
> *V1 ships analytical suction GT. Pose was not part of V1 scope but is a known coverage gap.*

From the literature-backed evaluation framework (`project_synth_evaluation_framework.md`) **Layer 2 (Coverage characterization)**:
> *"Report mesh diversity, occlusion-rate distribution, instance counts as first-class metrics."*

**Pose variety is the missing piece of Layer 2.** Without pose export, we can't quantitatively report things like "the dataset contains bottles tilted at angles X° to Y°," which means external benchmark users have no way to know whether our coverage matches their needs. Adding pose export closes this Layer-2 gap with no code-architecture changes.

It also unlocks **6-DOF grasp evaluation** later — algorithms that predict full grasp poses (not just contact points) need GT pose to score against.

## TL;DR — what gets added

For each bottle in `scene_gt.json`:

```json
{
  "instance_id": 8,
  "class_name": "bottle_pill",
  ...existing fields...,
  "pose_cam": {
    "R":             [[r11, r12, r13],
                      [r21, r22, r23],
                      [r31, r32, r33]],   // 3×3 rotation, object→camera
    "t":             [tx, ty, tz],         // translation, meters, camera frame
    "object_up_axis": [0, 0, 1],           // bottle's "up" in its local frame
    "bbox_3d_mm":    [w, d, h]             // bottle's physical dimensions
  }
}
```

Composition: `point_cam = R @ point_obj + t` (standard convention).

## Conventions and unit choices (alignment matters)

| Field | Unit / Convention | Rationale |
|---|---|---|
| `t` (translation) | **meters** | Matches `point_3d_cam` in `suction_points`. Mixing units in the same JSON would invite bugs. |
| `R` (rotation) | 3×3 matrix, **right-handed**, det(R) = +1 | Standard. BOP-Challenge uses this format. Can be converted to quaternion or Euler client-side. |
| Coordinate frame | **camera frame** (camera → +Z into scene, +Y down image) | Matches `point_3d_cam` and `normal_cam` in suction_points. Single frame across the JSON. |
| `bbox_3d_mm` | **millimeters** | Matches our existing mm-scale fields (`flatness_residual_mm`, `torque_arm_mm`, depth in `depth/0000.png`). |
| `object_up_axis` | unit vector in **object's local frame** | Lets consumers reason about "which way is the cap" without loading the mesh. After our `bake_y_to_z_rotation` step, all bottles have +Z up in object frame. |

These choices match BOP-Challenge ([arXiv:2504.02812](https://arxiv.org/abs/2504.02812)) for the (R, t) representation, the SuctionNet/Sim-Suction convention for camera-frame coordinates, and our own existing JSON for units. **No format invention** — every choice traces to existing precedent.

## Algorithm (3 lines of math)

1. Get object's world-frame pose: `T_obj_to_world = obj.get_local2world_mat()` (4×4 from BlenderProc)
2. Get camera's world-frame pose: `T_cam_to_world = bpy.context.scene.camera.matrix_world`
3. Compose: `T_obj_to_cam = inv(T_cam_to_world) @ T_obj_to_world`. Extract R = `[:3, :3]` and t = `[:3, 3]`.

For `bbox_3d_mm`: read once from the original mesh OBJ at load time (we already do this implicitly when scaling — store dimensions in a per-class registry).

## Hook in the pipeline

Insert a new helper in `generate_scene.py`:

```python
def extract_pose_cam(bottle_obj) -> dict:
    """Returns object-to-camera-frame R (3x3) + t (3,) for a placed bottle."""
    import bpy
    T_obj_to_world = np.array(bottle_obj.get_local2world_mat())
    T_cam_to_world = np.array(bpy.context.scene.camera.matrix_world)
    T_obj_to_cam = np.linalg.inv(T_cam_to_world) @ T_obj_to_world
    R = T_obj_to_cam[:3, :3]
    t = T_obj_to_cam[:3, 3]
    return R, t
```

Call it inside `save_outputs()` for each placed bottle, attach to the instance dict alongside the existing fields. The placed list is already keyed by instance name, which we already use to look up amodal masks — same mechanism.

## QC additions

Extend `dataset_qc.py` with three integrity checks (added to the suction-GT block):

| Check | Computation | Expected |
|---|---|---|
| `pose_R_not_orthogonal` | `||R @ R.T - I|| > 1e-3` | 0 |
| `pose_R_det_not_one` | `|det(R) - 1| > 1e-3` | 0 |
| `pose_t_implausible` | `||t|| > 5 m` (bottle further than 5m from camera = bug) | 0 |

## Acceptance criteria

- [ ] Every instance with a visible mask has a non-empty `pose_cam`
- [ ] All R matrices are orthogonal with det = +1 (validated by QC)
- [ ] All translations have plausible magnitude (`||t||` ≈ 1.0–1.4 m, matching camera height + bottle z-positions)
- [ ] Per-scene runtime overhead < 0.1 s (just matrix ops)
- [ ] No new dependencies

## What's explicitly NOT in scope

- ❌ Pose-variety analysis script (separate, future work — uses the new field)
- ❌ Symmetry annotations (cylindrical bottles have rotational symmetry around their up-axis; flagging this is a separate concern, BOP supports it via `symmetries_continuous` in models_info)
- ❌ Multi-frame / video pose tracking (we have single-frame scenes)
- ❌ Pose for tray/ground (only bottles get pose_cam; tray is the world reference frame)

## Risk register

| Risk | Mitigation |
|---|---|
| BlenderProc and Blender camera matrices use different conventions | Verify by computing `R @ object_origin + t` for one bottle and confirming it matches the bottle's centroid in `point_3d_cam` from suction GT |
| `bbox_3d_mm` unknown at save_outputs() time | Read from per-class `mesh_pairs` cache built in `ensure_ascii_mesh_copies()`, or precompute with one bbox query per class at load |
| Object frame ambiguity (bottom-center vs bbox-center) | Document explicitly: origin = OBJ file's origin (bottom-center after our cleanup script), up = +Z in object frame after `bake_y_to_z_rotation` |

## Discovered during implementation (2026-05-05)

Two issues surfaced when running the first end-to-end test on scene 999. Both
fixed and now documented here so future re-implementations don't repeat them:

**Issue 1 — scale baked into the rotation matrix.**
`bottle_obj.get_local2world_mat()` returns a 4×4 that includes the per-axis
scale set via `template.set_scale([s, s, s])` (where `s = unit_scale = 0.001`).
Naively extracting `R = T_obj_to_cam[:3, :3]` gives a matrix with `det(R) ≈
1e-9` instead of 1. **Fix:** normalize each column of the 3×3 block (column
norm equals the per-axis scale factor); the result is the pure rotation. Valid
because all bottle scales are uniform and isotropic. If a future bottle uses
non-uniform scale, the column-normalization extension is straight-forward but
the resulting R may not be exactly orthogonal — consider Gram-Schmidt or
SVD-based polar decomposition then.

**Issue 2 — Blender vs OpenCV camera frame.**
Blender's camera frame has +Z pointing **out of** the scene (away from
objects). OpenCV (and our suction GT, which back-projects from depth)
has +Z pointing **into** the scene. Naively exporting `T_obj_to_cam` from the
Blender world-frame matrices gives `t.z < 0`, inconsistent with `point_3d_cam`
in `suction_points` (which always have `z > 0`).
**Fix:** apply a constant 4×4 conversion `T_BLENDER_TO_CV = diag(1, -1, -1, 1)`
before extracting R and t. This rotates the camera frame 180° around X so
+Z aligns with OpenCV's direction-into-scene.

The QC report now checks `t[2] > 0` as a `pose_t_implausible` violation;
this catches frame-convention regressions immediately.
