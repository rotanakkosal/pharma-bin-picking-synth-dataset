# Team Workflow — pharma-bin-picking-synth-dataset

**Status:** Working agreement, effective 2026-05-11
**Project state:** 🔒 **Synth marked terminal at v1.1-realistic-labels (2026-05-12).** No proactive work. Reactive-only — only act on this codebase if a downstream consumer (UOAIS, robot, etc.) reports a concrete failure that traces back here.
**Owner:** Synth dataset team lead

Short rules so we stop wasting render compute on work that doesn't advance the plan.

---

## The plan is the source of truth

`docs/synth_realism_improvement_plan.md` lists priorities P0–P4 and the current next concrete action. **Anything not in that plan is out of scope until the team lead approves it and updates the doc.**

If you think a new item should be added, propose it — don't just start building it.

---

## Before you start any render, answer three questions

1. **Which P-item does this advance?** Name it (e.g. "P1 follow-up batch", "P2 noise recalibration").
2. **What decision does the result drive?** If the answer is "none — we already know what we'll do regardless," **don't run the render.** That's theater, not work.
3. **What's it going to cost?** Number of scenes × ~95 s/scene on A6000. Anything over 5 minutes of compute needs explicit team-lead sign-off.

If you can't answer all three in one sentence each, surface it to the team lead first.

---

## Locked baselines — do not vary without explicit approval

These are calibrated to real hardware. Changing them breaks Layer 3 (predictive validity), which is the entire point of the benchmark.

| Knob | Locked value | Why locked |
|---|---|---|
| `camera.height_m` | `1.286` | Matches real Intel L515 mounting (`t_z ≈ 1286 mm` from sample_data/camera.json) |
| `camera.fx/fy/cx/cy/width/height` | L515 intrinsics from `sample_data/camera.json` | Same — sim-to-real correlation requires identical intrinsics |
| `render.depth_unit_mm` | `true` (or L515 native bins per v2-l515) | Consumers expect L515-format depth |

Sweeping any locked value to "explore" is wasted compute. The decision is already made by the real-hardware constraint.

---

## Out-of-plan work

If you spot something interesting (e.g. "what if we tried HDRI?", "what about a different bottle scale?"):

1. **Don't render yet.** Write a one-paragraph proposal in `docs/proposals/` (path: `docs/proposals/<topic>.md`).
2. Include: motivation, what P-item it would feed or supersede, expected compute cost, decision it would drive.
3. Tag the team lead. Approval gets logged in `synth_realism_improvement_plan.md` status snapshot before any render starts.

This isn't bureaucracy — it's a 5-minute write-up that prevents 40-minute compute waste.

---

## Render execution rules

- **Long-running renders (>30s) get handed to the human operator with a copy-paste command**, not background-launched by an agent. (Memory rule — see `feedback_render_commands.md`.)
- Renders that fail mid-batch: capture the log path, don't silently retry. Investigate root cause.
- Don't `rm -rf output/` without confirming with team lead — there may be reference batches needed for before/after comparisons.

---

## Docs structure — where things live

The `docs/` tree is organized by topic. **Add new docs into the right subdirectory; do not dump new files at the docs/ root.**

```
docs/
├── README.md                              # index of every doc — update when adding a new one
├── synth_realism_improvement_plan.md      # canonical project history (root, TERMINAL marker)
├── suction_gt/                            # suction-point GT topic
├── depth_noise/                           # L515 depth noise topic
├── pose_export/                           # 6-DOF pose export topic
├── sample_data/                           # mesh / sample data layout topic
└── team/                                  # internal process (this file lives here)
```

**Filename rule: self-describing.** Filenames must be readable on their own, without relying on the parent directory for context. Use the form `<topic>_<role>.md` — e.g. `suction_gt_v1_implementation.md`, not just `v1_impl.md`. If you see the filename in a search result with no path, you should still know what it is.

**Status block rule.** Every doc has a `Date:` and `Status:` block near the top. Status is one of: `Proposal`, `Approved for implementation`, `Shipped`, `Superseded`, or a terminal-state marker. Update it when state changes.

**Cross-references.** Use relative paths between docs (e.g. `../suction_gt/suction_gt_design.md`). When you rename or move a doc, grep the repo for the old name and fix every reference — scripts and other docs both.

**Adding a new doc:**
1. Pick the right subdirectory (or create one if it's a brand-new topic).
2. Use the `<topic>_<role>.md` filename pattern.
3. Add a `Date:` + `Status:` block at the top.
4. Add an entry in `docs/README.md` under the matching section.
5. If any code references the doc path, update those references too.

---

## Status hygiene after every change

When you finish a piece of work — *before* you say "done":

1. Update the status snapshot in `synth_realism_improvement_plan.md` (move the row to ✅ shipped, or flag the new state).
2. If the change touched scripts, verify docstring usage examples still match the file's actual path.
3. If the change touched config, note the version bump in `README.md`'s version table.
4. Run the relevant verification command (e.g. `python scripts/eval/dataset_qc.py`) and paste results into the commit message or hand-off.

Saying "done" without these three steps means the team lead has to chase the state — that's how the project drifts.

---

## What the team lead owes the team

- A start-of-session check: "what's in flight, what's planned, any out-of-plan work in progress?"
- A pre-render gate: before any multi-scene render runs, the three questions above get answered explicitly.
- An honest plan doc: status snapshot reflects reality, not aspirations. If P-items are deferred or descoped, that's recorded the day it happens, not weeks later.
- No silent scope expansion. If a team member proposes new work, it gets approved-and-doc'd or deferred — not both half-done.
