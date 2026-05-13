# UOAIS-on-synth evaluation methodology

**Date:** 2026-05-12
**Status:** Current. Two adversarial reviews applied (plan + implementation) — see
`pharma-bin/reviewer-feedback/eval_uoais_occlusion_stratified_recall/`.
**Script:** `scripts/eval/eval_uoais_on_synth.py`

This doc records how we score UOAIS predictions against the synth GT, why the
protocol changed on 2026-05-12, and what the v1.1 batch numbers are under the
new protocol. **Important framing:** the headline F1 moved 0.832 → 0.845
between the old and new protocols **because of the protocol change, not because
the model or the GT changed.** The v1.1 render is unchanged; only the eval
script changed.

---

## What changed and why

The original eval (used for the v1.0-final and v1.1 baselines) did three things
that an adversarial review flagged as misleading:

1. **Matched predictions to GT on the amodal masks.** For a 90%-occluded
   bottle the amodal mask is mostly inferred shape — matching there tests
   amodal *completion*, not *detection*. It conflated "didn't find the object"
   with "found it but the hallucinated amodal mask missed IoU 0.5."
2. **Greedy IoU matching at threshold 0.5.** Order-dependent (not reproducible)
   and looser than the Hungarian + F@.75 protocol every published UOAIS paper
   uses — so the numbers weren't comparable to any baseline.
3. **Counted all GT instances**, including ones with a handful of visible
   pixels (down to 1 px). No detector finds a 1-pixel object; including those
   in the recall denominator unfairly tanked recall (the 0.766 figure was
   dominated by ≥80%-occluded, effectively-unobservable instances — 65 of 66
   such instances "missed", 98%).

The diagnostic that triggered the change: of the 246 false negatives under the
old protocol, 67% (165) were bottles ≥50% occluded; 65 were ≥80% occluded.
Recall on bottles ≤30% occluded was 0.94. The GT itself was correct — dataset
QC passes clean — but the eval was reporting an aggregate dominated by buried
instances.

---

## The protocol now

**One matching, three views.** Per scene, a single Hungarian (optimal) 1:1
assignment of predictions to GT on the **visible masks** at IoU ≥ 0.5. The
headline detection numbers, the per-occlusion-bin recall, and F@.75 are all
derived from that one matching, so they are mutually consistent (an
under-segmented prediction spanning two touching bottles can't "detect" both).

| View | What it answers | How |
|---|---|---|
| **Primary — detection** | Did the model find the object? | TP = matched GT with ≥ `--min-visible-px` visible px; FN = unmatched GT ≥ that threshold; FP = unmatched prediction. A prediction matched to a sub-threshold GT is neither TP nor FP (it found a real if tiny object we don't score). Reports P / R / F1 + mean visible-IoU on matched pairs. |
| **Published comparator** | How do we stack against UOAIS-Net's literature numbers? | Per-image Overlap P/R/F (region overlap = **Dice** = 2·inter/(a+b), averaged over scenes) and **F@.75** (fraction of GT whose matched prediction has Dice > 0.75) — visible and amodal. Dice, not IoU, is what UOAIS-Net's "Overlap F-measure" uses (IoU 0.75 ⟺ Dice 0.857, so an IoU criterion would be stricter and the comparison unfair to us). |
| **Stratified** | Where does the model actually fail? | Recall by GT occlusion bin (0–10 / 10–30 / 30–50 / 50–80 / 80–100 / all), plus a separate row for frame-truncated GT (amodal mask touches the image border). |
| **Secondary — amodal completion** | Given it found the object, how good is the occluded-region completion? | Mean IoU between pred_amodal and GT amodal on matched pairs. Noisier than detection, so reported on the side. |

**Small-mask filter.** `--min-visible-px` (default 100). Reported as a sweep
(50 / 100 / 500) so the threshold is visibly not load-bearing — recall is
~flat across it. There is no UOAIS-codebase default to inherit (it uses
standard COCO-style mask AP with no hard cutoff); 100 px is "a sliver, not an
object" for our bottle sizes (a normal unoccluded bottle is 8k–18k visible px).

**Legacy metric kept.** The old greedy-amodal-match-at-IoU0.5 numbers are still
printed, labelled non-standard, as a continuity / regression check (it should
reproduce P 0.910 / R 0.766 / F1 0.832 / amodal-IoU 0.857 on the v1.1 batch).

---

## v1.1 batch results (30 scenes, 1053 GT instances)

**Headline detection** (visible-mask Hungarian, IoU ≥ 0.5, ≥ 100 px filter):

| | value | (legacy v1.0 metric, for continuity) |
|---|---|---|
| precision | 0.917 | 0.910 |
| recall | 0.784 | 0.766 |
| F1 | **0.845** | **0.832** |
| mean visible-IoU on matched | 0.879 | (amodal-IoU 0.857) |

The F1 moved 0.832 → 0.845 **because of the protocol change** (visible-mask
matching + Hungarian + small-mask filter), not the model or GT.

**Published comparator:**

| metric | ours (all GT) | ours (≥100px) | UOAIS-Net on real OSD-Amodal |
|---|---|---|---|
| F@.75 visible (Dice > 0.75) | 0.751 | 0.763 | ≈ 0.79 |
| F@.75 amodal | 0.734 | 0.745 | ≈ 0.84 |
| per-image Overlap-F visible | 0.783 | — | ≈ 0.85 |
| per-image Overlap-F amodal | 0.768 | — | ≈ 0.85 |

We sit slightly below UOAIS-Net's OSD numbers. Our scenes are *denser* than the
OSD tabletop set (≤49 bottles in a 70×45 cm tray), so the gap is mostly density,
not difficulty. Note: the old "F1 0.89" under the old protocol made the synth
look *too easy*; under the proper protocol it's slightly *harder* than OSD —
the opposite conclusion, and the right one.

**Occlusion-stratified recall:**

| occ bin | n_gt | recall | mean vis-IoU on matched |
|---|---|---|---|
| 0–10% | 564 | 0.961 | 0.896 |
| 10–30% | 202 | 0.881 | 0.870 |
| 30–50% | 104 | 0.692 | 0.798 |
| 50–80% | 117 | 0.179 | 0.786 |
| 80–100% | 66 | 0.000 | — |
| **all** | 1053 | 0.772 | 0.879 |
| truncated (border-touching) | 19 | 0.684 | — |

- **Recall on pickable bottles (≤30% occluded): 0.94** (720/766). On ≤30%-occluded, untruncated, ≥100px bottles: 0.946.
- The 30–50% band (0.69) is the cliff edge — still genuinely pickable, but where detection starts to fail.
- The ≥80% band (0.00) is effectively unobservable; no published UOIS method detects those either. The "all" row is dominated by this population — do not read it as detection ability on pickable objects.

**Per-class recall (≥100px):** blue_cap_pill_bottle 0.500, white_pill_bottle 0.569; the other five classes 0.83–0.92. The two low ones are the plainest white HDPE bottles — see the white-on-white finding below.

---

## The one nameable clean failure: white-on-white separation

Of the 23 false negatives at ≤10% occlusion (the "should have detected" ones):
- ~2 are degenerate slivers (<500 px visible — drop with the small-mask filter)
- ~6 are frame-truncated (cut off by the image border — not occluded by other bottles, but the frame chops them; UOAIS handles truncated objects poorly)
- **~15 are plain white bottles surrounded by other plain white bottles** — low RGB contrast at the boundary, UOAIS can't tell where one ends and the next begins. Some of these are full-size (13k–16k visible px), clearly visible to a human.

So recall on truly-clean, untruncated, normal-contrast bottles is ~99%. The white-on-white misses are a real, characterizable gap. They concentrate on blue_cap_pill_bottle (8 of 23) and white_pill_bottle/pill_jar (8 of 23) — the featureless white classes. The colorful syrups (kolmin, levozin) appear only via frame-truncation, not low-contrast. Crops: `screenshot/clean_fn_crops/`.

This is a domain mismatch: UOAIS-Net was trained on OSD/OCID (colorful, varied household objects), and Korean pharma bottles are mostly white plastic.

---

## QuBER: off the table

QuBER ([arXiv:2306.16132](https://arxiv.org/abs/2306.16132)) refines the *quality* of masks a model already produced. The white-on-white gap is *under-detection / merging* — there's no mask to refine when UOAIS merges two white bottles or misses one. With precision already at 0.92, QuBER's only upside is nudging mean IoU (already 0.88) and trimming the ~8% FPs. Marginal. Not the next step.

---

## Depth-channel diagnostic — done 2026-05-12. Result: it's the model, not the preprocessing.

We hypothesised that `normalize_depth`'s fixed clip range (250–1500 mm at our
1.286 m mount) was squeezing the inter-bottle depth gradient away before
UOAIS-Net's depth branch could use it. **The diagnostic falsified that.**

Method (`pharma-bin/pharma-bin-picking/tools/depth_diagnostic_white_on_white.py`):
for each white-on-white miss, measure the height step between the missed bottle
and its confused neighbour in raw mm, then track it through the actual pipeline
path (`normalize_depth` → nearest-resize to 640×480 → `inpaint_depth` TELEA),
and compare against one colourful bottle UOAIS *got right* as a yardstick.

| case | raw step | survives to model input | UOAIS found it? |
|---|---|---|---|
| missed pill_jar (scene 16) | 63 mm | ~11 levels | ❌ |
| missed pill_jar (scene 12) | 48 mm | ~8 levels | ❌ |
| missed medicine_bottle_b (scene 25) | 42 mm | ~4 levels | ❌ |
| **colourful syrup UOAIS got right (scene 1)** | 44 mm | ~8 levels | ✅ |

The missed white bottles carry a depth step **as strong as or stronger than**
the one UOAIS successfully used on the colourful bottle, and it survives the
pipeline cleanly — the figures (`screenshot/depth_diag/scene_*_raw_vs_modelinput.png`)
show the missed bottle as a distinct foreground depth blob in the image UOAIS
actually receives, *more* prominent after the squish+inpaint than in raw. So:

- **Not a preprocessing bug.** No range/resize/inpaint tweak will help — the signal is already there.
- **Outcome (b): model-side limitation.** UOAIS-Net's RGB-D concat fusion is RGB-dominated; it ignores a clean depth boundary when the colours are too similar. We can't retrain it (synth is benchmark, not training data).
- **Operating point:** ~94% recall on pickable bottles is the accepted level. The robot picks what UOAIS finds; the pile shifts after each pick; missed white-on-white bottles surface next cycle. Not blocking for the pick-and-place goal.

**Open question (verify before any paper):** UOAIS's *training* depth normalization range. The config in this repo (`configs/R50_*.yaml`) says `DEPTH_RANGE: [2500, 15000]` mm, and `adet/data/dataset_mapper.py` applies it — but that range would clip tabletop training data to black, so either the config changed after the checkpoint was trained or the training depth is in non-mm units. Don't assert a training-range OOD claim until this is checked against the actual training dataloader. What *is* verified: our ~1.1 m scenes cluster in the top ~15% of the *inference* normalization band [250, 1500] mm — a mild "values bunched near the top" note, not a strong OOD claim.

## Next step: a second algorithm (also a hypothesis test of the finding)

Run a depth-first UOIS model — UOIS-Net-3D (Xie et al., T-RO 2021) or MSMFormer
(Lu et al. 2023), or SAM-for-UOIS ([arXiv:2409.15481](https://arxiv.org/abs/2409.15481))
— on the same 30-scene v1.1 batch with the same eval script (pretrained, no
training, days of work). This is not a random second algorithm:

- It tests the white-on-white finding directly. If a depth-first model catches those cases → clean confirmation that UOAIS-Net's concat fusion underweights depth, and the paper gets a *comparative* result instead of a single-model observation. If it doesn't → the case is harder than the depth-blob picture suggests — also informative.
- It's load-bearing for the benchmark's headline claim. "Algorithm-agnostic validation benchmark" needs ≥2 algorithms run through it (Layer-3 in `project_synth_evaluation_framework.md`); a second algorithm is part of making the benchmark a credible contribution, not extra polish.

Sequence: this comes after the (already-shipped) eval rewrite and the L515 v2 noise model, and before the publication write-up. Stage 5 stays a capped "rebuild needed" Step-0 item — not on the critical path.

---

## Caveat: occlusion distribution = config knob

The occlusion histogram above reflects the v1.1 drop config (≤49 bottles in a
70×45 cm tray — dense). If deployment bins are sparser, re-weight the per-bin
recall to your density. This is a config knob (`drop:` section in `config.yaml`),
not a GT bug; the synth stays frozen at v1.1 unless real bin-density numbers say
otherwise. Frame-truncation should become a `truncated: bool` field in
`scene_gt.json` on the next synth touch (a concrete consumer need from this eval).
