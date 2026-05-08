# L515-Specific Depth Noise Plan

**Date:** 2026-05-08
**Status:** Plan, awaiting user review BEFORE implementation
**Driver:** Current `depth_noise.py` uses Lehrmann 2024's polynomial coefficients fit for **Kinect v1/v2 + MotionCam-3D**, NOT for the Intel L515. Literature shows our model is ~15× too noisy and misses the L515's actual dominant failure modes.

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

## Implementation plan (scope B, if approved)

```
Step 1 (~10 min): Update DEFAULTS in depth_noise.py to L515 magnitudes
    - axial_a0_mm 1.0 → 0.3
    - axial_a1_mm 2.0 → 0.1
    - axial_a2_mm 1.5 → 0.0
    - quant_mm 1.0 → 0.25
    - dropout_rate 0.01 → 0.005
    - edge_noise_mult 3.0 → 1.5

Step 2 (~30 min): Add material-dependent dropout helper
    - new function apply_material_dropouts(depth_m, rgb_uint8, rng, ...)
    - hook into apply_l515_noise as a new step (or expose separately)
    - new DEFAULTS:
        specular_luminance_threshold = 0.92
        specular_dropout_rate = 0.10
        dark_luminance_threshold = 0.10
        dark_dropout_rate = 0.05

Step 3 (~10 min): Update generate_scene.py call site
    - pass rendered RGB (data["colors"][0]) into apply_l515_noise
    - update make_noise_meta() to include new fields

Step 4 (~10 min): Update depth_noise_meta in scene_gt.json
    - bump version "v1" → "v2-l515"
    - add citations: Servi 2021, Berlin 2021, Intel L515 docs
    - update calibration_status

Step 5 (~15 min): Run scene 999, eyeball check
    - depth.png should look much cleaner overall (lower σ)
    - bottle caps with bright highlights should have black holes (specular drop)
    - dark label regions should have scattered dropouts

Step 6 (~10 min): Run 5-scene QC, confirm no regressions
```

Total: ~85 min. No new dependencies.

---

## Validation plan

Without real L515 captures, we can validate three properties:

| Property | How |
|---|---|
| Magnitudes match spec | Compute σ on flat tray-floor pixels in a rendered scene → expect ~0.5 mm |
| Dropouts concentrate correctly | Visualize: should see black holes on bright spots (cap glints) and dark spots (text), not random scatter |
| Existing pipeline still passes | All 14 integrity checks remain at 0 violations |

When real L515 captures arrive (Layer 3 work):
- Capture same scene under both synth and real
- Compare depth histograms
- Tune `Mn`, `dropout_rate`, `specular_dropout_rate` to minimize KS-distance between distributions
- Update `depth_noise_meta.calibration_status` from "uncalibrated" to "calibrated to L515 SN xxx 2026-MM-DD"

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
| Reduced noise eliminates the realism benefit (synth becomes "too clean" again) | Low — material dropouts compensate | Verify visually on scene 999; can boost dropout rates if needed |
| Material-dropout uses RGB, but RGB has its own randomization (CCT lights from P3) → dropout pattern varies scene-to-scene unpredictably | Medium | Document this as a feature: real L515 ALSO varies dropout patterns based on lighting. Acceptable. |
| Algorithm-development on this updated synth requires re-running all prior validation | Low | Existing `output/` is small (5 scenes), trivial to re-render |
| L515 was discontinued by Intel in 2022 — research interest is shifting to L535/D435 successors | Medium (long-term) | Document the L515 specificity; if hardware changes, revisit calibration |

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
