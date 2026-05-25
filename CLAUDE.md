# CLAUDE.md — Guide for AI assistants in this repo

This file gives AI coding assistants the project context, conventions, and
guardrails needed to work effectively here. Read this first, then `README.md`
for dataset/schema details and `docs/README.md` for the design-doc index.

---

## 1. Project state — read before doing anything

🔒 **TERMINAL at `v1.1-realistic-labels` (2026-05-12). Reactive-only.**

- The synth dataset generator is **done for development**. No proactive realism
  work, no speculative sweeps, no new HDRI/material/lighting experiments, no
  calibration against "future real captures" (there are none in dev — they are
  a production-only resource).
- The benchmark evaluation track was **CLOSED 2026-05-17** (see
  `docs/benchmark/benchmark_findings.md`).
- **Only act on this codebase if a downstream consumer (UOAIS, robot
  integration, the picking-arm project) reports a concrete failure that traces
  back here.** If the request is ambiguous, ask before touching renders.
- All P-items closed (P0/P1/P2/P3 shipped, P4 deferred, P4-lite shipped).
  Source of truth: `docs/synth_realism_improvement_plan.md`.

If a user asks for "another sweep" / "more variety" / "let's see what HDRI
does" — flag the terminal state and ask whether this is reactive work tied to
a real downstream failure, before spending compute.

---

## 2. What this repository is

A synthetic benchmark for **suction-cup grasp prediction** on cluttered piles
of Korean pharmaceutical bottles. Each scene = RGB + depth + amodal/visible/
occlusion masks + 6-DOF pose + analytical suction-grasp GT, packaged in
`scene_gt.json`. Camera mimics an Intel RealSense L515 (1920×1080, 1.286 m
top-down).

**It is a benchmark, not training data.** The headline evaluation is `AP_μ`
(SuctionNet convention) for grasp algorithms. Instance-segmentation scoring of
UOAIS predictions is a separate, secondary check.

7 bottle classes, ~34 visible instances per scene. Render cost: ~95 s per
scene on an RTX A6000.

---

## 3. Repo layout

```
.
├── README.md                  # dataset overview, schema, eval protocol
├── CLAUDE.md                  # this file
├── scripts/
│   ├── config.yaml            # all scene/render/camera knobs
│   ├── generate_scene.py      # main BlenderProc script
│   ├── run_batch.sh           # batch runner
│   ├── depth_io.py            # core: depth save/load (centralized unit handling)
│   ├── depth_noise.py         # core: L515 v2 noise model
│   ├── suction_gt.py          # core: V1.5 suction-point GT (Sseal x Swrench)
│   ├── eval/
│   │   ├── dataset_qc.py             # 14 integrity invariants + coverage report
│   │   ├── eval_uoais_on_synth.py    # Hungarian visible-match, F@.75, stratified
│   │   └── eval_centroid_on_synth.py # 4c centroid-grasp analytic eval
│   ├── viz/                          # overlays, previews, noise demos
│   ├── convert/                      # format conversion (Sim-Suction)
│   └── archive/                      # obsolete scripts (kept for reference)
├── docs/                      # design docs grouped by topic — see docs/README.md
├── textures/
│   ├── labels/                # 32 active label PNGs (label_*.png, "fullcolor" excluded)
│   └── labels_distractors/    # quarantined off-domain labels (DO NOT load)
├── sample_data/               # bottle meshes — GITIGNORED, not in this clone
└── output/                    # generated scenes — GITIGNORED
```

`sample_data/` and `output/` are intentionally **not in git**. Render
generation requires `sample_data/bottles/<id>/mesh.obj` (and optionally
`mesh_uv.obj` + `label.png` for the two photoreal classes) to exist locally;
see `docs/sample_data/sample_data_naming_convention.md` for the layout.

---

## 4. Locked baselines — DO NOT CHANGE without explicit approval

Calibrated to real Intel L515 hardware. Changing them breaks Layer-3
predictive validity, which is the whole point of the benchmark.

| Knob | Locked value | Why |
|---|---|---|
| `camera.height_m` (`heights_m`) | `1.286` | Matches real L515 mounting |
| `camera.fx/fy/cx/cy/width/height` | L515 intrinsics from capture rig | Sim-to-real correlation requires identical intrinsics |
| `render.depth_unit_mm` / v2 `depth_unit_m=0.00025` | L515 native 0.25 mm bins | Consumers expect L515-format depth |

Sweeping any locked value to "explore" is wasted compute and out of scope.

---

## 5. Coordinate conventions (critical — easy to get wrong)

- **All 3D quantities use the OpenCV camera frame**: +X right, +Y down image,
  +Z into the scene. Applies to `point_3d_cam`, `pose_cam.t`, `normal_cam`,
  back-projected depth.
- **Blender's camera frame is different** (+X right, +Y up, -Z forward).
  `generate_scene.py` defines `T_BLENDER_TO_CV = np.diag([1, -1, -1, 1])` and
  applies it in `extract_pose_cam()`. Any new code touching pose extraction
  must apply the same transform.
- **Units**: translations and 3D points are in **meters**. Fields with the
  `_mm` suffix are in **millimeters** (`depth` PNG, `torque_arm_mm`,
  `flatness_residual_mm`, `bbox_3d_mm`, `cup_radius_mm`, etc.).
- **Depth PNG**: uint16, stored in L515 native 0.25 mm bins (v2-l515 format).
  `depth_in_meters = png_value * depth_unit_m` (`depth_unit_m = 0.00025`).
  Legacy v1 scenes used integer mm (`depth_unit_m = 0.001`).
- **Always go through `scripts/depth_io.py:load_depth_m()` to read depth.**
  Never hardcode `png_value / 1000.0` — the v1→v2 migration silently 4× breaks
  any consumer that does.
- **Object frame**: bottles' local +Z is up after `bake_y_to_z_rotation`. OBJ
  vertex coords are in mm; `unit_scale = 0.001` scales them to Blender meters.

---

## 6. How to run things

### Single scene
```bash
blenderproc run scripts/generate_scene.py --config scripts/config.yaml --scene-id 1
```

### Batch
```bash
bash scripts/run_batch.sh 50          # scenes 1..50
bash scripts/run_batch.sh 100 500     # scenes 500..599
```

Each scene runs in its own Blender process (BlenderProc requirement). Output
goes to `output/h_<camera_height>/scene_NNNNNN/`. Logs land in
`output/scene_NNNNN.log`.

### QC
```bash
python scripts/eval/dataset_qc.py --output-dir output
```
Validates 14 integrity invariants (8 suction GT + 6 pose + mask containment)
plus class balance, occlusion histogram, score distributions. A clean run
shows **all zeros** in the integrity sections — surface non-zero numbers
explicitly when reporting back.

### Visualize suction GT overlay
```bash
python scripts/viz/viz_suction.py --scene output/scene_000001 --top 5 --cup-radius
```

### UOAIS evaluation
```bash
python scripts/eval/eval_uoais_on_synth.py \
    --synth-output-dir output/h_1.286 \
    --uoais-out ../pharma-bin-picking/output/synth_v1.1/h_1.286
```
Eval protocol: one Hungarian match per scene on **visible** masks at IoU ≥
0.5, three views derived from it (detection / F@.75 Dice / occlusion-
stratified). Old greedy-amodal-match numbers still printed as legacy
continuity check. See `docs/eval/eval_methodology.md`.

### Environment
Python 3.10 or 3.11 (not 3.12). [uv](https://github.com/astral-sh/uv) for the
venv. BlenderProc 2.8.0.
```bash
uv venv .venv_synth --python 3.11
source .venv_synth/bin/activate
uv pip install blenderproc==2.8.0 numpy pillow opencv-python-headless pyyaml tqdm scipy
blenderproc quickstart    # one-time Blender download (~700 MB, cached to ~/blender/)
```

`run_batch.sh` activates `.venv_synth/` automatically. Long renders (>30 s)
should be **handed to the human operator as a copy-paste command**, not
background-launched by an agent.

---

## 7. Output layout & schema (the contract for downstream consumers)

```
output/h_<camera_height_m>/scene_NNNNNN/
├── rgb/0000.png              # 1920×1080 sRGB (Cycles, 64 spp)
├── depth/0000.png            # uint16, L515 0.25 mm bins (v2-l515)
├── visible_masks/0000_<inst_id>.png    # 0/255 — what the camera sees
├── amodal_masks/0000_<inst_id>.png     # 0/255 — full silhouette
├── occlusion_masks/0000_<inst_id>.png  # amodal AND NOT visible
└── scene_gt.json             # all annotations + metadata
```

Full `scene_gt.json` schema is in `README.md`. The non-obvious bits:

- `instances[].class_name` ∈ {`kolmin_a_syrup`, `levozin_syrup`,
  `blue_cap_pill_bottle`, `white_pill_bottle`, `pill_jar`,
  `medicine_bottle_a`, `medicine_bottle_b`}. Two photoreal-label classes
  (`kolmin_a_syrup`, `levozin_syrup`) use real-product UV-mapped textures; the
  rest get a random procedural label from `textures/labels/` per scene.
- `instances[].suction_points` is sorted by `S_combined_default` desc, capped
  at top-50, NMS-filtered (V1.5, min 5 mm spacing).
- `suction_meta.version` should read `"v1.5"`. Older renders may say `"v1"`.
- `depth_unit_m` is the BOP-convention depth scale. Always honor it via
  `depth_io.py`; never assume mm.

**Algorithms must not assume label content** (text, color). Only geometry.

---

## 8. Code conventions

- **Python ≥ 3.10 typing.** `from __future__ import annotations` at the top
  of new modules; `list[...]`, `dict[...]`, `tuple[...]` (not
  `typing.List`).
- **Docstrings live on the module and on non-trivial functions.** Several
  modules carry literature citations + design rationale in module-level
  docstrings (`suction_gt.py`, `depth_noise.py`, `depth_io.py`,
  `eval_uoais_on_synth.py`). Keep that style: cite the paper, name the
  decision, don't restate what the code does.
- **Comments**: explain WHY (a hidden invariant, a unit gotcha, a fix for a
  specific bug). Don't restate the code.
- **Random seeds are deterministic.** Spawn-position RNG = `cfg.seed +
  scene_id`. Depth-noise RNG = `cfg.seed + scene_id + 10007` (the offset
  decorrelates from spawn). Preserve this when adding new stochastic steps.
- **Korean filenames are forbidden in the active codebase.** The
  `ensure_ascii_mesh_copies()` and `stage_textured_mesh()` helpers exist to
  ASCII-stage any vendor-delivered Korean OBJ paths. New code must not
  reintroduce non-ASCII paths.
- **No `*.md` files outside `docs/` (or `README.md`/`CLAUDE.md` at root).**
  Don't dump new docs at the `docs/` root; pick the right subdirectory or
  create one for a brand-new topic. Filenames are self-describing
  (`<topic>_<role>.md`). Every doc has a `Date:` + `Status:` block. Update
  `docs/README.md` when adding a doc.

---

## 9. Team workflow rules (apply to AI sessions too)

From `docs/team/team_workflow.md`:

**Before any render, answer in one sentence each:**
1. Which P-item does this advance? (Almost certainly "none — terminal state.")
2. What decision does the result drive? If "none, we already know", **don't
   render.**
3. What's it going to cost? (`#scenes × ~95 s` on A6000. >5 min needs
   explicit user sign-off.)

**Out-of-plan work** (e.g. "what if we tried HDRI?"): don't render. Write a
proposal in `docs/proposals/<topic>.md` (motivation, P-item it would feed,
compute cost, decision driven) and ask the user for approval first.

**Don't `rm -rf output/`** without confirming — there may be reference
batches needed for before/after comparisons.

**After finishing a change, before saying "done":**
1. Update the status snapshot in `docs/synth_realism_improvement_plan.md`.
2. Verify docstring usage examples still match the script's actual path.
3. If config changed, note the version bump in `README.md`'s version table.
4. Run `python scripts/eval/dataset_qc.py` and report the result (or say
   explicitly that you couldn't run it because no scenes exist).

---

## 10. Git workflow

- Default branch in this environment: as specified in the session prompt
  (e.g. `claude/claude-md-docs-*`). Develop there; do not push to other
  branches without explicit permission.
- Commits use lowercase prefixes: `feat:`, `fix:`, `docs:`, `refactor:`,
  `eval:`, `feat(suction-gt):`, `feat(labels):`, etc. Look at `git log` for
  prior style.
- The version table in `README.md` is the dataset changelog. Earlier
  milestones are git-tagged (`v0.1.0-no-textures`, `v0.2-suction+pose`,
  `v0.2.5-suction-v1.5`, `v0.4-p1-shipped`, `v1.0-final`,
  `v1.1-realistic-labels`); newer logical milestones are documented in the
  table only.
- **Do not create PRs unless the user explicitly asks for one.**

---

## 11. Things that look like bugs but aren't

- **~5% of bottles escape the tray** during physics and land on the floor.
  Their masks/poses are still valid. Don't use scene "bottle count" as a
  quality signal — use `len(instances)`.
- **`category_id = 0`** is tray/ground. The instance loop in
  `save_outputs()` correctly skips these; don't "fix" the skip.
- **Per-instance amodal mask pass takes ~70 s of the ~95 s render budget.**
  This is intentional — one extra segmap render per instance is the only
  reliable way to get amodal masks under BlenderProc. Don't try to
  "optimize" by removing the per-instance pass.
- **Two photoreal classes have noticeably higher quality than the 5
  procedural ones.** That's by design (real-product photos vs. random
  ChatGPT-generated labels); domain match is honestly disclosed in
  `README.md` "Known limitations".
- **White-on-white separation drops UOAIS recall to 0.50–0.57 on three
  featureless white classes.** Diagnosed as model-side (UOAIS underweights
  depth when RGB contrast is low), not a depth-preprocessing artifact. The
  inter-bottle depth gradient survives the pipeline cleanly. See
  `docs/eval/eval_methodology.md` and `docs/benchmark/benchmark_findings.md`.
  Not a synth-side problem to chase.

---

## 12. Where to read more

- **Dataset usage / schema / eval protocol** → `README.md`
- **Project history + every P-item status** → `docs/synth_realism_improvement_plan.md`
- **Doc index (every design doc)** → `docs/README.md`
- **Team process** → `docs/team/team_workflow.md`
- **Eval methodology + headline numbers** → `docs/eval/eval_methodology.md`
- **Canonical benchmark findings** → `docs/benchmark/benchmark_findings.md`
- **Suction GT design** → `docs/suction_gt/`
- **L515 depth noise model** → `docs/depth_noise/`
- **6-DOF pose conventions** → `docs/pose_export/pose_export_design.md`
- **Sample-data layout** → `docs/sample_data/sample_data_naming_convention.md`
