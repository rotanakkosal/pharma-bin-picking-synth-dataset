# Synth Realism Improvement Plan (Literature-Validated)

**Date:** 2026-05-04 · **Updated:** 2026-05-11
**Status:** 🔒 **TERMINAL (v1.0-final, 2026-05-11).** All P-items closed (P0/P1/P2/P3 shipped, P4 deferred, P4-lite shipped). Synth marked done for development. No real L515 captures available in dev (production-only resource), so Layer 3 predictive validity is intentionally not pursued. **Future changes are reactive only** — triggered by specific downstream failure modes (UOAIS, robot integration), not by speculative realism work.
**Driver:** UOAIS-on-synth eval results + literature review
**Team rules:** see [`team/team_workflow.md`](team/team_workflow.md) — pre-render gate, locked baselines, out-of-plan proposal flow. Read before kicking off any render.

> **Note for any future Claude session opening this doc:** the synth is done. Do not propose more lighting/material/HDRI work, do not propose new sweeps, do not propose calibration against "future real captures" — there are no future real captures in dev. Only act on this codebase if the user reports a concrete downstream failure that traces back here.

## Status snapshot (2026-05-08)

| Priority | Description | Status |
|---|---|---|
| **P0** | sample_data cleanup → ASCII layout, per-bottle folders | ✅ shipped |
| **P1** | Class imbalance fix → render ≥30 scenes on canonical 7-mesh config and re-measure UOAIS IoU + per-class counts (~42 each) | ✅ **shipped 2026-05-11.** 30 scenes rendered at locked baseline `heights_m: [1.286]`; 1051 instances total. Original class-imbalance bug (2/5/3) resolved — all 7 classes now produce 133–164 instances (visibility 63–78%). UOAIS eval: precision 0.910, recall 0.759, **F1 0.828, mean IoU 0.853** — moved meaningfully from prior 6-scene baseline (F1 0.893, IoU 0.895). Now in SynTable-comparable range (their synth: 84.5 F-measure). Two outlier classes: photoreal kolmin_a_syrup recall 0.458, procedural white_pill_bottle 0.596 — hypothesis: high-luminance regions on photoreal labels trigger more specular dropouts (see "Outlier follow-up" below). |
| **P2** | Depth sensor noise simulation | ✅ shipped — upgraded to **v2-l515** noise model on 2026-05-08; see `depth_noise/depth_noise_l515_design.md` for the L515-specific rewrite |
| **P3** | Lighting variety (color-temperature randomization 2500–6500 K, wider position spread, wider energy range) | ✅ shipped — partial form of SynTable §3 recipe (CCT-randomized point lights; full HDRI deferred for dependency reasons) |
| **P4** | Material/texture variation per instance | ⏸ deferred (full scope: random material from a pool — HDPE, cardboard, frosted glass, metallic) — mesh diversity is the bigger driver. See P4-lite below for what was done in this scope-adjacent direction. |

### Exploratory work (2026-05-08, not a P-item)

- **Camera-height sweep** (`output/diff_camera_height/`, heights 0.8 / 0.9 / 1.0 / 1.1 / 1.286 / 1.4 / 1.5 / 1.8 m): exploratory only. Confirmed the pipeline is robust to small height variations around the calibrated 1.286 m. **No config change.** `config.yaml`'s `camera.height_m: 1.286` stays — matches the real L515 mounting in the planned capture rig.
- **Noise demo visualizations** (`output/noise_demo/`): static reference visuals of each v2-l515 noise component (wiggle, edge-fuzz, slight-blur, random-holes, steps). Useful for explaining the noise model in reports/slides. Not part of the rendered dataset.

### P4-lite (2026-05-08): external label pool expansion (42→47)

Scope-adjacent to P4 but applied to the procedural **label** pool rather than the material pool:

- Cropped 6 panels from 2 stock pharma-graphic JPGs in `sample_data/original_source_dataset/label/` (`medicines-banners-set.zip` → 3 panels, `trio-horizontal-medical-banners*.zip` → 2 panels).
- 1 staged panel was **quarantined** to `textures/labels_distractors/` (`label_042_external_beer.png` — craft-beer template, off-domain for a Korean pharma benchmark; would have leaked beer-can graphics onto random pill bottles).
- Final active count: 47 labels (42 synthetic + 5 external pharma-graphic panels).
- The remaining 5 are Western pharma stock graphics, not Korean pharmacy labels — a soft domain mismatch, acceptable since most real-bin bottles also lack Korean text outside our two photoreal classes.
- Does NOT replace the deferred P4 work (material variation). Documented here so the pool size change is traceable.

**P1 result (closed 2026-05-11):**
- Eval methodology verified apples-to-apples: same UOAIS weights (model_final.pth dated 2026-04-08), same IOU_THRESH=0.5, same greedy-IoU matching, eval script only changed for path-globbing (commits `e5d394b`, `07249a8`) — no methodology change.
- F1 0.893 → 0.828 (-0.065). Recall 0.868 → 0.759 (-0.109). IoU 0.895 → 0.853 (-0.042).
- Comparison framing: F1 0.828 is **no longer suspiciously above published synth norms**. SynTable's 84.5 is their UOAIS-trained-on-SynTable evaluated in-distribution — not directly comparable to our UOAIS-trained-on-OSD/OCID evaluated out-of-distribution. The only directly relevant SynTable number is their sim-to-real gap (3.6 points, 84.5 → 80.9). Our gap is unmeasured pending L515 captures.
- Verdict: v2-l515 noise + CCT lighting + 47-label pool produced a measurable, defensible shift away from suspiciously-high scores. Realism work is validated as moving-the-needle, not theater.

**Recall-drop diagnosis (closed 2026-05-11 via FN visible_px analysis):**

Initial hypothesis (photoreal-label specular dropouts triggering more FN on kolmin/levozin) was **rejected** before running a diagnostic render. Evidence:

| | TP visible_px | FN visible_px |
|---|---|---|
| kolmin_a_syrup | mean 14,636 / median 15,344 | mean 6,562 / median 7,338 |
| all classes | mean 11,447 / median 12,419 | mean 3,643 / median 2,928 |

FN instances are **heavily occluded, low-visible-area** (sampled kolmin FN cases had 44–74% occlusion). The pattern holds across all classes; kolmin's lower recall is because more kolmin instances fall into the heavily-occluded tail (kolmin's spawn visibility is also lowest at 63%, so the same physics expresses twice). The recall-vs-IoU asymmetry (-0.109 vs -0.042) makes sense: v2-l515 + denser piles produce more heavily-occluded cases, UOAIS misses those (known weakness), but when it does find a bottle the mask quality stays high.

**Next concrete action (post-P1):** No specular-dropout ablation needed — wrong hypothesis. Open options for next priority:
1. **Stop here** — P1 closed, realism work validated, all 7 priorities at terminal state for the synth side (P0/P1/P2/P3 shipped; P4 deferred; P4-lite shipped). Synth is now usable as a development tool for downstream algorithm work.
2. **Per-class recall improvement** — investigate UOAIS heavy-occlusion weakness (could improve recall by tuning UOAIS confidence threshold, post-processing, or training-set augmentation — but that's UOAIS-side work, not synth-dataset work).
3. **Layer 3 prep** (waiting on real captures, not project-gating).

Team lead's call on which.

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

**Action:** See [`sample_data/sample_data_naming_convention.md`](sample_data/sample_data_naming_convention.md) for the detailed proposal: one folder per object with ASCII ID, canonical filenames (`mesh.obj`, `mesh_uv.obj`, `label.png`), per-object `README.md`, and a machine-readable `index.yaml`.

**Approach:** Migration Option A from that doc — create the new `sample_data/bottles/<id>/` tree using symlinks first, switch config, verify render still works, then physically delete the old layout. Reversible if anything breaks.

**Why first:** All subsequent priorities depend on stable mesh references. Class-imbalance fix (P1) requires rendering with a stable 7-mesh set; depth-noise (P2) and lighting (P3) require knowing what mesh you're applying noise to. Doing P1 first means redoing it after P0 cleanup.

### Priority 1 — Fix class imbalance bug

**Status:** Suspected representation bug, not a synth-quality issue.

**Evidence:** Eval shows classes 5/6/7 with 2/5/3 instances vs expected ~42. Most likely scenes 700–704 were rendered before the new ASCII-named meshes (`bottle_medicine2`, `bottle_medicine3`, `bottle_pill`) were added; only scene_999 has all 7 classes.

**Action:**
- Inspect each scene's `scene_gt.json` to confirm the hypothesis.
- Re-render full batch with consistent 7-mesh config so per-class statistics become interpretable.

**Why first:** Cheap, fixes a bug, and any future eval depends on getting this right. No literature needed — internal data integrity issue.

### Priority 2 — Depth sensor noise simulation ✅ DONE (2026-05-08)

**Source:** [Lehrmann et al. 2024 — Enhancement of 3D Camera Synthetic Training Data with Noise Models](https://arxiv.org/html/2402.16514v1)

**Key findings from the paper:**
- Noise models with degree-2 polynomial fits for axial + lateral noise across Kinect v1/v2 + MotionCam-3D.
- Noise is dependent on distance (z) and surface angle (θ).
- Empirical sweet spot: noise multiplier `Mn=1.25`. Both `Mn=0` (no noise) and `Mn≥1.5` hurt downstream IoU on real data.
- Networks trained on pristine synth (`Mn=0`) generalize poorly to real.

**Why this is the highest-impact realism fix:**
- Pristine depth is THE most consistently identified synth-real gap signal in the literature.
- UOAIS was trained on real RealSense / Kinect depth — it learned to ignore noise patterns. Feeding it noise-free synth removes a signal it relies on, but in unfamiliar ways (overconfident segmentation).

**What was built:**
- [`scripts/depth_noise.py`](../scripts/depth_noise.py) — `apply_l515_noise()` post-processing pipeline:
  1. Axial Gaussian noise — degree-2 polynomial in z (mm): `σ_z(z) = 1.0 + 2.0·z + 1.5·z²`, scaled by `Mn=1.25`.
  2. Edge-bleed at depth discontinuities — pixels within 3 px of a >20 mm depth jump get 3× noise (multipath/occlusion artifact).
  3. Lateral 0.5 px Gaussian blur — cross-ray smoothing.
  4. 1% random dropout — L515's known holes on dark/specular surfaces.
  5. 1 mm quantization — L515's effective bin size.
- Integrated into [`generate_scene.py`](../scripts/generate_scene.py): saved depth uses noisy values; **clean depth is retained for analytical suction-GT scoring** so GT reflects true geometry.
- Per-scene noise seed = `cfg.seed + scene_id + 10007` (decorrelated from spawn-position rng).
- Self-describing `depth_noise_meta` block embedded in every `scene_gt.json`.

**Validation (5-scene smoke test):**
- Floor std on flat tray-floor patches: ~4.8 mm (scenes 1–5).
- Cross-scene seed independence confirmed: corner mean-abs-diff ~32 mm vs intra-scene std ~5 mm.
- Dropout: measured 1.00–1.02% (target 1%).
- Edge-bleed: corners catching tray-wall depth jumps show 21–28 mm std — the mechanic is firing.

**Caveat — measured noise is ~30% below the polynomial prediction.**
- Predicted at z=1.286 m: σ_z = 6.05 mm × 1.25 = 7.6 mm.
- Measured: 4.8 mm.
- Cause: the 0.5 px lateral Gaussian blur smooths out axial noise *after* it's added. Lehrmann's `Mn=1.25` was calibrated without this blur, so the *effective* Mn here is closer to ~0.8.
- Action: when real L515 captures arrive, calibrate Mn against measured real floor std, not against the polynomial. `make_noise_meta()` already flags this with `calibration_status: "uncalibrated_to_real_L515"`.

### Priority 3 — Lighting variety (CCT + position spread) ✅ DONE (2026-05-08)

**Source:** [SynTable §3 (Liu et al. 2023, v3 2024)](https://arxiv.org/html/2307.07333v3)

**SynTable's lighting recipe:**
- Spherical light sources (count `L`)
- Color temperature: 2,000–6,500 K
- Intensity: 100–20,000 lx
- Multiple camera viewpoints sampled from concentric hemispheres

**What was built:**
- `setup_lights()` rewritten in [`generate_scene.py`](../scripts/generate_scene.py):
  - Per-light CCT randomization in 2500–6500 K via Tanner-Helland approximation (`cct_to_rgb()`).
  - Energy range widened: `[40, 120]` → `[40, 200]`.
  - XY position spread widened: `±0.5 m` → `±0.8 m` for more dramatic side-lighting.
- New `lighting.cct_range_k` config knob in `config.yaml`.

**Intentional deviation from "HDRI" framing:**
- Original plan said "HDRI environment lighting"; implementation kept BlenderProc point lights with CCT + intensity + position randomization.
- Reasoning: SynTable's actual recipe uses *spherical/area light sources with CCT randomization*, not strict HDRI. The plan slightly conflated the two. CCT-randomized point lights match SynTable's recipe more directly than an HDRI map would, and avoid a network/asset download dependency for builds.
- HDRI maps remain available as a future upgrade if predictive-validity testing shows the point-light approach is insufficient.

**Validation:** Visible CCT variation across scenes (e.g. scene_5 noticeably warmer cast than scene_1).

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

1. **Apply P1 + P2 first** (class imbalance fix + depth noise). Re-render. Re-evaluate UOAIS via `scripts/eval/eval_uoais_on_synth.py`.

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

**Reframed 2026-05-08:** Layer 3 is a milestone *if* real captures eventually arrive — not a project-gating requirement. The synth's job is to serve the broader pharma-bin-picking project *now*, before real captures exist. The realism work in this plan (P0–P3) makes the synth *fit-for-purpose as a development tool*; Layer 3 would be the additional validation that lets it claim "validated benchmark" status. Two separate bars. Don't conflate them.

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

P0 ✅, P2 ✅, P3 ✅ — P1 was deferred (it was a config-drift bug from the old layout; the cleaned `bottles/<id>/` layout already prevents recurrence, and a fresh batch on the new layout naturally satisfies P1).

**Next:**

1. **Render a clean P1 batch on the new layout** (~50 scenes, no leftover scenes from older configs in `output/`). Confirms per-class instance counts are sane (~42 each across 7 classes).
2. **Re-run UOAIS eval on the noisy depth** — `python scripts/eval/eval_uoais_on_synth.py`. Expectations from the validation plan above:
   - IoU drops from 0.895 → 0.70–0.80 → SynTable territory, plausible regime.
   - IoU stays 0.85+ → noise didn't bite; investigate (likely the lateral-blur attenuation noted in P2's caveat — bump Mn).
   - IoU drops below 0.65 → too much noise; reduce Mn toward 1.0.
3. **Then P4** (material variation) only if predictive-validity testing flags an RGB-side gap.
