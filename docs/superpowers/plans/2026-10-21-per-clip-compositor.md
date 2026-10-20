# Per-clip Video Compositor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent non-destructive editor to every clip so its source range, main-video crop/layout, video banner, and advertising-video overlay can be adjusted before a full-quality export from the original source.

**Architecture:** Store one normalized `composition` object inside each segment and use the same pure geometry rules for browser preview and backend validation. A dedicated React editor changes the segment without encoding; FastAPI validates the payload and `VideoProcessor` builds a single FFmpeg composition graph from the original video, with legacy export retained for segments without composition.

**Tech Stack:** React 19, browser video/CSS transforms, JavaScript pure-state helpers, FastAPI, Pydantic, Python 3.12, FFmpeg, Node built-in tests, Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-19-per-clip-compositor-design.md`

## Global Constraints

- Keep all runtimes, dependencies, caches, temporary files, and media under `E:\ArkaimVPNproject`; do not write project data to drive `C:`.
- Preserve the source upload and render every edited clip non-destructively from that source.
- Store rectangles as normalized finite values and source-range times as absolute source seconds.
- Store banner/ad display times relative to the edited clip.
- Preserve legacy behavior for every segment without `composition`.
- A per-clip composition takes precedence over legacy global banner sizing for that clip.
- Do not stage or overwrite unrelated dirty-worktree changes.
- Add automated regression coverage, but do not execute tests, frontend builds, or media renders unless the operator explicitly changes the current instruction; use only static syntax/conflict/diff checks during implementation.

## Review Focus

- A segment extended to exactly source time `0` or source duration must render without an out-of-range seek.
- NaN, infinity, crossed time handles, and rectangles outside `0..1` must be rejected with a Russian 422 response.
- Missing or replaced banner/ad files must not silently render stale media; the affected layer must be disabled in preview and rejected before export.
- Two clips from one source must retain completely independent composition objects after autosave, hydration, reorder, and deletion.
- Overlay media with no audio, short duration, odd dimensions, or a different aspect ratio must loop visually without replacing the source audio or producing odd H.264 dimensions.

---

### Task 1: Define and normalize the per-clip composition model

**Files:**

- Create: `frontend/src/lib/clipComposition.js`
- Create: `frontend/src/lib/clipComposition.test.js`
- Modify: `backend/schemas.py`
- Create: `backend/tests/test_composition_schema.py`

**Interfaces:**

- Produces frontend `createDefaultComposition(segment, outputAspectRatio)`, `normalizeComposition(value, segment, sourceDuration)`, `updateCompositionLayer(composition, layerName, patch)`, and `compositionDuration(composition)`.
- Produces Pydantic `NormalizedRect`, `MainVideoLayer`, `TimedOverlayLayer`, `AdLayer`, `ClipComposition`, and `ExportSegment` models.
- `ExportRequest.segments` becomes `list[ExportSegment]`; backend callers convert models with `model_dump(exclude_none=True)` before passing them to processing functions.

- [ ] Add frontend test cases for default geometry, independent deep copies, finite normalized rectangle bounds, minimum `0.03` dimensions, source-range clamping, crossed-handle rejection, fit allow-list, and overlay timing relative to edited duration.

- [ ] Add backend schema tests for the same accepted and rejected payloads, including NaN/infinity and `insert` versus `overlay` ad modes.

- [ ] Implement the frontend pure helpers without React or browser dependencies.

- [ ] Implement the Pydantic models and a model-level validation pass for structural rules; source-duration-dependent validation remains in Task 5.

- [ ] Perform static checks only: Python compilation, merge-marker scan, and `git diff --check`. Record the operator test commands without executing them.

- [ ] Commit only Task 1 files with `feat: add clip composition model`.

### Task 2: Preserve composition through project and session persistence

**Files:**

- Modify: `frontend/src/lib/projectState.js`
- Modify: `frontend/src/lib/projectState.test.js`
- Modify: `frontend/src/lib/sessionStorage.js`
- Modify: `frontend/src/lib/sessionStorage.test.js`
- Modify: `backend/project_store.py`
- Modify: `backend/tests/test_project_store.py`

**Interfaces:**

- Consumes `normalizeComposition` from Task 1.
- Produces hydrated `segments[]` whose optional `composition` is normalized and deep-cloned.
- Keeps the existing editor payload shape; composition travels inside the already allow-listed `segments` array.

- [ ] Add tests proving two segment compositions survive session serialization and project API round-trip without shared nested references.

- [ ] Add compatibility cases proving missing or malformed old composition values fall back to no composition while the segment remains usable.

- [ ] Normalize compositions in `mapProjectToEditorState`; keep `buildProjectEditorPatch` unchanged except for verified deep-safe segment output.

- [ ] Tighten project-store segment sanitization only where necessary to retain JSON-compatible composition fields while excluding unknown secret/path fields.

- [ ] Perform static checks only and commit Task 2 files with `feat: persist clip compositions`.

### Task 3: Build the independent clip-composer interface

**Files:**

- Create: `frontend/src/components/ClipComposer.jsx`
- Create: `frontend/src/components/ClipComposer.test.js`
- Create: `frontend/src/components/CompositionCanvas.jsx`
- Modify: `frontend/src/components/SegmentEditor.jsx`
- Modify: `frontend/src/App.css`

**Interfaces:**

- `ClipComposer({ segment, sourceDuration, sourceUrl, outputAspectRatio, bannerMedia, adMedia, onPreview, onSave, onCancel })` returns the full updated segment to `onSave`.
- `CompositionCanvas({ sourceUrl, previewTime, composition, selectedLayer, bannerMedia, adMedia, onCompositionChange })` edits normalized geometry only.
- `SegmentEditor` keeps transcript-based timing/title editing but delegates the visual-composition section to `ClipComposer` rather than owning geometry.

- [ ] Add component/helper tests for opening from segment defaults, moving each layer, independent-axis resize, crop changes, fit-mode selection, Reset/Cancel/Save, and keyboard-accessible numeric alternatives to dragging.

- [ ] Implement a modal with three tools: main video, video banner, and video advertising. Show unavailable media as disabled with a clear message.

- [ ] Implement a dark output canvas, clipped media layers, selection outlines, pointer capture for drag/resize, and numeric `x/y/width/height` fields using normalized percentages.

- [ ] Add `contain`, `cover`, and `stretch` controls for overlays and `cover`/`contain` for the main layer.

- [ ] Keep preview-only outlines/handles outside exported media and add responsive styling for desktop and narrow screens.

- [ ] Perform static JSX/diff/conflict review only and commit Task 3 files with `feat: add per-clip composition editor`.

### Task 4: Add source-range and overlay timing controls

**Files:**

- Modify: `frontend/src/components/ClipComposer.jsx`
- Modify: `frontend/src/components/ClipComposer.test.js`
- Modify: `frontend/src/components/SegmentEditor.jsx`
- Modify: `frontend/src/App.css`

**Interfaces:**

- Consumes Task 1 composition helpers.
- Produces absolute `composition.source_start/source_end` and relative `banner_layer.start/end` plus `ad_layer.start/end`.

- [ ] Add tests for extending left to `0`, extending right to exact source duration, minimum `0.5` second clip length, continuously updated duration, and overlay ranges clamped after the source range is shortened.

- [ ] Add a source timeline with detected-range indicator and draggable left/right handles; retain the existing precise transcript-word selection as an alternate way to set the range.

- [ ] Add numeric start/end controls for banner and overlay-ad visibility plus a one-click `Весь клип` action.

- [ ] Ensure moving either source handle updates the segment's public `start/end` as well as composition source times so lists, preview seeks, and export agree.

- [ ] Perform static checks only and commit Task 4 files with `feat: edit clip and overlay timing`.

### Task 5: Validate composition against the actual source and expose media metadata

**Files:**

- Modify: `backend/main.py`
- Modify: `backend/schemas.py`
- Modify: `backend/tests/test_project_api.py`
- Modify: `frontend/src/components/AdManager.jsx`
- Modify: `frontend/src/App.jsx`

**Interfaces:**

- Add backend `validate_composition_for_source(segment: dict, source_duration: float) -> dict` returning a normalized processing dictionary or raising HTTP 422 with a Russian detail.
- `AdManager` status callbacks provide `{ has_media, url, duration, width, height, filename }`, not only a boolean.
- `App` passes current banner/ad metadata and the original source URL into `ClipComposer`.

- [ ] Add API tests for exact source boundaries, out-of-source ranges, invalid overlay end times, missing banner/ad media, and valid legacy segments.

- [ ] Return width and height from `/api/ad/current` and `/api/banner/current`; preserve current fields for compatibility.

- [ ] Convert `ExportSegment` models to dictionaries, read source metadata once, and validate every composition before any output file is written.

- [ ] Update `AdManager` callbacks and App state so the editor previews the same uploaded files that backend export resolves.

- [ ] Perform Python compilation and static frontend/diff/conflict checks only; commit Task 5 with `feat: validate clip composition exports`.

### Task 6: Build the single-pass FFmpeg compositor

**Files:**

- Create: `backend/clip_compositor.py`
- Create: `backend/tests/test_clip_compositor.py`
- Modify: `backend/video_processor.py`
- Modify: `backend/tests/test_media_regressions.py`

**Interfaces:**

- `build_composition_filter(composition: dict, source_width: int, source_height: int, output_width: int, output_height: int, banner_input: int | None, ad_input: int | None, post_filters: list[str]) -> CompositionFilter`.
- `CompositionFilter` contains `filter_complex: str`, `video_map: str`, and the required ordered overlay inputs.
- `VideoProcessor.clip_video(..., composition: dict | None = None, overlay_ad_path: str = "")` selects the compositor only when composition is present.

- [ ] Add pure filter tests for main crop/destination placement and `contain`, `cover`, and `stretch` overlay graphs, including even pixel rounding and timed `enable=between(t,...)` expressions.

- [ ] Add cases for no overlay, banner only, overlay ad only, both overlays, missing audio, short looping overlay media, and post-composition subtitle/text filters.

- [ ] Implement crop/destination conversion helpers that keep every generated dimension even and at least two pixels.

- [ ] Build the main canvas and overlays in one `filter_complex`; strip overlay audio and retain only the original clip audio in overlay mode.

- [ ] Apply subtitles and text advertising after the visual stack, encode vertical compositions at 1080×1920 with Lanczos and CRF 18, and leave legacy paths unchanged.

- [ ] Keep midpoint ad insertion as a distinct existing path when ad mode is `insert`; never apply both modes for one clip.

- [ ] Perform Python compilation, filter-string inspection, merge-marker scan, and diff check only; do not invoke FFmpeg. Commit Task 6 with `feat: render per-clip compositions`.

### Task 7: Integrate independent compositions into every export path

**Files:**

- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/HighlightList.jsx`
- Modify: `frontend/src/components/ExportPanel.jsx`
- Modify: `backend/main.py`
- Modify: `backend/video_processor.py`
- Modify: `backend/tests/test_project_api.py`
- Modify: `backend/tests/test_media_regressions.py`

**Interfaces:**

- Frontend export segments include `{ start, end, label, composition }` when composition exists.
- `_export_segments(...)` forwards each segment's composition independently to `clip_video` and uses the composition's ad mode when deciding whether to call midpoint insertion.
- Concatenated export renders each independently composed temporary clip before concatenation.

- [ ] Add tests proving two clips from the same source receive different ranges/layouts and that an edited concatenated export preserves each layout in order.

- [ ] Replace every App export payload that currently strips segments to three fields with a shared `serializeExportSegment(segment)` helper.

- [ ] Add `Редактировать`/`Редактировать снова` actions consistently before and after rendering, without changing selection or download behavior.

- [ ] Forward per-segment composition through separate, single-clip, and concatenated export paths; legacy segments continue through the current global settings.

- [ ] Prevent global `insertAd` and `videoBannerHeight` from overriding a per-clip composition while retaining them for unedited clips.

- [ ] Perform static checks only and commit Task 7 files with `feat: export independent clip layouts`.

### Task 8: Static integration review and operator test checklist

**Files:**

- Modify: `README.md`
- Review: every file changed in Tasks 1–7.
- Modify: only files required to resolve verified static findings.

**Interfaces:**

- Produces a documented operator workflow and exact test commands, without executing them under the current instruction.

- [ ] Review every acceptance criterion in the approved spec against a concrete implementation location and regression test.

- [ ] Run fresh static verification only: Python `py_compile`, `git diff --check`, unmerged-file check, conflict-marker scan, import/name search, and manual review of frontend/backend field-name parity.

- [ ] Document how to edit an individual clip, extend its timeline, place a banner/ad, choose overlay versus midpoint insertion, save, export, and reset to legacy behavior.

- [ ] List the operator-run commands for backend tests, frontend tests/build, and one visual FFmpeg smoke test, clearly marked as not executed by Codex.

- [ ] Inspect `git status` and ensure no runtime data, cache, model, temporary file, secret, or unrelated user file is staged.

- [ ] Commit documentation/static corrections with `docs: explain per-clip composition editor`.

## Operator-run verification commands (not executed by Codex under the current instruction)

```powershell
$env:TEMP = "E:\ArkaimVPNproject\.tmp"
$env:TMP = "E:\ArkaimVPNproject\.tmp"
$env:TMPDIR = "E:\ArkaimVPNproject\.tmp"
$env:PIP_CACHE_DIR = "E:\ArkaimVPNproject\.pip-cache"
$env:npm_config_cache = "E:\ArkaimVPNproject\.npm-cache"
$env:HF_HOME = "E:\ArkaimVPNproject\models"
& "E:\ArkaimVPNproject\runtime\python\python.exe" -m unittest discover -s backend/tests -v
$frontendTests = Get-ChildItem "E:\ArkaimVPNproject\frontend\src" -Recurse -Filter "*.test.js" | ForEach-Object FullName
& "E:\ArkaimVPNproject\runtime\node\node.exe" --test $frontendTests
& "E:\ArkaimVPNproject\runtime\corepack\v1\pnpm\12.14.0\pnpm-native.exe" --dir frontend run build
```
