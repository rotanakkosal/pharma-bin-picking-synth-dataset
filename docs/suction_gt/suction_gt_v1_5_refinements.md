# V1.5 Implementation Plan — Suction-Point GT Refinements

Date: 2026-05-06
Companion docs:
- [suction_gt_design.md](suction_gt_design.md) — original design (V1)
- [suction_gt_v1_implementation.md](suction_gt_v1_implementation.md) — V1 scope (already shipped)

Status: Ready to implement. Three targeted fixes informed by literature + a debugging finding from V1's actual output.

---

## Trigger — what V1 gets wrong

User inspection of `output/scene_000004/suction_overlay.png` flagged a high-quality (green) suction candidate placed near the cap–body junction of an upright bottle. Investigation revealed three distinct issues that V1's analytical scoring does not catch:

1. **Sparse-neighborhood plane fit.** V1's plane fit uses the 200 FPS-subsampled candidate points as neighbors within `cup_radius_m`. Average neighbor count is 5–15 — far too sparse to detect a 5mm cap-body height step. The plane fit averages across the step and reports low residual → spurious high `Sseal`.

2. **Binary edge clearance.** Filter F1 requires the cup disc to fit inside the silhouette mask but doesn't enforce a safety margin. A cup whose center sits 1mm from the silhouette boundary still passes — yet a 1mm offset in real grasping is enough for the seal to break.

3. **Top-K clustering.** V1 sorts candidates by `S_combined` and takes the top-50 raw. The top picks often cluster within a 1–2cm region of the highest-scoring patch. Visualization shows 4–5 overlapping circles per bottle around a single cap; benchmark consumers who want diverse top-K predictions get redundant data.

---

## TL;DR — three changes, all literature-backed

| # | Fix | Citation |
|---|---|---|
| 1 | **Dense plane fit** — sample all depth pixels within the cup-disc footprint, not the FPS candidate cloud | Sim-Suction `pointcloud_seal_eval.py` (dense ray casting); SuctionNet spring model (dense contact-ring evaluation); [Three-Filters-to-Normal+](https://ui.adsabs.harvard.edu/abs/2025ITASE..22..895Y/abstract) for discontinuity detection generally requires dense sampling |
| 2 | **Margin-aware edge clearance** — cup center must be at least `r_cup + r_safety` from any mask boundary | [Frontiers bin-picking suction](https://www.frontiersin.org/articles/10.3389/fnbot.2022.806898/full) treats "distance from cup center to surface center" as a first-class geometric feature; industry practice uses 3–10mm safety margin |
| 3 | **NMS on top-K export** — after scoring, suppress points within `nms_dist_mm` of an already-kept higher-scoring point | [SuctionNet §V](https://arxiv.org/abs/2103.12311): *"NMS is run before evaluation to prevent hacking on testset by predicting many similar suction poses and encourage diverse suction poses"*; [GraspNet evaluation](https://graspnet.net/evaluation.html) uses translation-distance NMS as a tuple |

---

## Change 1 — Dense plane fit

**V1 (current, broken):**
```python
# Plane fit on neighbors among the 200 FPS samples — sparse, misses discontinuities
d3 = np.linalg.norm(pts_cands - p, axis=1)
neighbor_mask = d3 <= cup_radius_m
normal, residual = fit_plane(pts_cands[neighbor_mask])  # 5-15 points typical
```

**V1.5 (proposed):**
```python
# Plane fit on every depth pixel inside the cup-disc footprint — dense
r_px = cup_pixel_radius(p[2], K, cup_radius_mm)
disc_v, disc_u = disc_pixels(uv, r_px, H, W)
disc_z = depth_m[disc_v, disc_u]
valid = disc_z > 0.01                         # drop pixels with no depth
disc_v, disc_u, disc_z = disc_v[valid], disc_u[valid], disc_z[valid]
disc_3d = backproject(np.stack([disc_u, disc_v], axis=-1), depth_m, K)
normal, residual = fit_plane(disc_3d)         # 50-300 points typical
```

**Effect on the discontinuity problem:**
- Cap-body junction now produces ~50% of disc points at body depth, ~50% at cap depth.
- Plane fit through both surfaces has residual ≈ step_height / 2 (e.g., 2.5mm for a 5mm step).
- `Sseal = exp(-2.5 / 1.0) = 0.082` → bright red, correctly flagged as bad.

**Cost:** ~50–300 pixel back-projections per candidate × ~200 candidates × ~22 instances = ~660K pixel ops per scene. Vectorized numpy, ~0.5s extra per scene. Acceptable.

**Cited justification:** Sim-Suction's [`pointcloud_seal_eval.py`](https://github.com/junchengli1/Sim-Suction-API/blob/main/isaac_sim_gen/pointcloud_seal_eval.py) casts dense rays from the cup contact ring to the surface — same idea, different implementation. SuctionNet's deformable spring model evaluates "average residual errors from the points around the contact ring" — also dense. Our V1 deviated from both by reusing the FPS cloud; this is a regression we should fix.

---

## Change 2 — Margin-aware edge clearance (Filter F1.5)

**V1 (current):**
```python
# F1: full cup disc inside this instance's visible mask
def filter_edge_clearance(uv, r_px, this_visible_mask):
    vs, us = disc_pixels(uv, r_px, ...)
    return np.all(this_visible_mask[vs, us] > 0)
```

**V1.5 (proposed):**
```python
# F1: cup disc inside mask AND center at least (r + r_safety) from boundary
def filter_edge_clearance(uv, r_px, this_visible_mask, r_safety_px):
    eroded = cv2.erode(this_visible_mask, kernel_circle(r_px + r_safety_px))
    return eroded[int(uv[1]), int(uv[0])] > 0
```

**Default:** `r_safety_mm = 5 mm` → at typical depth `z = 1.25 m`, `r_safety_px ≈ 5.4 px`.

**Effect:** all candidates pushed at least 20mm (15mm cup + 5mm safety) from any silhouette edge. The "green circle near the cap rim" disappears because the cap rim is a silhouette-mask boundary in image space.

**Cited justification:** Industry practice for vacuum suction in pick-and-place uses 3–10mm safety margin to absorb perception error and cup compliance. Frontiers bin-picking review treats "distance from cup center to surface center" as a key feature for graspability — margin-aware clearance is a discrete proxy. Our V1 was lenient; V1.5 brings us in line with practical robotics.

---

## Change 3 — NMS on top-K export

**V1 (current):**
```python
# Take top-K=50 raw, sorted by S_combined
kept.sort(key=lambda d: d["S_combined_default"], reverse=True)
out[inst_id] = kept[:50]
```

**V1.5 (proposed):**
```python
# NMS-then-take-top-K: greedily pick best, suppress within nms_dist_mm
def nms_top_k(candidates, nms_dist_mm, k):
    candidates.sort(key=lambda d: d["S_combined_default"], reverse=True)
    kept = []
    for c in candidates:
        if len(kept) >= k:
            break
        c_pos = np.array(c["point_3d_cam"])
        too_close = any(
            np.linalg.norm(np.array(k["point_3d_cam"]) - c_pos) * 1000 < nms_dist_mm
            for k in kept
        )
        if not too_close:
            kept.append(c)
    return kept
```

**Default:** `nms_dist_mm = 5 mm` (matches `match_tolerance_mm` — predictions within 5mm of a GT point already match the same GT, so multiple GT points within 5mm carry no additional information).

**Effect:** top-K=50 now spans the bottle's surface diversely. The visualization shows distinct, non-overlapping circles. Benchmark consumers can rank predictions against truly-distinct GT options.

**Cited justification:**
- SuctionNet §V: NMS run before evaluation, max 10 suctions per object.
- GraspNet: NMS threshold defined as a translation × rotation distance tuple.
- Both apply NMS to **predictions** during evaluation. We apply it to **GT** at generation time, so the top-K are pre-filtered diverse. This is consistent with both works' intent and lets the GT itself be the gold standard for "diverse high-quality grasps."

**Why we don't drop top-K to 10:** SuctionNet's 10-per-object is an evaluation-time cap on PREDICTIONS, not GT. Our 50-per-object GT lets evaluators run their own k=1..50 sweeps for AP_μ. NMS just ensures those 50 are spatially diverse.

---

## Updated `suction_meta` block

```jsonc
"suction_meta": {
  "version":              "v1.5",                   // bumped from "v1"
  "cup_radius_mm":        15.0,
  "r_safety_mm":          5.0,                      // NEW
  "plane_fit_dense":      true,                     // NEW
  "nms_dist_mm":          5.0,                      // NEW
  ...rest unchanged...
}
```

The added fields make the GT self-describing for evaluators reproducing our scoring.

---

## Acceptance criteria (V1.5 is "done" when)

- [ ] `output/scene_*/suction_overlay.png` shows **no green/orange circles within 5 mm of cap-rim or silhouette boundaries**
- [ ] Top-K viz shows **spatially diverse points** (no dense circle clusters)
- [ ] Discontinuity test: a candidate placed exactly at a cap-body junction gets `Sseal < 0.1` (was ~0.5+ in V1)
- [ ] All existing 14 integrity checks still pass
- [ ] Per-scene compute overhead stays under 5 seconds (was 1.3s in V1; budget +0.5s for dense plane fit, +0.1s for NMS = ~2s total)
- [ ] `suction_meta.version == "v1.5"` and the three new fields are present
- [ ] Plan-version doc reads: ranks of algorithms shouldn't reorder vs V1 (only fewer false-positive GT points; valid-positive GT points should largely persist with marginally lower scores)

## Implementation order

```
Step 1 (~30 min): Dense plane fit
  - Replace pts_cands neighbor lookup with full-depth-pixel sampling within disc
  - Re-run scene 999, eyeball overlay → expect cap-body junctions to go red

Step 2 (~20 min): Margin-aware F1
  - Pre-compute eroded mask once per instance (cv2.erode)
  - Update filter_edge_clearance to test eroded[v, u]
  - Re-run scene 999, eyeball overlay → expect no points within 5mm of mask boundary

Step 3 (~20 min): NMS top-K
  - Add nms_top_k() helper
  - Replace kept[:top_k] with nms_top_k(kept, 5.0, 50)
  - Re-run scene 999, eyeball overlay → expect spread, non-overlapping circles

Step 4 (~15 min): Update suction_meta + version bump
Step 5 (~30 min): Re-run 5-scene validation batch + full QC
Step 6 (~15 min): Update memory + checklist
```

Total: ~2 hours work, ~10 minutes render time, ~1 hour validation.

## Risks

| Risk | Mitigation |
|---|---|
| Dense plane fit slow on tilted bottles where the disc is large | Cap disc-pixel count at 1000; sample randomly above that |
| Eroded mask becomes empty for small/narrow bottles, no candidates pass | Fall back to raw F1 if eroded mask is empty (with a `r_safety_relaxed` flag in the point) |
| NMS reduces total point count significantly (especially on small bottles) | Ship even reduced counts; document that GT density varies with bottle size |
| Score ranks shift wildly vs V1 | Compare top-1 ranks before/after on the same 5-scene batch; if >20% rank changes, investigate |

## Why not implement V2 instead?

V2 = full SuctionNet spring-mesh model + QP wrench solver. Estimated 1 week. V1.5 is **3 changes addressing the actual observed problems** (the user's screenshot), takes 2 hours, and stays within V1's analytical-only architecture. V2 remains the right call if V1.5 ranks algorithms wrongly against real-world data; V1.5 is the right call to address the visible quality gap **now**.
