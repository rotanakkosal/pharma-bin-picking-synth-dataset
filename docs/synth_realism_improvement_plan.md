# Synth Realism Improvement Plan (Literature-Validated)

**Date:** 2026-05-04
**Status:** Plan, pre-implementation
**Driver:** UOAIS-on-synth eval results + literature review

---

## Context

We ran UOAIS (existing instance-segmentation model in `pharma-bin/pharma-bin-picking`) on our synth dataset (6 scenes, 174 GT instances). The numbers were eyebrow-raising on the high side, prompting the question: is our synth too easy, too realistic, or just well-aligned with what UOAIS expects?

This document records the eval results, the diagnostic interpretation (corrected after literature review), and the resulting prioritized intervention plan with sources.

---

## Eval results

```
UOAIS on synth — IoU threshold = 0.5
================================================================
scenes evaluated         : 6
GT instances             : 174
UOAIS predictions        : 164
  matched (TP)           : 151
  unmatched preds (FP)   : 13     over-segmentation / spurious
  missed GT (FN)         : 23     under-segmentation / missed bottles

precision (TP / (TP+FP)) : 0.921
recall    (TP / (TP+FN)) : 0.868
F1                       : 0.893
mean IoU on matches      : 0.895

--- per-class breakdown ---
class_id   TP   FP   FN  recall  mean_IoU
1          35    0    2   0.946     0.894
2          41    0    0   1.000     0.893
3          34    0   16   0.680     0.889
4          32    0    4   0.889     0.897
5           2    0    0   1.000     0.943
6           5    0    0   1.000     0.923
7           2    0    1   0.667     0.924
```

Two unusual patterns:

1. **Mean IoU on matches = 0.895.** UOAIS typically scores 0.60–0.75 IoU on its own training distribution (OSD/OCID). On real Korean pharma photos in this project (per project memory), it over-segments at 1.4× ground truth — clear evidence of struggle. Our synth gives it 0.895 with no struggle.

2. **Class imbalance is severe.** Classes 5/6/7 only have 2/5/3 instances total across 6 scenes. With `copies_per_mesh: 7`, each class should produce ~42 instances. Three classes are underrepresented by 10–20×.

---

## Diagnostic interpretation (corrected after literature review)

### Initial (naive) reading
"UOAIS scoring 0.895 IoU is too high; the synth is too easy; we need to make it harder so algorithms separate cleanly across a wider range."

### What the literature actually says

That framing is partially wrong:

1. **Modern synth-trained models routinely score 94–99% on synth and still transfer well to real** ([DR Survey 2024](https://dl.acm.org/doi/10.1145/3637064)). High synth scores aren't inherently a sign of bad benchmark.

2. **The "too easy synthetic benchmark" critique applies mostly to LLM-generated benchmarks** ([Benchmark Saturation 2026](https://arxiv.org/html/2602.16763v1)) where models exploit stylistic patterns. Perception synth is a different category.

3. **The proper goal is correlation between synth ranking and real ranking, not absolute difficulty.** [SynTable](https://arxiv.org/html/2307.07333v3) — the most directly comparable work (cluttered tabletop UOAIS-Net training data) — reports UOAIS scoring 84.5 F-measure on synth vs 80.9 on real OSD-Amodal. Their synth is "easy" by my naive metric, but it predicts real performance accurately. That's the actual benchmark virtue.

4. **Domain randomization vs photorealism is an active debate** ([Sensors 2021](https://www.mdpi.com/1424-8220/21/23/7901)). DR + non-photorealistic textures sometimes transfers better than photorealism. Our pipeline is closer to DR; that's not automatically wrong.

### Corrected goal

Not "make synth harder," but **"add the realism elements that empirically improve sim-to-real transferability."** When real captures arrive, we want synth scores to predict real scores within a few points — like SynTable's 84.5/80.9 alignment.

The 0.895 IoU on our synth is not a problem in itself. The problem is we have no real reference to know whether 0.895 predicts 0.85 (good benchmark) or 0.55 (broken benchmark). We can't answer that without real captures.

---

## Validated intervention plan

Ordered by impact and confidence.

### Priority 0 — Clean up `sample_data/` naming and layout

**Status:** Prerequisite. Doing any of the priorities below without this is building on shifting sand.

**Why:**
- 7 active meshes are scattered across 4 parent dirs with inconsistent naming (Korean glyphs with spaces, ASCII descriptive names, vendor-cryptic strings, single-letter folders).
- Each new mesh requires reinventing the naming decision.
- Existing render code carries workarounds (`stage_textured_mesh()` ASCII-staging, broken-mtllib fallback) that exist purely to compensate for messy upstream layout.
- The class-imbalance bug we found was partly enabled by config drift across renders — easier when there's no canonical structure.

**Action:** See [`sample_data_naming_convention.md`](sample_data_naming_convention.md) for the detailed proposal: one folder per object with ASCII ID, canonical filenames (`mesh.obj`, `mesh_uv.obj`, `label.png`), per-object `README.md`, and a machine-readable `index.yaml`.

**Approach:** Migration Option A from that doc — create the new `sample_data/bottles/<id>/` tree using symlinks first, switch config, verify render still works, then physically delete the old layout. Reversible if anything breaks.

**Why first:** All subsequent priorities depend on stable mesh references. Class-imbalance fix (P1) requires rendering with a stable 7-mesh set; depth-noise (P2) and lighting (P3) require knowing what mesh you're applying noise to. Doing P1 first means redoing it after P0 cleanup.

### Priority 1 — Fix class imbalance bug

**Status:** Suspected representation bug, not a synth-quality issue.

**Evidence:** Eval shows classes 5/6/7 with 2/5/3 instances vs expected ~42. Most likely scenes 700–704 were rendered before the new ASCII-named meshes (`bottle_medicine2`, `bottle_medicine3`, `bottle_pill`) were added; only scene_999 has all 7 classes.

**Action:**
- Inspect each scene's `scene_gt.json` to confirm the hypothesis.
- Re-render full batch with consistent 7-mesh config so per-class statistics become interpretable.

**Why first:** Cheap, fixes a bug, and any future eval depends on getting this right. No literature needed — internal data integrity issue.

### Priority 2 — Depth sensor noise simulation

**Source:** [Lehrmann et al. 2024 — Enhancement of 3D Camera Synthetic Training Data with Noise Models](https://arxiv.org/html/2402.16514v1)

**Key findings from the paper:**
- Noise models with degree-2 polynomial fits for axial + lateral noise across Kinect v1/v2 + MotionCam-3D.
- Noise is dependent on distance (z) and surface angle (θ).
- Empirical sweet spot: noise multiplier `Mn=1.25`. Both `Mn=0` (no noise) and `Mn≥1.5` hurt downstream IoU on real data.
- Networks trained on pristine synth (`Mn=0`) generalize poorly to real.

**Why this is the highest-impact realism fix:**
- Pristine depth is THE most consistently identified synth-real gap signal in the literature.
- UOAIS was trained on real RealSense / Kinect depth — it learned to ignore noise patterns. Feeding it noise-free synth removes a signal it relies on, but in unfamiliar ways (overconfident segmentation).

**Implementation options:**
- [simkinect](https://github.com/ankurhanda/simkinect) — Hanson's simple Kinect noise add-on
- [simsense](https://github.com/angli66/simsense) — GPU-accelerated stereo depth simulator with noise
- [render_kinect](https://github.com/jbohg/render_kinect) — Bohg lab's Kinect simulation
- Or port Lehrmann's polynomial coefficients directly into a BlenderProc post-processing step

**Expected impact:** UOAIS IoU likely drops 5–10 points; predictive validity vs real should improve.

**Calibration warning:** Lehrmann's `Mn=1.25` is sensor- and dataset-specific. Once we have real L515 captures, do our own noise-level sweep to find the sweet spot for our specific setup.

### Priority 3 — HDRI environment lighting

**Source:** [SynTable §3 (Liu et al. 2023, v3 2024)](https://arxiv.org/html/2307.07333v3)

**SynTable's lighting recipe:**
- Spherical light sources (count `L`)
- Color temperature: 2,000–6,500 K
- Intensity: 100–20,000 lx
- Multiple camera viewpoints sampled from concentric hemispheres

**Why:**
- Our current `setup_lights()` uses 3 random point lights with fixed energy ranges. Real-world lighting variation is broader.
- HDRI maps capture realistic global illumination; point lights don't.

**Implementation:**
- Replace point-light rig with HDRI maps (free packs from polyhaven.com).
- Keep the color-temperature variation we already have; widen ranges to match SynTable.
- Consider random camera viewpoint perturbation as a secondary win.

**Expected impact:** Improves RGB realism, less direct effect on depth. Cumulative with Priority 2.

### Priority 4 — Material/texture variation

**Source:** [SynTable §3](https://arxiv.org/html/2307.07333v3) — 130 materials randomly applied per scene.

**Why deferred to P4:**
- Real bins have non-HDPE objects (cardboard, glass, metal). Our all-HDPE rendering is a known gap.
- BUT: mesh diversity (4 → 7 → ?) is the larger driver. Adding material variation to a 7-mesh set is cheaper but lower impact than getting more meshes from the capture team.

**Action when prioritized:** Apply random material from a curated pool to each instance (HDPE, cardboard-paper, frosted glass, metallic finish).

### Deprioritized / dropped from initial draft

- ~~Image-space noise (compression, blur, motion blur)~~ — no literature support for impact on this task type.
- ~~"Make benchmark harder for algorithm-spread"~~ — wrong framing per SynTable's results; goal is real-correlation, not absolute difficulty.

---

## Validation plan

1. **Apply P1 + P2 first** (class imbalance fix + depth noise). Re-render. Re-evaluate UOAIS via `scripts/eval_uoais_on_synth.py`.

2. **Compare:**
   - If UOAIS IoU drops to 0.70–0.80 range — aligns with SynTable's 0.81 on real OSD-Amodal. Plausible regime.
   - If UOAIS IoU stays at 0.85+ — depth noise didn't bite; investigate noise application or move to P3.
   - If UOAIS IoU drops below 0.65 — too much noise; reduce `Mn` toward 1.0.

3. **Document the noise calibration value** chosen. Lehrmann's `Mn=1.25` is empirical for their sensor mix; we should record what we picked and why.

4. **Layer 3 (predictive validity) still requires real captures.** This plan does not unlock benchmark validation by itself. It makes synth more *transferable* in a way that should make real-correlation more likely once we test it.

---

## What this plan does NOT solve

The benchmark's decisive validation gate (per `project_synth_evaluation_framework.md`):

- Spearman ρ > 0.7 between two algorithms' synth rankings vs their real rankings
- Requires ≥20 real-world top-down pharma-bin captures + ≥2 algorithm implementations

Until L515 is connected (or capture team delivers), this gate stays closed. The work in this plan makes synth *better positioned* to pass the gate once it opens.

---

## Sources

- [SynTable: Synthetic Data Pipeline for Cluttered Tabletop UOAIS (arXiv 2307.07333)](https://arxiv.org/html/2307.07333v3)
- [Lehrmann et al. — Enhancement of 3D Camera Synthetic Training Data with Noise Models (arXiv 2402.16514)](https://arxiv.org/html/2402.16514v1)
- [DR Survey for Object Detection (ACM TOMM 2024)](https://dl.acm.org/doi/10.1145/3637064)
- [Realism vs Domain Randomization for Industrial Object Detection (Sensors 2021)](https://www.mdpi.com/1424-8220/21/23/7901)
- [Benchmark Saturation Study (arXiv 2602.16763)](https://arxiv.org/html/2602.16763v1)
- [DR for Manufacturing — Comprehensive Study (Springer 2025)](https://link.springer.com/article/10.1007/s44196-025-00817-4)
- [simkinect](https://github.com/ankurhanda/simkinect) · [simsense](https://github.com/angli66/simsense) · [render_kinect](https://github.com/jbohg/render_kinect)
- [project_synth_evaluation_framework.md](../../../.claude/projects/-home-kosal-cbnu-project-AI-picking-arm-robot/memory/project_synth_evaluation_framework.md) — internal 4-layer eval framework

---

## Next concrete action

**P0 first.** Pick Option A/B/C from [`sample_data_naming_convention.md`](sample_data_naming_convention.md), do the migration, verify a render still works. Then proceed to P1 (class imbalance re-render) on the cleaned layout — re-render with consistent 7-mesh config so per-class statistics become interpretable.
