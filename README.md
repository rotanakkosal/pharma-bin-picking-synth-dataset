# uoais-synth-bin-scenes

BlenderProc pipeline that generates cluttered bin-picking scenes (RGB + depth + visible / amodal / occlusion masks) from 3D-scanned Korean pharmaceutical bottles, for training [UOAIS-Net](https://github.com/gist-ailab/uoais).

Each rendered scene mimics the real Intel RealSense L515 capture setup: 1920x1080 top-down view, camera ~1.286 m above a workspace, same intrinsics as the physical rig (see `sample_data/`).

---

## What you get per scene

```
output/scene_NNNNNN/
├── rgb/0000.png              # 1920x1080 RGB (Cycles 64 spp)
├── depth/0000.png            # uint16 PNG in millimeters
├── visible_masks/            # per-instance, 0/255
├── amodal_masks/             # per-instance, full shape even if occluded
├── occlusion_masks/          # amodal minus visible
└── scene_gt.json             # bbox, occlusion rate, class name per instance
```

Physics drops 20–30 bottles of 4 classes into a virtual blue tray. 25–30% of instances end up partially occluded (useful signal for UOAIS amodal supervision).

---

## Setup

Requires Python 3.10 or 3.11 (not 3.12). Uses [uv](https://github.com/astral-sh/uv) for the venv.

```bash
cd synthetic_dataset_generate

uv venv .venv_synth --python 3.11
source .venv_synth/bin/activate

uv pip install blenderproc==2.8.0 numpy pillow opencv-python-headless pyyaml tqdm scipy

blenderproc quickstart   # one-time Blender download (~700 MB, cached to ~/blender/)
```

Verify:
```bash
blenderproc --version     # 2.8.0
```

---

## Generate scenes

Single scene:
```bash
blenderproc run scripts/generate_scene.py --config scripts/config.yaml --scene-id 1
```

Batch of N scenes (each runs in its own Blender process, which BlenderProc requires):
```bash
bash scripts/run_batch.sh 10          # scenes 1..10
bash scripts/run_batch.sh 100 500     # scenes 500..599
```

Expect ~70 seconds per scene on an RTX A6000 (about 70% of that is the per-instance amodal mask pass).

---

## Reproduce the reference scene

Use this to sanity-check after any change:
```bash
bash scripts/run_batch.sh 1 42
```

You should get `output/scene_000042/` containing roughly 25 instance entries in `scene_gt.json`, with the same folder layout shown above.

---

## Configuration

All scene knobs live in `scripts/config.yaml`:

| Section | Knob | Default | Effect |
|---|---|---|---|
| `meshes` | `copies_per_mesh` | 8 | total bottles = 4 × this |
| `tray` | `inner_w` / `inner_d` | 0.70 / 0.45 m | tray footprint |
| `tray` | `wall_h` | 0.12 m | tray wall height |
| `drop` | `z_range` | [0.30, 0.60] | drop height above tray floor |
| `camera` | `height_m` | 1.286 | matches real L515 extrinsics |
| `camera` | `jitter_xy_m`, `jitter_rot_deg` | 0.02, 2.0 | camera variety per scene |
| `lighting` | `n_lights`, `energy_range` | 3, [40, 120] | point-light count and brightness |
| `render` | `samples` | 64 | Cycles path-traced samples — bump to 256 for final data |
| `output` | `seed` | 42 | deterministic randomization per scene_id |

---

## Source data

`sample_data/` contains the reference captures from the data capture team:

| Path | Contents |
|---|---|
| `Medicine box OBJ_260324/2026-04-22/` | Four 3D-reconstructed bottle meshes (`.obj`, Rhino-exported) |
| `Medicine box RGBD/2026-04-22/` | Real L515 RGB + depth captures, camera intrinsics / extrinsics, ArUco config |

The scripts read camera intrinsics from `sample_data/Medicine box RGBD/2026-04-22/camera.json` for reference; values are currently mirrored in `scripts/config.yaml`.

---

## Known limitations

- **No `.mtl` / textures.** The OBJ files reference material libraries that weren't provided by the capture team. Bottles render with random pastel plastic materials — good enough for training the mask/occlusion heads, but there will be a visible sim-to-real gap on the RGB branch. Two paths forward:
  1. Capture team ships the `.mtl` + texture images — wire them into `load_and_drop_bottles()`.
  2. Procedurally generate fake pharmaceutical labels (colored strip + barcode + text blocks) and UV-project onto the cylindrical bottles.
- **Amodal pass is the bottleneck.** 70% of render time is sequential per-instance segmap renders. Can be cut with (a) parallel scene generation, (b) skipping amodal for unoccluded bottles.
- **A few bottles occasionally escape the tray** in bouncy physics settles. Still valid instance annotations; not a blocker.

---

## Versions

| Tag | State |
|---|---|
| `v0.1.0-no-textures` | End-to-end pipeline works. Procedural pastel materials, no real textures. |

---

## Layout

```
synthetic_dataset_generate/
├── scripts/
│   ├── config.yaml               # all scene / render / camera knobs
│   ├── generate_scene.py         # main BlenderProc script
│   └── run_batch.sh              # batch runner
├── sample_data/                  # capture-team reference data (OBJs + real RGBD)
└── output/                       # generated scenes (gitignored)
```
