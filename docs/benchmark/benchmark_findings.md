# Benchmark findings — canonical results reference

**Date:** 2026-05-17
**Status:** CANONICAL. Benchmark track CLOSED (audited & passed 2026-05-17). This is the single source of truth for *what the benchmark found*; [eval/eval_methodology.md](../eval/eval_methodology.md) is the source of truth for *how it was scored*.
**Provenance:** consolidated by the reviewer from independently audited results. Every number here was either reviewer-recomputed from disk or is a locked operating baseline. Audit trail: `pharma-bin/reviewer-feedback/centroid_step/`.

---

## 0. One-paragraph summary

A synthetic, algorithm-agnostic benchmark (30 dense top-down pharma-bin scenes, physical suction-point ground truth) was used to (a) characterise the UOAIS segmentation→grasp-point pipeline and (b) compare it against a second pretrained algorithm. Three findings: the UOAIS pipeline operates at **F1 0.845 / 0.94 recall on pickable bottles** with a *model-side* (not preprocessing) white-on-white miss; the trivial **mask-centroid grasp point is statistically near-optimal** for selection (validated, not assumed); and the released SOTA alternative (**single-stage MSMFormer, UOAIS-Sim**) **collapses to F1 0.000** on this domain where UOAIS — same training set — succeeds. The benchmark is a publication-grade validation artifact, not evidence of a working robot.

---

## 1. What the benchmark is

- **Form:** synthetic, BlenderProc-generated. 30-scene v1.1 batch, ~30 pharma bottles/scene (~1050 GT instances), dense, top-down, packed-bin geometry. Camera mount 1.286 m; depth in L515-noise-modelled units (0.00025 m).
- **Ground truth:** per-instance amodal + visible masks, 6-DOF pose, and **physical suction-point GT** (`suction_gt.py` V1.5 — dense plane-fit, analytic Sseal × Swrench, top-K NMS). Physical truth, not algorithm-convenient.
- **Purpose:** a *stable, algorithm-agnostic validation benchmark* — exists because no real L515 capture is available in dev. Not training data. See [project_dataset_purpose.md](../../../../.claude/projects/-home-kosal-cbnu-project-AI-picking-arm-robot/memory/project_dataset_purpose.md).
- **Status:** synth generator TERMINAL at v1.1 (reactive-only). Benchmark evaluation track CLOSED 2026-05-17.

## 2. Honest-eval methodology (summary; full detail in eval_methodology.md)

One Hungarian match over *all* GT, three views derived from it: detection headline (≥100 px), occlusion-stratified, F@.75 via **Dice** (not IoU). In-eval frame-truncation detection. The 2026-05-12 protocol change moved the headline F1 0.832 → 0.845 **with neither the model nor the GT changing** (protocol change, stated as such). Legacy 0.832 reproduces as a regression check.

## 3. Findings

### 3.1 Segmentation operating point (UOAIS)

| Metric | Value | Notes |
|---|---|---|
| Headline F1 | **0.845** | RGBD-concat, τ=0.70 (operator-resolved; depth-only @ τ=0.35 is an ablation) |
| Precision | **0.917** | high-precision operating point (ten Pas 2017 rationale: FP costs a pick cycle) |
| Recall | **0.784** | aggregate |
| Recall on pickable (≤30 % occ) | **0.94** | the deployment-relevant number; accepted operating point |
| Legacy regression check | 0.832 | reproduces; protocol-change provenance documented |

**White-on-white miss is model-side, not preprocessing.** UOAIS misses plain white bottles adjacent to white bottles; the depth step survives the whole pipeline clean (figures in `screenshot/depth_diag/`). The model does not exploit it (RGBD-concat fusion is RGB-dominated). ~0.94 recall on pickable is the accepted operating point — not a preprocessing bug to chase. See [project_white_on_white_depth_finding.md](../../../../.claude/projects/-home-kosal-cbnu-project-AI-picking-arm-robot/memory/project_white_on_white_depth_finding.md).

### 3.2 Centroid grasp-point is near-optimal for *selection*

Scored with the same simplified Dex-Net-3.0 / SuctionNet-inspired analytic model that generates the GT (`suction_gt.py`), on **noisy depth**, at the predicted mask centroid (`compute_centroid_adaptive`, 2026-02-24 moments-primary fix).

| Quantity | Predicted-mask (headline) | GT-mask (ablation) |
|---|---|---|
| S_combined median | 0.336 (P25 0.212 / P75 0.428) | 0.280 |
| Per-instance Δ to instance-best (median) | **−0.002** | +0.044 |
| Within-instance GT S_combined spread (median) | 0.321 | 0.321 |
| Random-pixel baseline | 0.000 | 0.000 |
| GT top-1 oracle | 0.343 | 0.343 |
| Secondary: within 5 mm of GT top-5 / top-10 | 0.484 / 0.569 | — |

**Claim (evidence-backed, validated):** the geometric centroid sits essentially *at* the per-instance analytic best (Δ ≈ 0) within a distribution that is itself ~0.32 wide, while a random visible pixel scores ~0. The wide within-instance spread refutes metric-saturation — this is genuine, and validates Jiang et al. 2022's `J_c` distance-to-graspable-centroid prior.

**Framing precision (binding):** "near-optimal" applies to the **predicted-mask headline** (Δ −0.002) only. The GT-mask ablation (Δ +0.044) is **"well-placed / upper-quintile"**, not "near-optimal". Eval output enforces this conditionally on |Δ median|.

**Stated limitation (do not overreach):** this does **not** show "a learned score-map / Stage-5 adds little." The analytic metric's top end is compressed (GT-best median ~0.33; only ~12 % of instances have any point ≥0.5; μ-flat across {0.2…1.2}). The small oracle−centroid gap is small because the *ceiling* is low, not because the centroid is physically optimal. Assessing Stage-5 / learned-score value needs a better-scaled metric or real-robot trials. (This corrects an earlier overreach caught in audit.)

### 3.3 Comparative — UOAIS vs single-stage MSMFormer (Layer-3)

Second algorithm: **MSMFormer** (Lu et al. 2023), UOAIS-Sim-trained, OCID checkpoint, same 30-scene benchmark, same eval, re-projected to shared (stretch-640×480) geometry via disclosed inverse-letterbox.

| | precision | recall | F1 | per-scene |
|---|---|---|---|---|
| UOAIS (same benchmark) | 0.917 | 0.784 | **0.845** | ~27 |
| MSMFormer (single-stage, all τ) | 0.000 | 0.000 | **0.000** | 2.1 |

TP **0** / FP **63** / FN **1037** (reviewer-recomputed: 30 scenes, 63 detections, mean 2.10, range [0,4]; predictions are degenerate — max mask = the entire 307 200 px frame, ~35 % of predictions >90 % of frame, 13 sub-500 px specks). **Best pred–GT IoU over the whole benchmark = 0.35** (sub-threshold; an earlier "0.029" figure was a team-lead integrity-check error, corrected in audit).

**Binding framing (verbatim — do not weaken or strengthen):**
> The released UOAIS-Sim MSMFormer checkpoints — available **only** in single-stage form (no crop/refine stage) — severely under-detect on dense top-down pharma bins (F1 0.000, 0 TP at IoU≥0.5, degenerate whole-frame masks, best pred–GT IoU 0.35 below threshold), where Mask R-CNN UOAIS, the **same training set**, succeeds (F1 0.845). **Cause not isolated**: consistent with the absent 2-stage crop/refine pipeline, a domain gap, or mean-shift defaults; the released artifact does not permit disentangling them.

Not "MSMFormer fails." Not "single-stage is why." Not "fails to segment at all." The 2-stage form is **not testable** (no crop checkpoint in the UOAIS-Sim release).

## 4. Stated limitations (honest, for the paper)

1. **Synthetic-only.** No real L515 validation — by necessity (no hardware/captures in dev). Predictive validity (Layer-3-of-the-eval-framework) intentionally not pursued.
2. **Analytic-metric top-end saturation.** Section 3.2 — bounds what the centroid result can claim.
3. **Comparative is a negative result**, partly because the released SOTA model only ships in a crippled single-stage form. Honest, publishable, but not "we beat SOTA".
4. **Centroid "near-optimal" is in score space, not position space** (5 mm secondary: only ~0.48/0.57 within 5 mm of GT top-K) — the centroid hits *a* flat region, not necessarily *the* GT point.

## 5. What this does NOT establish

- No working robot; no real-world pick; no live camera/arm loop.
- Stage-5 (3D flatness) value is **not** bounded by this work.
- "Single-stage causes the MSMFormer collapse" is a hypothesis, not a demonstrated mechanism.

## 6. Reproducibility / provenance

- Eval: `pharma-bin-picking-synth-dataset/scripts/eval/eval_uoais_on_synth.py`, `eval_centroid_on_synth.py`; GT: `suction_gt.py` V1.5 (read-only).
- MSMFormer ckpt SHA256 (OCID RGBD) `60f4df8b…`; UCN backbone `33c5467f…` — full trace in the audit trail.
- Audit trail (every gate, every caught error): `pharma-bin/reviewer-feedback/centroid_step/` — `centroid_plan_2026-05-13.md`, `implementation_audit_4b/4c_2026-05-16.md`, `second_algorithm_selection_review_2026-05-16.md`, `msmformer_failure_audit_2026-05-16.md`.
- **Process value:** four wrong conclusions were caught before reaching the paper — over-segmentation merge, depth-preprocessing hypothesis, "Stage-5 adds little" overreach, "fails at all"/0.029 overclaim. The gated symptom-check cadence is itself a methodological contribution.

## Citations (corrected, literature-verified)

- Mahler et al. 2018, Dex-Net 3.0, arXiv:1709.06670 — analytic suction model lineage.
- Cao et al. 2021, SuctionNet-1Billion, arXiv:2103.12311 — Sseal × Swrench scoring (we use a simplified form; stated as such).
- **Jiang** et al. 2022, "Learning Suction Graspability…", Frontiers Neurorobotics, doi:10.3389/fnbot.2022.806898 — `J_c` distance-to-graspable-centroid prior (NOT "Tsuji"; first-author miscitation corrected in audit).
- ten Pas et al. 2017, arXiv:1706.09911 — high-precision operating-point rationale.
- Lu et al. 2023, MSMFormer (RSS) — the second algorithm.
