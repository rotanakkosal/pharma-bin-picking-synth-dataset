# L515-Specific Depth Noise Plan

**Date:** 2026-05-08
**Status:** APPROVED for implementation; revised after independent reviewer audit (see `docs/agent-feedback/l515_noise_plan/review_2026-05-08.md`).
**Driver:** Current `depth_noise.py` uses Lehrmann 2024's polynomial coefficients fit for **Kinect v1/v2 + MotionCam-3D**, NOT for the Intel L515. Literature shows our model is ~15× too noisy and misses the L515's actual dominant failure modes.

## Revisions in this version (post-review)

The independent reviewer flagged four critical issues, six important gaps, and four nice-to-haves. After triage:

| ID | Finding | Action |
|---|---|---|
| **A1** | 0.25 mm internal quantization is destroyed by `*1000 → uint16` storage | **Fix.** Switch storage to `*4000`, expose `depth_unit_m: 0.00025` (BOP convention). Migrate 5 hardcoded-mm consumer scripts via centralized helper. |
| **A2** | Scope B drops systematic bias, but back-projected GT depends on absolute depth | **Fix.** Promote 5 mm radial bias from Scope C → B. Apply to saved depth only. |
| **A3** | Visible-luminance is a wrong proxy for 860 nm IR specular/dark dropout | **Partial fix.** Augment luminance with surface-normal angle from BlenderProc. Label as approximate in metadata. Full BSDF integration deferred to V3. |
| **A4** | No grazing-incidence dropout — exactly the dominant failure for top-down cylinders | **Fix.** Add `cos(angle_to_camera)` term using BlenderProc normal pass. |
| **B1** | Polynomial coefficients are fitted to bound, not from a paper | **Fix.** Honest metadata: `"derivation": "fitted to Berlin 2021 upper-bound; not from published parametric model"`. |
| **B2** | Firmware preset not specified | **Fix.** Add `"firmware_preset_target": "Short Range"` to metadata. |
| **B3** | Near-range dropout (<0.25 m blind zone) | **Document only** — our 1.286 m setup is well outside the blind zone. |
| **B4** | RGB-depth registration error not modeled (real L515 has 2–4 px misalignment) | **Fix.** 0–3 px random integer shift between saved RGB and saved depth. |
| **B5** | No confidence map output | **Defer to V3** — separate pipeline-output change, not core noise. |
| **B6** | Cheap proxy validation feasible NOW (public L515 captures) | **Defer.** 25 min search yielded only Open3D `L515_test`/`L515_JackJack` bag files (single-subject, require Open3D install + bag→PNG conversion, ~1-2 hr setup). Revisit when team has real captures. Document in metadata. |
| **C1** | BSDF passes for material recovery | **Defer to V3.** |
| **C2** | L535/D455 migration paragraph | **Add to plan.** |
| **C3** | Temporal noise | **TODO comment only.** |
| **C4** | Bin-corner multipath | **TODO comment only.** |

The reviewer's verdict — *"proceed with Scope B, with three modifications"* — is honored: Scope B as implemented now includes A1–A4 fixes, B1/B2/B4 polish, with B5/B6 explicitly deferred and documented.

---

## TL;DR

In one sentence: **Replace generic ToF noise (Lehrmann/Kinect) with L515-specific noise: much smaller Gaussian noise + material-dependent dropouts that actually represent how the L515 fails.**

| What | Current model | What L515 actually does | Source |
|---|---|---|---|
| Axial std at 1m | 7.6 mm | **< 0.5 mm** | Servi 2021, Berlin 2021 |
| Quantization | 1.0 mm | **0.25 mm** | Intel L515 datasheet |
| Edge bleed | 3× extra noise | small (single-beam scan) | Intel docs (multipath section) |
| Dropouts | random 1% | **concentrated on specular + dark surfaces** | Intel optimization guide |
| Saturation | not modeled | bright reflections → no return | Intel docs |
| Systematic bias | not modeled | up to 20cm at edges of range | GitHub Issues #10168 |
| Ambient-light sensitivity | not modeled | degrades at 860nm wavelengths | Intel datasheet |

**Decision needed:** which subset of these to implement (recommended scopes A/B/C below).

---

## What the literature actually says about L515

### Precision (random noise around the true value)

**Servi et al. 2021** ([Metrological Characterization, Sensors 21:7770](https://www.mdpi.com/1424-8220/21/22/7770)) — applied ISO 10360-13 methodology in the close range (100-1500 mm). Their key finding: *"The L515 device performed better on systematic depth errors and showed a better ability in the representation of planar surfaces compared to D415/D455."*

**Berlin et al. 2021** ([SPIE 11782, "Measurement accuracy of the lidar camera Intel RealSense L515"](https://www.spiedigitallibrary.org/conference-proceedings-of-spie/11782/2592570/Measurement-accuracy-and-practical-assessment-of-the-lidar-camera-Intel/10.1117/12.2592570.short)) — measured precision: *"standard deviation as a measure of precision less than 0.5 mm for distances of up to 3.5 m."*

**Lourenço et al. 2021** ([VISAPP 2021](https://commandia.unizar.es/wp-content/uploads/VISAPP_2021_frlourenco.pdf)) — comparative evaluation. L515 has best raw precision in close range among the three tested.

**Conclusion:** at our 1.286 m camera height, σ ≈ 0.5 mm is the right number. Our current 7.6 mm model is **15× too noisy.**

### Systematic bias (offset from the true value)

**GitHub Issue #10168 ([L515 Depth Bias](https://github.com/IntelRealSense/librealsense/issues/10168)):** Users report bias of up to 20 cm at certain ranges (e.g., reads 1.10-1.15 m when actual = 1.30 m). Intel recommends the "Short Range" preset to mitigate.

**Servi 2021** also discusses systematic errors as a separate component from precision.

**Conclusion:** L515 has nontrivial systematic bias (5-20 mm typical at our range, can be bigger near sensor limits). This is currently NOT modeled in our pipeline.

### Failure modes (where depth is missing or unreliable)

From Intel's official optimization guide ([L515 Range Optimization](https://www.intelrealsense.com/optimizing-the-lidar-camera-l515-range/)):

> *"With specular reflection the laser light may not get reflected back into the receiver for detection, and no depth value will be registered."*

> *"Dark gray and black colors absorb light, making it difficult for depth cameras to obtain detail from them."*

> *"If the reflected light is too strong, the receiver will saturate ... resulting in poor or no depth."*

> *"Direct sunlight, sunlight through windows, halogen, and some LED lights containing IR at 860nm can affect camera performance."*

**GitHub Issue #9748 ([Incorrect depth on plants/vegetation](https://github.com/IntelRealSense/librealsense/issues/9748)):** Documents that complex/textured surfaces (vegetation, fabric, etc.) produce more dropouts.

**Conclusion:** L515 dropouts are **NOT random and uniform**. They concentrate on:
1. Specular surfaces (glossy bottle caps) → null return
2. Dark surfaces (black labels, shadows) → noisy or null return
3. Saturated regions (bright IR reflections) → null return
4. Edge regions (multipath rejection by firmware) → null return

Our current 1% uniform random dropout is the wrong distribution.

### Quantization

L515 datasheet ([Mouser PDF, Rev 002](https://www.mouser.com/datasheet/2/612/Intel_RealSense_LiDAR_L515_Datasheet_Rev002-1713847.pdf)) lists:
- Output depth resolution: 1024 × 768
- Depth bit precision implies sub-mm output bins (~0.25 mm at our distances)

Our current 1 mm quantization is too coarse.

### Edge multipath

Intel docs note that L515 has *less* multipath than other ToF sensors because of single-beam scanning ("only one beam of light experiences multipath trip at a given time"). Edge bleed is real but smaller in magnitude than for global-shutter ToF.

Our current 3× edge noise multiplier is too aggressive.

### Existing synthetic L515 noise models

Searched for existing implementations specifically targeting L515:
- **Sim-Suction** ([GitHub](https://github.com/junchengli1/Sim-Suction-API)): generic depth noise, not L515-specific
- **simkinect / simsense / render_kinect**: Kinect-specific, not directly applicable
- **CDMs (Camera Depth Models)** mentioned in some 2024 papers: some include L515 but are learned models, not analytical
- **No published analytical L515 noise simulator found** as of search.

**Conclusion:** we'll have to compose one from the empirical numbers above. Mark calibration as TBD until real captures arrive.

---

## Current model — gap analysis

The `depth_noise.py` we shipped on 2026-05-08 (before this review) uses these defaults:

| Parameter | Current value | L515-accurate target | Gap factor |
|---|---|---|---|
| `axial_a0_mm` | 1.0 | ~0.3 | 3.3× |
| `axial_a1_mm` | 2.0 | ~0.0 (precision flat across range) | infinite |
| `axial_a2_mm` | 1.5 | ~0.0 | infinite |
| Effective σ at z=1.286m | 7.6 mm | **0.5 mm** | **15.2×** |
| `quant_mm` | 1.0 | 0.25 | 4× |
| `dropout_rate` (uniform) | 0.01 | 0.005 base + concentrated drops | distribution wrong |
| `edge_noise_mult` | 3.0 | 1.5 | 2× |
| Specular dropout | not modeled | 5-15% on bright pixels | missing |
| Dark dropout | not modeled | 3-10% on dark pixels | missing |
| Systematic bias | not modeled | 5-20 mm | missing |
| Ambient light degradation | not modeled | scene-dependent | missing |

**Verdict:** the noise we apply is too aggressive *in the wrong way*. Real L515 is much more *precise* than our model says, but it has *concentrated failure modes* we don't model. The net effect is that synth depth currently looks more like a noisy Kinect than a clean-but-failure-prone L515.

---

## Proposed L515 model

Three implementation scopes, choose one:

### Scope A — Quick correction (~15 min, smallest change)

Just fix the magnitudes. Don't add new failure modes.

**Changes to `depth_noise.py` `DEFAULTS`:**
```python
"axial_a0_mm":     0.3,    # was 1.0
"axial_a1_mm":     0.1,    # was 2.0 — minimal z-dependence per Servi/Berlin
"axial_a2_mm":     0.0,    # was 1.5
"quant_mm":        0.25,   # was 1.0 — match L515 datasheet
"dropout_rate":    0.005,  # was 0.01 — half (uniform component)
"edge_noise_mult": 1.5,    # was 3.0 — L515 has less multipath
```

At z=1.286m: σ = (0.3 + 0.1·1.286 + 0·1.286²) × 1.25 ≈ **0.54 mm** — matches Berlin 2021's < 0.5 mm spec.

**What this gets us:** depth values are now realistic L515 magnitudes. Does NOT add the dominant L515 failure modes (specular, dark, saturation).

**What this misses:** the actual reason real L515 captures look different from synth — material-dependent failures.

### Scope B — Scope A + material-dependent dropouts (~1 hour)

A + new function `apply_material_dropouts(depth, rgb, ...)` that uses the rendered RGB to estimate per-pixel reflectivity proxy:

```python
# Convert RGB to luminance
lum = 0.299*R + 0.587*G + 0.114*B
# Specular dropout: very bright pixels (highlights, glare) → likely specular
specular_mask = lum > 0.92
specular_drop = rng.random(shape) < specular_dropout_rate  # default 0.10
out[specular_mask & specular_drop] = 0
# Dark dropout: very dark pixels (shadows, black labels) → likely low return
dark_mask = lum < 0.10
dark_drop = rng.random(shape) < dark_dropout_rate  # default 0.05
out[dark_mask & dark_drop] = 0
```

**What this gets us:** the depth.png now FAILS in the same places real L515 would fail on the same scene (e.g., glossy cap tops, dark text on labels).

**Tunables exposed in `depth_noise_meta`:**
- `specular_dropout_rate`, `specular_luminance_threshold`
- `dark_dropout_rate`, `dark_luminance_threshold`

**Cost:** small extra compute (one luminance pass + two boolean masks).

### Scope C — Scope B + systematic bias (~1.5 hours)

B + a small systematic bias (5-15mm offset) that varies smoothly across the image to mimic the L515's lens-distortion-related depth bias.

```python
# Systematic bias: smooth low-frequency offset across image
xx, yy = np.meshgrid(np.linspace(-1, 1, W), np.linspace(-1, 1, H))
bias_field_m = (
    bias_amplitude_m * (xx**2 + yy**2)  # quadratic radial
    + rng.normal(0, bias_seed_std_m, shape)  # low-freq seed
)
out = out + bias_field_m
```

**What this gets us:** matches the GitHub Issue #10168 reports of cm-scale depth bias.

**Risk:** systematic bias affects GT computation if applied to the depth used for suction GT. Must keep it ONLY on the saved depth.png.

**Verdict on scope C:** lower priority. Most algorithms are bias-tolerant (they use depth gradients more than absolute depth). Skip unless real captures show this matters.

---

## Recommended scope: B

Reasoning:
- **Scope A alone is wrong-quality** — fewer mm of Gaussian noise, but nothing that models the L515's actual dominant failure pattern. Algorithms validated on this would still see "too clean" synth.
- **Scope B addresses the real gap** — material-dependent dropouts are *the* characteristic L515 visual signature, per Intel's own docs.
- **Scope C is premature** — systematic bias matters less for our task (segmentation + grasp), and we can't calibrate it without real captures anyway.

---

## Implementation plan (Scope B revised, post-review)

Migration order matters: land the unit-handling helper + consumer migrations against v1 data first (no-op verification), THEN flip the storage format. Reversing this order opens a window where stale consumers silently misread.

```
═══ Phase 1 — Storage-unit migration (A1) ═══
Step 1.1 (~15 min): scripts/depth_io.py with load_depth_m(scene_dir)
    - Reads depth_unit_m from scene_gt.json (BOP convention)
    - Falls back to 0.001 (legacy v1 mm) if field is absent → no-op for v1 data
    - Returns depth as float32 meters
    - Single source of truth for depth-unit handling

Step 1.2 (~25 min): Migrate 5 consumers to use load_depth_m()
    - synth-dataset/scripts/convert/convert_scene_to_simsuction.py (3 callsites)
    - synth-dataset/scripts/eval/dataset_qc.py (3 callsites)
    - synth-dataset/scripts/viz/viz_simsuction_grasps.py (1 callsite)
    - synth-dataset/scripts/eval/eval_uoais_on_synth.py (1 callsite, then *1000 for normalize_depth)
    - pharma-bin-picking/tools/run_on_synth.py (similar pattern, fix at call site, do NOT touch utils.py:normalize_depth)
    - VERIFY: each consumer produces byte-identical output against current v1 data
      (since load_depth_m falls back to 0.001, this should be a no-op)

Step 1.3 (~10 min): Flip writer in generate_scene.py
    - depth_noisy_m * 1000 → depth_noisy_m * 4000
    - meta["depth_unit"] = "mm" → meta["depth_unit_m"] = 0.00025
    - VERIFY: re-render scene_999, confirm consumers still work on v2 data

═══ Phase 2 — L515-accurate noise model ═══
Step 2.1 (~15 min): Update DEFAULTS in depth_noise.py to L515 magnitudes
    - axial_a0_mm 1.0 → 0.3
    - axial_a1_mm 2.0 → 0.1
    - axial_a2_mm 1.5 → 0.0
    - quant_mm 1.0 → 0.25 (now actually realized via *4000 storage)
    - dropout_rate 0.01 → 0.005
    - edge_noise_mult 3.0 → 1.5

Step 2.2 (~30 min): Material + normal-angle dropout (A3 + A4 combined)
    - apply_material_dropouts(depth_m, rgb_uint8, normals_cam, rng, ...)
    - Three terms (multiply probabilities, then random-test):
        specular: lum > 0.92 → +10% dropout (A3)
        dark:     lum < 0.10 → +5% dropout (A3)
        grazing:  cos(normal · -ray) → smoothstep(0.5, 0.2) → up to +50% dropout (A4)
    - All terms use the rendered surface-normal pass from BlenderProc

Step 2.3 (~15 min): Add 5 mm radial systematic bias (A2)
    - Apply ONLY to saved depth (not to depth_m used by suction GT)
    - Quadratic radial: bias = 0.005 * (xx² + yy²) m, applied per-pixel
    - Plus low-freq Gaussian seed (mean=0, std=2 mm) for non-symmetric component

Step 2.4 (~10 min): RGB-depth registration jitter (B4)
    - Random per-scene integer shift in [0, 3] px
    - Applied as np.roll on the saved depth array AFTER all noise
    - Documented as a tunable in metadata

═══ Phase 3 — Metadata + integration ═══
Step 3.1 (~15 min): Update generate_scene.py integration
    - Pass rendered RGB (data["colors"][0]) into apply_l515_noise
    - Pass surface-normal pass (data["normals"][0] from bproc) into apply_l515_noise
    - Update meta dict with new depth_unit_m, depth_noise_meta v2-l515

Step 3.2 (~10 min): Update make_noise_meta() with all new fields
    - "version": "v2-l515"
    - "derivation": "fitted to Berlin 2021 upper-bound; not from published parametric model"  (B1)
    - "firmware_preset_target": "Short Range"  (B2)
    - "proxy_basis": "visible_luminance + normal_angle (approximate; not 860nm IR)"  (A3)
    - Two-criterion validation note: "validation_criteria": ["magnitude (σ<0.5mm on flat tray)", "dropout-location vs RGB-luminance-extremes spatial correlation"]
    - "validation_status": "deferred — no public L515 capture sufficient for dropout-pattern validation; awaiting team's L515 hardware"
    - Citations: Servi 2021, Berlin 2021, Intel L515 docs/datasheet, librealsense issue #6636 (depth_units)

═══ Phase 4 — Validation ═══
Step 4.1 (~15 min): Run scene 999, eyeball-check three things
    - depth.png on flat tray pixels: σ should be ~0.5 mm (was ~5 mm)
    - depth.png on bottle caps with highlights: should have black holes (specular drop)
    - depth.png on bottle SIDES (cylindrical, near grazing): should have widespread dropout (A4)

Step 4.2 (~15 min): Run 5-scene batch + dataset_qc.py + visual diff
    - All 14 integrity checks should pass
    - Score distributions should be near-identical to V1.5 baseline (noise on saved depth only, not GT)
    - Compare scene_000001 RGB unchanged from V1.5; depth dramatically different
```

**Total: ~175 min** (was 85 min original estimate; review identified +90 min of must-fix items).

No new dependencies. All work in this repo + minimal touches in sibling pharma-bin-picking/.

---

## Validation plan

### What we can check without real captures

| Property | How |
|---|---|
| Magnitudes match spec | σ on flat tray-floor pixels in a rendered scene → expect ~0.5 mm at 1.286 m camera height |
| Dropouts concentrate correctly | Visualize: black holes on bright spots (cap glints), speckle on dark text, broad dropout on bottle sides (grazing) |
| GT computation untouched | suction-point scores identical to V1.5 baseline (proves noise didn't leak into GT) |
| Existing pipeline still passes | All 14 integrity checks remain at 0 violations |

### Two-criterion validation when real L515 captures arrive (B6 deferred)

Magnitude validation alone does **not** prove the dropout model is right. Must test BOTH:

**Criterion 1 — Magnitude.** σ on flat tray-floor pixels in real captures should match Berlin 2021's < 0.5 mm bound. Run the same statistic on synth and on real, compare distributions (KS-distance, χ² goodness-of-fit).

**Criterion 2 — Dropout location.** Dropout pixels in real captures should spatially correlate with rendered RGB luminance extremes (lum > 0.92 ∪ lum < 0.10) on the same scene. This is the actual Scope B claim — magnitude validation alone doesn't test it. Procedure:
1. Capture the same physical bin scene under real L515 and render the same scene synth (matching object placements as best we can)
2. For each scene, compute the dropout-mask (depth = 0 pixels)
3. For each scene, compute luminance extremes on RGB
4. Spatial correlation: IoU between (real dropout) and (luminance extreme) masks should be ≥ 0.6 (proxy: synth dropout should land where real dropout lands)
5. If IoU is low, recalibrate `specular_dropout_rate`, `dark_dropout_rate`, `grazing_dropout_max`, and adjust luminance thresholds

Update `depth_noise_meta.validation_status` from `"deferred"` to `"calibrated to L515 SN xxx 2026-MM-DD; magnitude_match=<value>; dropout_iou=<value>"` once both criteria pass.

### Non-validation — proxies considered and rejected for V2

- **LM-O / SHOP-VRB / T-LESS / CLUBS comparison:** these use PrimeSense / Kinect / D415 — different sensors with different failure modes. Validating L515 dropout patterns against structured-light or stereo would push our parameters in the wrong direction. Per reviewer guidance, do not substitute non-L515 datasets.
- **Open3D `L515_test` / `L515_JackJack` .bag samples:** authentic L515, but require Open3D installation + bag→PNG conversion (~1-2 hr setup) and are single-subject scenes (not bin clutter). Revisit if team's real L515 is delayed beyond a few weeks; otherwise wait for in-house captures.

---

## Open questions for user (to resolve before implementing)

1. **Scope choice — A, B, or C?** I recommend B. A is too narrow; C needs real data to calibrate.

2. **Specular threshold (`luminance > 0.92`)** — should this be per-class (e.g., shinier glass classes get higher threshold)? V1 says no (apply uniformly), accept that procedural-label bottles' caps are similar to photoreal ones'.

3. **Dropout rates (10% specular, 5% dark)** — these are educated guesses. Real L515 numbers depend on cap material and label print quality. Approve or override?

4. **Backward compatibility:** the `depth_noise_meta.version` will bump to `v2-l515`. Existing scene_gt.json files with `v1` remain valid; users decide if they want to re-render to v2. OK?

5. **Should we also apply ambient-light degradation?** Intel docs warn about 860nm IR sources (sunlight, halogen). Our scene is indoor with point lights — probably negligible, but we could add a small uniform noise floor. Skip for V2; revisit if real captures show this matters.

---

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Reduced noise eliminates the realism benefit (synth becomes "too clean" again) | Low — grazing + material dropouts compensate | Verify visually on scene 999; can boost dropout rates if needed |
| Material-dropout uses visible RGB, but RGB has its own randomization (CCT lights from P3) → dropout pattern varies scene-to-scene unpredictably | Medium | Acceptable: real L515 also varies dropout based on lighting. Document explicitly. |
| Algorithm-development on this updated synth requires re-running all prior validation | Low | Existing `output/` is small (5 scenes), trivial to re-render |
| L515 was discontinued by Intel in 2022 — research interest shifting to L535/D435/D455 successors | Medium (long-term) | See L535/D455 migration note below |
| Storage migration (Phase 1) breaks a hardcoded-mm consumer we missed in the grep | Low | Phase 1.2 verifies byte-identical output against v1 data before flipping the writer |
| Phase 1 helper's 0.001 fallback masks future v3+ format changes silently | Low | Document explicitly in helper docstring; future format bumps must always set `depth_unit_m` field |

## L535/D455 migration note (C2)

L515 is EOL since March 2022 (Intel PCN118463-00). If the team migrates to L535 or D455:

- **L535:** Intel's announced successor (released late 2024). Same MEMS-LiDAR architecture as L515 — noise model parameters should transfer with re-calibration of `Mn` and dropout rates. The fundamental failure modes (specular, dark, grazing) remain the same.
- **D455 (or D435i):** Stereo-IR active, fundamentally different physics. Noise scales as z² much more strongly. Dropouts are texture-dependent (not specular-dependent). The current model is **not portable** to D455 by tweaking constants — it would need a new noise module (`scripts/depth_noise_d455.py`).

The `depth_noise_meta.firmware_preset_target` and citation block should be updated, and the version field bumped (e.g., `v3-l535`, `v3-d455`) when migrating.

---

## Citations

- **Servi et al. 2021.** Metrological Characterization and Comparison of D415, D455, L515 RealSense Devices in the Close Range. *Sensors* 21(22):7770. [MDPI](https://www.mdpi.com/1424-8220/21/22/7770) · [PMC8622561](https://pmc.ncbi.nlm.nih.gov/articles/PMC8622561/)
- **Berlin et al. 2021.** Measurement accuracy and practical assessment of the lidar camera Intel RealSense L515. *SPIE Proc.* 11782. [SPIE](https://www.spiedigitallibrary.org/conference-proceedings-of-spie/11782/2592570/Measurement-accuracy-and-practical-assessment-of-the-lidar-camera-Intel/10.1117/12.2592570.short)
- **Lourenço et al. 2021.** Intel RealSense SR305, D415 and L515: Experimental Evaluation and Comparison of Depth Estimation. VISAPP 2021. [PDF](https://commandia.unizar.es/wp-content/uploads/VISAPP_2021_frlourenco.pdf)
- **Lehrmann et al. 2024.** Enhancement of 3D Camera Synthetic Training Data with Noise Models. [arXiv:2402.16514](https://arxiv.org/html/2402.16514v1) — used as the methodology reference even though their fits were for Kinect/MotionCam
- **Intel RealSense L515 Datasheet, Rev 002.** [Mouser PDF](https://www.mouser.com/datasheet/2/612/Intel_RealSense_LiDAR_L515_Datasheet_Rev002-1713847.pdf)
- **Intel RealSense L515 Optimization Guide.** [Range optimization article](https://www.intelrealsense.com/optimizing-the-lidar-camera-l515-range/)
- **GitHub librealsense Issue #10168 — L515 Depth Bias.** [Link](https://github.com/IntelRealSense/librealsense/issues/10168) — user-reported systematic bias
- **GitHub librealsense Issue #9748 — Incorrect depth on plants/vegetation.** [Link](https://github.com/IntelRealSense/librealsense/issues/9748) — material-dependent failure pattern

---

## Decision required from user

**Before I implement:**
1. Confirm scope (A / B / C). My recommendation: **B**.
2. Confirm parameter defaults (specular threshold, dropout rates) or override with values you prefer.
3. Confirm OK to bump `depth_noise_meta.version` from `v1` to `v2-l515` (existing scenes keep `v1`).

After your sign-off, implementation per "Implementation plan (scope B)" above takes ~85 minutes.
