# docs/ — pharma-bin-picking-synth-dataset

Map of every doc in this directory. Project state: 🔒 **TERMINAL (v1.0-final, 2026-05-11)**.

## Start here

- **[synth_realism_improvement_plan.md](synth_realism_improvement_plan.md)** — canonical project history. P0–P4 priorities, status snapshot, eval results, what's shipped, what's deferred. Read first if you're new to this codebase.

## Feature design + implementation (all shipped)

### Suction-point GT
- **[suction_gt/suction_gt_design.md](suction_gt/suction_gt_design.md)** — V2 literature-backed design (Dex-Net 3.0, SuctionNet-1Billion, Sim-Suction). Analytical Sseal + Swrench scoring.
- **[suction_gt/suction_gt_v1_implementation.md](suction_gt/suction_gt_v1_implementation.md)** — V1 scope + acceptance criteria. Shipped 2026-05-04.
- **[suction_gt/suction_gt_v1_5_refinements.md](suction_gt/suction_gt_v1_5_refinements.md)** — V1.5 refinements: dense plane fit, margin-aware edge clearance, NMS top-K. Shipped 2026-05-06.

### Depth noise (L515)
- **[depth_noise/depth_noise_l515_design.md](depth_noise/depth_noise_l515_design.md)** — L515-specific noise model design. Replaces Lehrmann/Kinect coefficients with L515 axial polynomial, specular/dark/grazing dropouts, 5 mm radial bias.
- **[depth_noise/depth_noise_reviewer_audit.md](depth_noise/depth_noise_reviewer_audit.md)** — independent adversarial review of the L515 design (pre-implementation).

### 3D pose export
- **[pose_export/pose_export_design.md](pose_export/pose_export_design.md)** — 6-DOF per-instance pose in camera frame. Schema + Blender→OpenCV frame conventions.

### Sample data layout
- **[sample_data/sample_data_naming_convention.md](sample_data/sample_data_naming_convention.md)** — ASCII per-bottle folder layout (`sample_data/bottles/<id>/`), canonical filenames (`mesh.obj`, `mesh_uv.obj`, `label.png`), `index.yaml`.

## Team process

- **[team/team_workflow.md](team/team_workflow.md)** — working agreement between `synth-lead` and `synth-dev` Claude sessions. Pre-render gate, locked baselines, out-of-plan proposal flow. **Read before kicking off any render.**

## Conventions

- Every doc has a `Date:` and `Status:` block near the top. Status is one of: `Proposal`, `Approved for implementation`, `Shipped`, `Superseded`, or a terminal-state marker.
- Cross-references between docs use relative paths.
- This index is the source of truth for "what docs exist." If you add a new doc, add an entry here too.
