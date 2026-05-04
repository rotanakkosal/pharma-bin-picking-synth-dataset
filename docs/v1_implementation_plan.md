# V1 Implementation Plan — Suction-Point GT Generation

Date: 2026-05-04
Companion doc: [suction_point_gt_plan.md](suction_point_gt_plan.md) (the design)
Status: Ready to implement

---

## Goals (what V1 must achieve)

1. **Make the dataset usable as a grasp benchmark.** Right now it can score segmentation but not grasping. After V1, every rendered scene's `scene_gt.json` includes top-50 suction grasp points per bottle, with quality scores.

2. **Match published-benchmark conventions.** Output format and scoring follow the SuctionNet / Sim-Suction pattern (two continuous scores `Sseal` + `Swrench`, μ-independent physical quantities, top-50 per instance) so external algorithms can be evaluated without custom adapters.

3. **Be self-contained.** No new dependencies beyond what BlenderProc + numpy already provides. No Isaac Sim, no QP solver, no spring-mesh simulation. Closed-form math only.

4. **Be fast enough not to hurt batch rendering.** Adds < 10 seconds per scene on top of the existing ~70s render. Total per-scene time stays under 90s.

5. **Be defensible.** Every default value (cup radius, deformation threshold, top-K, etc.) traces to a published reference. Cited in code comments and in the doc.

## Non-goals (what V1 will NOT do)

- ❌ Full SuctionNet spring-mesh seal model (deferred to V2)
- ❌ QP-based wrench resistance (deferred to V2)
- ❌ Real physics simulation of suction events (deferred to V3)
- ❌ Multi-cup grippers
- ❌ Robot-arm reachability filtering
- ❌ Photoreal labels for new bottles (separate work)
- ❌ Predictive validity test against real captures (Layer 3 of evaluation framework — blocked on real data)

## What gets built (concrete deliverables)

### 1. New module: `scripts/suction_gt.py`

A single Python file that exposes one entry point:

```python
def compute_suction_gt(
    placed_bottles: list,           # blenderproc instances with poses
    visible_masks: dict,            # {instance_id: HxW bool array}
    depth_map: np.ndarray,          # HxW float32, in meters
    camera_K: np.ndarray,           # 3x3 intrinsics
    cfg: dict,                      # config from config.yaml
) -> dict:
    """
    Returns: {instance_id: {"suction_points": [...], "suction_meta": {...}}}
    """
```

### 2. Internal helper functions (in same file)

| Function | Purpose |
|---|---|
| `sample_candidates(visible_mask, depth_map, K, n=200)` | Dense per-pixel back-projection + FPS subsample to 200 |
| `passes_edge_clearance(point, r, visible_mask, K)` | Filter F1 |
| `passes_normal_alignment(point, neighbors, camera_ray)` | Filter F2 (also returns plane fit + normal) |
| `passes_visibility(point, depth_map, K)` | Filter F3 |
| `passes_collision_free(point, r, all_masks, instance_id, K)` | Filter F4 |
| `compute_sseal(plane_residual_mm, sigma=1.0)` | Score 1 |
| `compute_swrench(point, normal, com, mass, mu, r)` | Score 2 |

### 3. Hook in `scripts/generate_scene.py`

Insert call to `compute_suction_gt()` between line 428 (per-instance amodal pass) and line 432 (save_outputs). Pass results to `save_outputs()` so they get embedded in `scene_gt.json`.

### 4. Update `scripts/dataset_qc.py`

Add new QC checks:
- Every instance has a `suction_points` list (possibly empty)
- For each point: `Sseal` and `Swrench` are in [0, 1]
- Top-K is sorted descending by `S_combined_default`
- Histogram of `S_combined` across all instances (sanity check on score distribution)

## How V1 will be tested

### Unit-test-level sanity (informal, in a scratch script)

| Test | Expected |
|---|---|
| Flat horizontal plane (synthetic) | All candidates pass; `Sseal ≈ 1.0`, `Swrench` high |
| Sphere with r=15mm cup | `Sseal` decreases as candidate moves from pole to equator |
| Bottle at 60° tilt | `Swrench` < 0.3 (high lateral force) |
| Bottle at edge of mask | Edge clearance filter rejects candidates near boundary |
| Two bottles touching | Collision-free filter rejects candidates whose cup overlaps the neighbor |

### Integration test

1. Run `blenderproc run scripts/generate_scene.py --config scripts/config.yaml --scene-id 999`
2. Confirm `scene_gt.json` contains `suction_points` per instance
3. Run `python scripts/dataset_qc.py --output-dir output/`
4. Confirm new QC checks pass (no errors, score distribution looks reasonable)
5. Visualize top-3 suction points on rgb image (write a tiny `viz_suction.py`)
6. Eyeball check: top-scored points should land on bottle caps / centers, not on edges or curved sides

## Acceptance criteria (V1 is "done" when)

- [ ] `compute_suction_gt()` runs without errors on all 7 bottle classes
- [ ] Output schema matches the spec in [suction_point_gt_plan.md §"Updated export schema"](suction_point_gt_plan.md)
- [ ] Per-scene runtime overhead < 10 seconds (measured via wall-clock)
- [ ] `dataset_qc.py` reports 0 violations on a 5-scene batch
- [ ] Visualization shows top-K points landing on graspable surfaces (visual sanity check)
- [ ] All numeric defaults documented in code comments with citation

## Implementation order (within V1)

```
Day 1 (~3 hrs):
  1. Stub compute_suction_gt() with empty output
  2. Implement sample_candidates() with FPS
  3. Wire into generate_scene.py — confirm empty-list output saves correctly
  4. Run scene 999 — verify scene_gt.json structure

Day 1 (~3 hrs):
  5. Implement filter functions F1-F4
  6. Implement compute_sseal() with plane fit
  7. Implement compute_swrench() closed-form
  8. Run scene 999 — manually inspect a few suction_points entries

Day 2 (~2 hrs):
  9. Add dataset_qc checks
  10. Write viz_suction.py for eyeball validation
  11. Run scene 999 + visualize → confirm points land sensibly
  12. Time the per-scene overhead; optimize if > 10s

Day 2 (~1 hr):
  13. Run a 5-scene batch
  14. Run dataset_qc on the batch
  15. Update memory + plan doc with results
  16. Mark V1 complete
```

Total: ~9 hours of focused work.

## Risks and what to do if they happen

| Risk | Mitigation |
|---|---|
| Filter F4 (collision-free) is too strict — most candidates rejected in dense piles | Loosen by allowing partial overlap (e.g. 90% of cup disc clear instead of 100%); document the change |
| `Sseal` saturates near 1.0 for all candidates (bottles are mostly flat) | Tighten σ_seal from 1.0 mm to 0.5 mm; rerun and check distribution |
| Per-scene runtime > 10s | Profile, then reduce candidate count from 200 to 100 (still > 50 needed for top-K) |
| Bottle COM not directly accessible from BlenderProc | Approximate using bbox centroid + half-height (good enough for cylindrical bottles) |
| Top-K=50 produces few unique points (clustering near best spot) | Add a min-distance constraint between exported points (e.g., 5 mm spacing) |

## What V2 would add (for context)

If V1 produces a benchmark that ranks algorithms inconsistently with real-world grasp success rates, V2 would replace:
- `compute_sseal()` with a deformable spring-mesh contact model (perimeter/flexion/cone springs)
- `compute_swrench()` with QP-based wrench resistance under friction-cone constraints

V2 is ~1 week of additional work. We defer it until evidence shows V1 is insufficient.

---

## Confirmation

If this plan is approved, V1 implementation starts with `scripts/suction_gt.py` stub.
