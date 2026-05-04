# Suction-Point Ground Truth — Design Plan (v2, literature-backed)

Date: 2026-05-04
Status: Approved for implementation, pending user confirmation of parameters

## Purpose

Define the criteria, scoring, sampling, and export format for **suction-point ground truth** in the pharma-bin-picking synthetic benchmark. The benchmark's purpose is to evaluate algorithms that predict suction grasp points on cluttered Korean pharmaceutical bottles. Without suction-point GT the dataset can only score segmentation, not grasping — defeating its core purpose.

This plan is anchored to four reference works:

1. **Dex-Net 3.0** (Mahler et al., 2018) — original analytical suction model with **seal quality + wrench resistance** ([arXiv:1709.06670](https://arxiv.org/abs/1709.06670))
2. **SuctionNet-1Billion** (Cao et al., 2021) — large-scale benchmark; defines the two-score continuous label format and AP@k evaluation ([arXiv:2103.12311](https://arxiv.org/abs/2103.12311))
3. **Sim-Suction** (Li & Cappelleri, 2023) — synthetic-data analog of SuctionNet; closest to our setup (cluttered, simulated, 500 scenes / 3.2M poses) ([arXiv:2305.16378](https://arxiv.org/abs/2305.16378), [GitHub](https://github.com/junchengli1/Sim-Suction-API))
4. **Diffusion-Suction / Parcel-Suction** (Huang et al., 2025) — most recent, 25K scenes / 410M poses ([arXiv:2502.07238](https://arxiv.org/abs/2502.07238))

---

## TL;DR — How GT is built (read this first)

**The plan in one sentence:** For every bottle in every rendered scene, pick candidate grasp points on its top surface, throw out the bad ones using 4 physics-based filters, score what's left for grasp quality, save the best 50 per bottle to a file.

### The 5 steps, in plain English

For each bottle:

| Step | What it does | What you get |
|---|---|---|
| **1. Sample** | Pick about 200 candidate points spread across the bottle's visible top surface | A list of 200 candidate grasp points |
| **2. Filter** | Throw out any candidate that fails one of 4 checks (see below) | A shorter list of "physically possible" candidates |
| **3. Score** | Give each survivor two scores from 0 to 1: `Sseal` (will the cup seal?) and `Swrench` (will the seal hold the bottle's weight?) | Each candidate has two grades |
| **4. Rank** | Multiply the two scores and sort. Keep the top 50. | The best 50 grasp points |
| **5. Save** | Write them to `scene_gt.json` along with the physics numbers used to compute the scores | The GT file |

### The 4 filters (Step 2)

A candidate gets thrown out if **any** of these is true:

- The 15 mm cup disc would hang off the edge of the bottle
- The surface tilts more than 30° from straight-up (cup can't seal at extreme angles)
- The point is hidden behind another bottle (camera can't see it)
- Another bottle is in the way of the cup's approach path

### What "GT" means here

A point becomes ground truth **only if**:
1. It survived all 4 filters above, **AND**
2. It ranked in the top 50 for that bottle by quality score

Everything else is thrown away. The GT file only contains the **best, physically graspable** points.

### Defaults

- Cup radius: 15 mm (standard industrial vacuum cup)
- Bottle mass: 0.1 kg (already used in the physics simulation)
- Friction: 0.5 (mid-range; evaluators can sweep from 0.2 to 1.2 later)
- Top-K kept per bottle: 50

### Where this lives in the pipeline

A new step inserted between [generate_scene.py:428](../scripts/generate_scene.py) (after segmentation masks are made) and [generate_scene.py:432](../scripts/generate_scene.py) (before saving outputs).

---

The rest of this document is reference material — math, citations, export schema, evaluation. Skip or skim as needed.

---

## What changed from v1 (after literature review)

| v1 (initial) | v2 (literature-aligned) | Reason |
|---|---|---|
| Single weighted `score` | **Two scores: `Sseal` and `Swrench`** | SuctionNet, Dex-Net 3.0, Sim-Suction all use this two-score continuous format. Standard. |
| Flatness threshold = 1 mm RMS | Flatness via plane-fit residual + cup deformation ratio (15% default per Sim-Suction) | Sim-Suction defaults to 15% deformation; SuctionNet uses spring-model residual at contact ring. |
| `centrality` as 3rd weighted term | Centrality merged into `Swrench` (lever-arm / gravity wrench) | Centrality is a proxy for wrench resistance — should be computed analytically, not weighted heuristically. |
| Top-K = 10 per instance | **Top-K = 50** per instance | SuctionNet evaluation uses Precision@k for k=1..50 → need ≥50 to compute AP_μ properly. |
| 5 mm sampling grid | Dense per-visible-pixel + FPS subsampling | Diffusion-Suction uses farthest-point sampling (FPS); avoids missing local optima from grid alignment. |
| Single-cup, visible mask only | Same — confirmed standard | All four reference works do single-cup analytical scoring on visible surfaces. |

---

## Physical model (literature-aligned)

A suction cup grasp succeeds iff:
1. The cup forms a vacuum **seal** at contact (Dex-Net 3.0 §III.B; SuctionNet §III.A).
2. The seal can resist the gravity **wrench** acting on the lifted object (Dex-Net 3.0 §III.C; SuctionNet §III.B).

These are independent failure modes: a perfect seal on a heavy off-center grasp still fails (wrench failure); a centered grasp on a curved surface fails (seal failure). Hence the **two-score format**.

### Score 1 — Seal score `Sseal ∈ [0, 1]`

**SuctionNet method (full)**: deformable spring model with three spring types (perimeter / flexion / cone). Project cup onto target surface; compute the energy required for the perimeter springs to maintain contact. Feasible iff energy is below a threshold. Score = average residual error from contact-ring points to a fitted plane, mapped to [0,1].

**Sim-Suction method (simplified)**: cup deformation ratio. Sample points in a disc of radius `r` around the candidate; fit plane; deformation = max distance from any contact point to plane, divided by cup compliance budget (default 15%). Score = `1 - deformation_ratio` clamped to [0,1].

**Our V1 implementation (simplest, defensible)**: plane-fit residual.
```
residuals = [|d_i - plane(p_i)|  for p_i in points within radius r of candidate]
residual_rms = sqrt(mean(residuals²))
Sseal = exp(-residual_rms / σ_seal)   # σ_seal = 1.0 mm
```
Cited justification: SuctionNet §III.A defines the seal score from "average residual errors from the points around the contact ring and a fitted plane." Our formula is the SuctionNet residual reduced to a single Gaussian falloff for V1 simplicity. V2 can upgrade to the full spring model.

### Score 2 — Wrench score `Swrench ∈ [0, 1]`

**Dex-Net 3.0 / SuctionNet method**: solve a QP for max wrench resistance subject to:
- Friction limit surface (tangential force ≤ μ × normal force)
- Cup elastic limits (material can support some shear before tearing)
- Vacuum force ceiling (atmospheric pressure × cup area)

**Our V1 implementation**: closed-form approximation following Dex-Net 3.0 §III.C, simplified to the dominant gravity-only wrench:
```
F_grav      = m × g                                              # gravity force on object
F_lateral   = F_grav × sin(angle_from_vertical)                   # tangential component cup must resist
F_normal    = F_grav × cos(angle_from_vertical)
torque_arm  = ||COM_xy - contact_xy||                             # lever arm from contact to COM projection
max_F_lat   = μ × F_vacuum                                        # max friction the seal can support

# Two failure modes:
seal_fail   = F_lateral > max_F_lat                               # tangential force tears seal
torque_fail = m × g × torque_arm > τ_max                          # torque exceeds cup compliance

Swrench = exp(-F_lateral / max_F_lat) × exp(-torque_arm / r)
```
Cited justification: Dex-Net 3.0 §III.C: "wrench resistance ... constrained due to (a) the friction limit surface, (b) limits on the elastic behavior of the suction cup material, and (c) limits on the vacuum force." Our formula keeps (a) and (b) with reasonable defaults; (c) is handled implicitly via `μ × F_vacuum`.

### Hard filters (apply BEFORE scoring)

These are necessary conditions; failing any of them means `Sseal = Swrench = 0`:

| # | Filter | Computation | Citation |
|---|---|---|---|
| **F1** | **Edge clearance** — full cup disc inside visible mask | Project cup of radius `r` onto image; check 100% of disc pixels lie inside visible mask of this instance | Standard across Dex-Net, SuctionNet, Sim-Suction |
| **F2** | **Normal alignment** — approach direction within cup compliance | Angle between fitted plane normal and camera ray < 30° | Sim-Suction (`pointcloud_seal_eval.py`) uses similar gating |
| **F3** | **Visibility** — point is the closest surface to camera | Z-buffer test using full-scene depth map | Implicit in single-image grasp evaluation |
| **F4** | **Approach collision-free** — no other object intersects the cup approach cone | Project cup approach to camera; check no other instance's mask overlaps the cup disc | Sim-Suction calls this collision check (mode `"collision"`) |

Points failing any filter are **excluded** from the GT export (not just zero-scored — excluded entirely, to keep file size manageable).

---

## Default parameters (literature-backed)

| Parameter | Default | Source |
|---|---|---|
| Cup radius `r` | **15 mm** | Sim-Suction default (1.5 cm); matches our hardware target |
| Seal flatness scale `σ_seal` | **1.0 mm** | Tuned so 1mm RMS gives `Sseal ≈ 0.37`; our pharma bottles' caps have surface roughness < 0.5mm → most caps score > 0.6 |
| Cup deformation budget | **15%** | Sim-Suction `pointcloud_seal_eval.py` default |
| Friction coefficient `μ` (default for Swrench) | **0.5** | Mid-range; SuctionNet evaluates μ ∈ [0.2, 1.2] in steps of 0.2 |
| Vacuum force `F_vacuum` | atmospheric × π·r² ≈ **71 N** for r=15mm | Standard atmospheric (101 kPa) × cup area |
| Object mass `m` | **0.1 kg per bottle** | Already set in [generate_scene.py:292](../scripts/generate_scene.py#L292) |
| Normal angle threshold | **30°** | Top-down approach + cup compliance (standard) |
| Sampling | **dense per-pixel within visible mask, then FPS to N=200** | Diffusion-Suction uses 16,384 FPS-sampled per scene; we scale to ~200/instance |
| Top-K kept | **50** | SuctionNet AP_μ uses Precision@k for k=1..50 |

---

## Export format

> **The export schema is defined below in §"Updated export schema"** — it depends on the evaluation pipeline (next section), so we describe the evaluation first.

`S_combined = Sseal × Swrench` is the SuctionNet convention — multiplication (not sum) penalizes points that fail either failure mode. We use this for sorting/ranking; evaluators can recompute at any friction μ.

---

## Evaluation pipeline (how GT is consumed)

This section traces how an algorithm's predictions are scored against our GT, end to end. **Implications for what we must export are flagged inline.**

### Step 1 — Algorithm input/output contract

Algorithm receives per scene:
- `rgb/0000.png`, `depth/0000.png`, `camera_K`, optionally `visible_masks/`

Algorithm outputs per scene:
- A list of predicted suction grasps: `[(point_3d_cam, normal_cam, predicted_score), ...]`, sorted by `predicted_score` descending.
- No fixed K — the algorithm decides how many to predict. Evaluator truncates to top-50.

### Step 2 — Match each prediction to a GT point

For each predicted point `p_pred`, find the closest GT point on the **same instance** (using its visible mask):

```
matched_gt = argmin_{g ∈ GT} ||g.point_3d_cam - p_pred|| 
             subject to visible_mask[p_pred.pixel] == g.instance_id
             AND ||g.point_3d_cam - p_pred|| < d_tol  (default 5 mm)
```

If no GT point is within `d_tol` on the same instance → prediction is **unmatched** → counted as incorrect.

> **Implication for export:** GT must be **dense enough** that any reasonable prediction lands within 5 mm of a GT point. Justifies our choice of N=200 candidates per instance with FPS sampling (mean spacing ~3-4 mm on a 75×150 mm bottle surface).

### Step 3 — Decide if the matched GT point is "correct" at friction μ

A prediction is **correct at friction μ** iff:
```
Sseal(matched_gt) ≥ τ_seal           AND   Swrench(matched_gt, μ) ≥ τ_wrench
```
where `τ_seal = τ_wrench = 0.5` (SuctionNet default).

> **Critical implication for export:** `Swrench` depends on μ. If we export only `Swrench(μ=0.5)` the evaluator can't compute AP_μ for other μ values. **We must export the underlying physical quantities** so the evaluator recomputes `Swrench(μ)` at any μ.

This forces the export schema (next section update) to include:
- `lateral_force_N` (μ-independent — it's `m·g·sin(θ)`)
- `vacuum_force_N` (μ-independent — `P_atm · π·r²`)
- `normal_force_N` (μ-independent — `m·g·cos(θ)`)
- `torque_arm_mm` (μ-independent — distance from contact to COM projection)
- `Sseal` (μ-independent)
- `Swrench(μ=0.5)` (precomputed for convenience; evaluator can recompute at other μ)

The evaluator then computes at any μ:
```
F_lat_max(μ)  = μ · vacuum_force_N
Swrench(μ)    = exp(-lateral_force_N / F_lat_max(μ)) · exp(-torque_arm_mm / r)
```

### Step 4 — Per-scene Precision@k

For each scene, take the top-k predictions (by `predicted_score`). Match and classify each (Step 2-3).
```
Precision@k(scene, μ) = (# correct in top-k) / k
```

### Step 5 — Average Precision at friction μ

Average over k = 1..50 (SuctionNet's standard sweep):
```
AP_μ(scene) = mean_{k=1..50} Precision@k(scene, μ)
```

### Step 6 — Final benchmark score

Average over all scenes and friction sweep:
```
AP = mean_{scene} mean_{μ ∈ {0.2, 0.4, 0.6, 0.8, 1.0, 1.2}} AP_μ(scene)
```

This is the **single number that ranks algorithms** on our benchmark.

### Step 7 — Per-class breakdowns (disclosure metadata)

In addition to overall AP, report:
- `AP_per_class` for each of the 7 bottle classes (some bottles may be intrinsically harder)
- `AP_per_occlusion_bin` (0-10%, 10-30%, 30-50%, 50-80%) — shows how performance degrades with occlusion
- `AP_per_friction` (the AP_μ sweep) — shows how performance depends on assumed friction

These are reported alongside, not averaged into, the headline AP.

### Where the hard filters and sampling strategy live

The four hard filters (F1 edge clearance, F2 normal alignment, F3 visibility, F4 collision-free approach) and the sampling strategy (dense + FPS to N=200) are **GT-generation-time concerns, not evaluation-time concerns**. The evaluator never re-applies them to predictions. This matches the SuctionNet convention.

**How this works in practice:**

1. **At GT generation (our pipeline)**: filters + sampling decide which candidate points become GT.
   - Only points passing all four hard filters get exported.
   - Sampling determines GT density across the visible surface.

2. **At evaluation (running an algorithm against the benchmark)**: matching is the only check.
   - Algorithm submits predictions (no filter constraints).
   - Each prediction matches to nearest GT within 5 mm on the same instance.
   - Predictions in zones where GT was filtered out have **no nearby GT to match** → unmatched → counted as incorrect.

**Implication: filters propagate to evaluation through "absence of GT," not through re-application.**

Concrete example: if an algorithm predicts a point right at the bottle's edge (failing F1 edge clearance), there will be no GT point within 5 mm there because we excluded that zone during generation. The prediction is unmatched → marked incorrect at every k. The algorithm is implicitly punished for proposing physically infeasible grasps without us having to re-implement the geometry checks at eval time.

**Why we don't re-apply filters at eval time:**
- Adds complexity to the evaluator (would need camera intrinsics, all instance masks, depth — already the case here, but in general benchmarks try to keep evaluators simple).
- The "absence of GT" mechanism already enforces the filter constraint, just implicitly.
- SuctionNet, Sim-Suction, and Diffusion-Suction all follow this pattern. Diverging would make our benchmark non-standard.

**Implication for sampling density** (already noted in Step 2): GT density must be **at least as dense as the matching tolerance** so that any reasonable prediction inside a feasible zone finds a match. Our N=200 + FPS gives mean spacing ~3-4 mm on a 75×150 mm bottle — well below the 5 mm tolerance.

**One caveat — the `suction_meta` block exports filter parameters anyway.** This isn't because the evaluator uses them; it's so independent re-implementations of GT generation can match ours exactly. Reproducibility, not enforcement.

---

## Updated export schema (consequence of Step 3)

Replaces the schema in §"Export format" above. **The change**: instead of one precomputed `Swrench`, we export the underlying physical quantities so the evaluator can recompute at any μ.

```json
{
  "instance_id": 8,
  "class_name": "bottle_pill",
  ...existing fields...,
  "suction_points": [
    {
      "point_3d_cam":     [0.012, -0.045, 0.834],   // m, camera frame
      "point_2d_px":      [712, 423],
      "normal_cam":       [0.02, 0.05, -0.998],     // unit normal in camera frame

      // Score components (Sseal is μ-independent, Swrench depends on μ)
      "Sseal":            0.91,
      "Swrench_default":  0.78,                      // computed at μ_default = 0.5
      "S_combined_default": 0.71,                    // = Sseal × Swrench_default

      // Physical quantities for evaluator to recompute Swrench at any μ
      "lateral_force_N":  0.42,                      // m·g·sin(θ)
      "normal_force_N":   0.96,                      // m·g·cos(θ)
      "vacuum_force_N":   71.4,                      // P_atm · π·r²
      "torque_arm_mm":    4.1,                       // ||contact_xy - COM_xy||

      // Diagnostic fields
      "flatness_residual_mm": 0.42,
      "normal_angle_deg":     5.7
    }
    // ...top-K=50 per instance, sorted by S_combined_default descending
  ],
  "suction_meta": {
    "cup_radius_mm":    15,
    "mu_default":       0.5,
    "mu_sweep":         [0.2, 0.4, 0.6, 0.8, 1.0, 1.2],
    "tau_seal":         0.5,
    "tau_wrench":       0.5,
    "match_tolerance_mm": 5.0,
    "filters_applied":  ["edge_clearance", "normal_alignment", "visibility", "collision_free"],
    "object_mass_kg":   0.1,
    "atmospheric_pressure_Pa": 101325
  }
}
```

The `suction_meta` block makes the GT **self-describing** — anyone evaluating the benchmark can reproduce our scoring exactly without reading our code.

---

## Implementation phases

**Phase 1 (V1) — simplified analytical model** (this implementation)
- Hard filters F1-F4 (literature-standard)
- Sseal: Gaussian falloff on plane-fit RMS residual (SuctionNet residual, simplified)
- Swrench: closed-form gravity-resistance with friction limit (Dex-Net 3.0, simplified)
- Dense sampling + FPS subsampling
- Top-K=50 per instance
- **Status: ready to implement**

**Phase 2 (V2) — full SuctionNet model** (deferred unless V1 underperforms)
- Replace Sseal with deformable spring model (perimeter/flexion/cone springs)
- Replace Swrench with QP-based wrench resistance
- Effort: ~1 week. Defer unless we find that V1 ranks algorithms wrongly.

**Phase 3 (V3) — physical-simulation validation** (deferred, optional)
- Run Isaac Sim or PyBullet physics to simulate actual suction events at top-K points
- Compute empirical success rate; compare to analytical V1/V2 scores
- Sim-Suction does this; could borrow their pipeline ([Sim-Suction-API](https://github.com/junchengli1/Sim-Suction-API))

---

## Implementation hook in pipeline

Insert between [generate_scene.py:428](../scripts/generate_scene.py) (per-instance amodal pass) and [generate_scene.py:432](../scripts/generate_scene.py) (save_outputs). At that point:
- `placed` list contains all bottles with final settled poses
- `data["depth"]` has per-pixel depth in mm
- `cfg["camera"]` has intrinsics
- `amodal_masks[instance_id]` and visible masks are computed
- Object COMs are recoverable from `placed[i].get_origin()` + per-bottle mass

Algorithm (V1):
```
for each placed bottle:
    visible_mask = visible_masks[instance_id]
    candidate_pixels = sample dense grid + FPS to N=200 within visible_mask
    candidates_3d   = backproject candidate_pixels using depth + K
    com_2d = project bottle COM to image plane
    
    accepted = []
    for cand in candidates_3d:
        # F1: edge clearance
        if not full_cup_inside_mask(cand, r, visible_mask): continue
        # F2: normal alignment
        normal, residual = fit_plane_within_radius(cand, r, depth_map)
        if angle(normal, camera_ray) > 30°: continue
        # F3: visibility (depth-buffer test)
        if cand.z != depth_map[cand.pixel]: continue
        # F4: collision (any other instance mask overlaps cup disc?)
        if other_mask_overlaps_cup(cand, r, all_visible_masks): continue
        
        Sseal   = exp(-residual / σ_seal)
        Swrench = compute_wrench_score(cand, normal, com_3d, m=0.1, μ=0.5)
        accepted.append((cand, normal, Sseal, Swrench))
    
    accepted.sort(key=lambda x: x[2] * x[3], reverse=True)
    instance['suction_points'] = accepted[:50]
```

Estimated runtime: ~5-10 seconds per scene (200 candidates × 4 filters × cheap math), acceptable on top of existing ~70s render time.

---

## Open questions for user before implementation

1. **Cup radius**: 15 mm matches Sim-Suction default and our hardware target. Confirm or specify your hardware spec.
2. **Single-cup only?** Pharma picking is single-cup. Confirm we're not supporting multi-cup grippers (would need pair scoring, much more complex).
3. **Friction default**: 0.5 is mid-range. Used to compute exported `Swrench`. (User-time evaluation can sweep μ ∈ [0.2, 1.2].) OK?
4. **Skip V2/V3 for now?** V1 is sufficient for a first benchmark release; V2/V3 are upgrades only if reviewers demand more rigor.

If yes to all defaults, ready to implement.

---

## Citations / Sources

- Mahler, J. et al. **Dex-Net 3.0** (2018). [arXiv:1709.06670](https://arxiv.org/abs/1709.06670). Original analytical suction model: seal quality + wrench resistance via QP.
- Cao, H. et al. **SuctionNet-1Billion** (2021). [arXiv:2103.12311](https://arxiv.org/abs/2103.12311). Two-score continuous format (`Sseal` + `Swrench`); AP_μ evaluation; spring-based seal model.
- Li, J. & Cappelleri, D. **Sim-Suction** (2023). [arXiv:2305.16378](https://arxiv.org/abs/2305.16378), [GitHub](https://github.com/junchengli1/Sim-Suction-API). Synthetic-data analog: 500 scenes / 3.2M poses; 1.5 cm cup, 15% deformation defaults; closest to our use case.
- Huang, D. et al. **Diffusion-Suction / Parcel-Suction** (2025). [arXiv:2502.07238](https://arxiv.org/abs/2502.07238). 25K scenes / 410M poses; FPS sampling strategy; current SOTA.
- Fang, H. et al. **GraspNet-1Billion** (2020). [CVPR 2020](https://openaccess.thecvf.com/content_CVPR_2020/papers/Fang_GraspNet-1Billion_A_Large-Scale_Benchmark_for_General_Object_Grasping_CVPR_2020_paper.pdf). Substrate dataset for SuctionNet's real-world labels.
