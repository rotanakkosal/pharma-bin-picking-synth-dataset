# sample_data/ Inventory and Naming Convention

**Date:** 2026-05-04
**Status:** Proposal, awaiting decision before migration

---

## Why this doc exists

`sample_data/` has accumulated meshes, textures, and source photos across four delivery batches with inconsistent naming. The current layout has three concrete problems:

1. **Workarounds in code.** `stage_textured_mesh()` exists primarily to ASCII-stage Korean OBJ paths because Blender's importer chokes on them. The function also has a fallback for the `L/` folder's broken `mtllib` reference. Both are workarounds for sloppy upstream data, not solutions.

2. **No canonical version.** `콜민A시럽` exists in 4 places: `Medicine box OBJ_260324/2026-04-22/`, `Phama Bottle 3D Object/2026-04-29/` (twice — plain + UV-mapped), and `2026-05-03/medicine bottle 1/`. Nothing in the filesystem says which is "the one." That information lives only in `scripts/config.yaml`.

3. **Hard to add new objects.** Each new bottle requires deciding which folder it belongs in, what to call it, whether to rename Korean filenames, etc. The decisions are reinvented per-object instead of following a rule.

---

## Current inventory (what's actually on disk)

```
sample_data/
├── Medicine box OBJ_260324/
│   └── 2026-04-22/                      ← config's active dir
│       ├── 콜민A시럽.obj                  ← plain (used as procedural-fallback geometry)
│       ├── 레보진시럽.obj                 ← plain (procedural-fallback)
│       ├── 파란뚜껑 약통.obj              ← plain (Korean + space in name)
│       ├── 하얀 약통.obj                  ← plain (Korean + space)
│       ├── bottle_medicine2.obj          ← plain ASCII
│       ├── bottle_medicine3.obj          ← plain ASCII
│       └── bottle_pill.obj               ← plain ASCII
│
├── Phama Bottle 3D Object/
│   └── 2026-04-29/                      ← photoreal UV-mapped + textures
│       ├── 콜민A시럽.obj                  ← duplicate plain
│       ├── 레보진시럽.obj                 ← duplicate plain
│       ├── 파란뚜껑 약통.obj              ← duplicate plain
│       ├── 하얀 약통.obj                  ← duplicate plain
│       ├── A/                            ← old version of 콜민A photoreal
│       │   ├── A_UV_mapped.obj
│       │   ├── A_texture.png
│       │   └── ...
│       ├── L/                            ← 레보진 photoreal (mtllib broken)
│       │   ├── L_UV_mapped.obj
│       │   ├── L_texture.png
│       │   └── ...
│       ├── ver7/                         ← newer version of 콜민A photoreal (active)
│       │   ├── 콜민A시럽_UV_mapped.obj
│       │   ├── 콜민A_texture.png
│       │   └── ...
│       └── Label/                        ← original label photos (KakaoTalk_*)
│
├── 2026-05-03/                          ← vendor delivery, raw filenames
│   ├── medicine bottle 1/
│   │   └── uploads_files_4527270_pill.obj
│   └── medicine bottle 2/
│       └── uploads_files_4176934_medicine+bottle+obj.obj
│
└── 2026-05-03_cleaned/                  ← cleaned vendor delivery
    ├── bottle_medicine2.obj
    ├── bottle_medicine3.obj
    └── bottle_pill.obj
```

**Total active meshes:** 7. Total mesh files on disk: ~14. Most of the duplication is silent and only documented in `config.yaml`.

---

## Proposed convention

### One folder per object, ASCII-only IDs

```
sample_data/
└── bottles/
    ├── kolmin_a_syrup/                  ← formerly 콜민A시럽
    │   ├── mesh.obj                     ← plain geometry (used by procedural path)
    │   ├── mesh_uv.obj                  ← UV-mapped (used by textured-override path)
    │   ├── mesh_uv.mtl
    │   ├── label.png                    ← UV-unwrapped label texture
    │   ├── label_source.jpg             ← original capture-team photo
    │   └── README.md                    ← provenance, dimensions, notes
    │
    ├── levozin_syrup/                   ← formerly 레보진시럽
    │   ├── mesh.obj
    │   ├── mesh_uv.obj
    │   ├── mesh_uv.mtl
    │   ├── label.png
    │   ├── label_source.png
    │   └── README.md
    │
    ├── blue_cap_pill_bottle/            ← formerly 파란뚜껑 약통
    │   ├── mesh.obj
    │   └── README.md
    │
    ├── white_pill_bottle/               ← formerly 하얀 약통
    │   ├── mesh.obj
    │   └── README.md
    │
    ├── medicine_bottle_a/               ← formerly bottle_medicine2
    │   ├── mesh.obj
    │   └── README.md
    │
    ├── medicine_bottle_b/               ← formerly bottle_medicine3
    │   ├── mesh.obj
    │   └── README.md
    │
    └── pill_jar/                        ← formerly bottle_pill
        ├── mesh.obj
        └── README.md
```

### Rules

1. **Folder name = canonical object ID.** ASCII, lowercase, snake_case. This is what code references everywhere.
2. **Korean class name preserved as metadata.** Each `README.md` includes `display_name_kr: 콜민A시럽` — for printing in user-facing reports and matching capture-team labels.
3. **Plain mesh = `mesh.obj`. UV-mapped mesh = `mesh_uv.obj`.** No version suffixes (ver7, A, L). If we need to keep history, use git, not filenames.
4. **One label texture per object: `label.png`.** Original photo source: `label_source.{jpg,png}`. Don't keep multiple cropped variants in the active dir.
5. **No spaces, no Korean glyphs, no vendor names.** All paths ASCII. ASCII-staging in `stage_textured_mesh()` becomes obsolete after migration.
6. **Each object has a README.md.** Provenance (capture date, version), Korean display name, height/diameter, notes.

### Per-object README template

```markdown
# kolmin_a_syrup

- Display name (KR): 콜민A시럽
- Display name (EN): Coldmin A Syrup
- Source: capture team batch 2026-04-29 (`Phama Bottle 3D Object/2026-04-29/ver7/`)
- Mesh: 1.2 MB, 8200 verts, watertight
- Approx. dimensions: 50mm × 50mm × 110mm (W × D × H)
- Has photoreal UV-mapped variant: yes (mesh_uv.obj + label.png)
- Notes: Contains the brand panel + Korean dosage info on the label.
```

### Config.yaml after migration

```yaml
meshes:
  base_dir: "sample_data/bottles"
  copies_per_mesh: 7
  bottles:
    - id: kolmin_a_syrup
      uv_mapped: true
    - id: levozin_syrup
      uv_mapped: true
    - id: blue_cap_pill_bottle
    - id: white_pill_bottle
    - id: medicine_bottle_a
    - id: medicine_bottle_b
    - id: pill_jar
```

Render code reads `sample_data/bottles/<id>/mesh.obj` (or `mesh_uv.obj` if `uv_mapped: true`) and `label.png`. Korean display names come from the per-object README at print time.

---

## Migration plan

Three options, ordered by safety:

### Option A — additive only (recommended first)
1. Create new `sample_data/bottles/` tree as proposed.
2. **Symlink** to existing files; don't delete originals yet.
3. Add a new `meshes.base_dir` config entry; switch render code to read the new layout.
4. Render once with new config to verify nothing broke.
5. Once confirmed working, **then** delete the old directories.

**Pros:** zero risk; rollback is just changing a config key.
**Cons:** disk has duplicates briefly.

### Option B — physical move
1. Create `sample_data/bottles/` with renamed copies (or git-mv).
2. Update config.yaml.
3. Delete old directories in same commit.

**Pros:** clean cut.
**Cons:** any external script pointing at old paths breaks immediately.

### Option C — leave as-is, document only
Document the existing layout, don't migrate.

**Pros:** zero work.
**Cons:** doesn't fix any of the three problems this doc opened with.

---

## Open questions before migrating

1. **Which version of 콜민A is canonical?** `A/A_UV_mapped.obj` (older) or `ver7/콜민A시럽_UV_mapped.obj` (newer, currently in config)? — A: `ver7`. Discard `A/`.
2. **Which `bottle_pill` is canonical?** `Medicine box OBJ_260324/2026-04-22/bottle_pill.obj` or `2026-05-03_cleaned/bottle_pill.obj`? — Need to compare. The `_cleaned` version sounds canonical; verify it was meant as a replacement.
3. **`2026-05-03/` raw vendor files (`uploads_files_4527270_pill.obj`)** — keep as archival source, or discard? Recommendation: archive into `sample_data/_archive/` if needed for traceability, otherwise delete.
4. **Display-name source of truth.** Should `README.md` in each folder be the canonical name registry, or should we also have a `sample_data/bottles/index.yaml`? Recommendation: the README is human-readable provenance; `index.yaml` is the machine-readable list and should be the source of truth for code.

---

## Why doing this matters now

The class-imbalance bug we found (scenes 700–704 vs scene_999 having different mesh sets) was partly caused by config drift across renders. A clean, ID-stable layout makes config-drift bugs detectable: if `id: pill_jar` is in the config but the folder doesn't exist, render fails immediately instead of silently rendering a different mesh set.

This is also a prerequisite for talking to the capture team about new meshes. Right now if they deliver a new bottle, we have no rule for where it goes or what to name it. With this convention, the answer is "create `sample_data/bottles/<descriptive_name>/` with the standard files inside, add an entry to `index.yaml`, and you're done."

---

## Next concrete action

Before any rendering work, decide on Option A/B/C above. If A or B, do the migration in a dedicated commit before continuing with Priority 1 (class-imbalance re-render) or Priority 2 (depth noise) from `synth_realism_improvement_plan.md`.
