# Whisper Operation Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local Whisper transcription run in the background with pause, resume, stop-with-checkpoint, partial text, and page-refresh recovery while the backend remains running.

**Architecture:** Add a thread-safe in-memory `OperationManager`, extend `VideoTranscriber` with cooperative controls and checkpoint-aware merging, and put background orchestration behind focused API endpoints. The React UI consumes those endpoints through a dedicated hook and renders a reusable operation panel while legacy progress remains intact for the other workflows.

**Tech Stack:** Python 3.12, FastAPI, faster-whisper, threading/asyncio, unittest/pytest, React 19, Vite 8, vanilla CSS.

**Spec:** `docs/superpowers/specs/2026-10-14-whisper-operation-manager-design.md`

## Global Constraints

- Keep every runtime, cache, model, dependency, and temporary file under `E:\ArkaimVPNproject`; do not write project data to drive `C:`.
- Use bundled executables from `runtime/python`, `runtime/node`, and `runtime/ffmpeg`.
- Preserve existing uncommitted user changes and stage only files belonging to each task.
- Do not add dependencies in this stage.
- Unfinished operation state exists only in backend memory and is intentionally lost when backend exits.
- Only an explicit Whisper transcription is migrated to a background operation in this plan; download, analysis, and export retain their current execution model.

## Review Focus

- A pause or stop requested while one Whisper segment is being computed takes effect at the next segment boundary without losing the completed segment.
- Repeated start clicks or two tabs cannot create two transcription workers for the same video.
- Resume from a stopped checkpoint does not duplicate overlapping text or word timestamps.
- A WebSocket reconnect or page refresh restores the matching video operation and never adopts another tab's operation.
- A partial, stopped, or failed result is never written or exposed as a completed transcript.

---

### Task 1: Thread-safe operation state machine

**Files:**
- Create: `backend/operation_manager.py`
- Create: `backend/tests/test_operation_manager.py`

**Interfaces:**
- Produces: `OperationControl.pause()`, `resume()`, `request_stop()`, `wait_if_paused() -> bool`, and `stop_requested`.
- Produces: `OperationManager.create(operation_type: str, video_id: str) -> dict`, `get(operation_id: str) -> dict | None`, `find_for_video(video_id: str, include_stopped: bool = True) -> dict | None`, `transition(operation_id: str, target: str) -> dict`, `update(operation_id: str, **fields) -> dict`, `control(operation_id: str) -> OperationControl`, and `public_snapshot(operation_id: str) -> dict`.
- Statuses: `queued`, `running`, `paused`, `stopped`, `completed`, `failed`, `cancelled`.

- [ ] **Step 1: Write failing state-machine tests**

Add tests named `test_valid_transcription_state_transitions`, `test_invalid_transition_preserves_state`, `test_duplicate_video_operation_returns_existing_record`, `test_terminal_retention_never_evicts_active_records`, and `test_public_snapshot_hides_threading_controls`. Assert exact statuses, HTTP-independent conflict exceptions, a 100-terminal-record cap, and serializable public data.

- [ ] **Step 2: Run the focused test and verify failure**

Run with project-local temp/cache variables:

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests/test_operation_manager.py -q
```

Expected: FAIL because `operation_manager` does not exist.

- [ ] **Step 3: Implement the minimal manager and controls**

Use `threading.RLock` around records and `threading.Event` for pause/stop control. Return defensive copies from public methods, validate transitions centrally, reuse an unfinished operation for the same `(operation_type, video_id)`, and evict only the oldest terminal records beyond 100.

- [ ] **Step 4: Run the focused test and verify success**

Run the command from Step 2. Expected: all operation-manager tests PASS.

- [ ] **Step 5: Commit only this task**

```powershell
git add -- backend/operation_manager.py backend/tests/test_operation_manager.py
git commit -m "feat: add in-memory operation manager"
```

### Task 2: Cooperative Whisper checkpoints and overlap-safe resume

**Files:**
- Modify: `backend/transcriber.py`
- Create: `backend/tests/test_transcriber_operations.py`

**Interfaces:**
- Consumes: `OperationControl` from Task 1.
- Produces: `VideoTranscriber.transcribe(video_path: str, language: str | None = None, on_progress=None, control: OperationControl | None = None, on_partial=None, resume_from: float = 0.0, retained_result: dict | None = None) -> dict`.
- Produces: `merge_transcript_segments(retained: list[dict], resumed: list[dict]) -> list[dict]` and a complete transcript rebuilt from merged segments.

- [ ] **Step 1: Write failing deterministic transcriber tests**

Use fake Whisper segments/model info and patch `_extract_audio`. Add `test_pause_waits_between_segments_and_resume_continues`, `test_stop_returns_incomplete_result_with_checkpoint`, `test_resume_uses_one_second_overlap`, `test_overlap_merge_removes_duplicate_boundary_segment`, and `test_partial_callback_receives_immutable_snapshots`.

- [ ] **Step 2: Run the focused test and verify failure**

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests/test_transcriber_operations.py -q
```

Expected: FAIL because control/checkpoint parameters and merge logic do not exist.

- [ ] **Step 3: Implement cooperative control and transcript assembly**

Check pause/stop before consuming the next generator item, publish a copied partial result after each completed item, set checkpoint to the greatest completed segment end, and return an internal completion marker for orchestration. Pass `clip_timestamps=[max(0.0, resume_from - 1.0)]` only when resuming. Deduplicate by overlapping normalized time ranges, then rebuild words, paragraphs, and full text chronologically.

- [ ] **Step 4: Run transcriber and existing backend tests**

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests/test_transcriber_operations.py backend/tests/test_regressions.py backend/tests/test_media_regressions.py -q
```

Expected: all tests PASS without loading a real Whisper model.

- [ ] **Step 5: Commit only this task**

```powershell
git add -- backend/transcriber.py backend/tests/test_transcriber_operations.py
git commit -m "feat: add controllable Whisper checkpoints"
```

### Task 3: Background transcription service and operation-scoped events

**Files:**
- Create: `backend/transcription_service.py`
- Create: `backend/tests/test_transcription_service.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `OperationManager`, `OperationControl`, and the extended `VideoTranscriber.transcribe`.
- Produces: `TranscriptionService.start(video_id: str, video_path: str, language: str | None, transcript_path: Path, loop: asyncio.AbstractEventLoop) -> dict`, `pause(operation_id: str) -> dict`, `resume(operation_id: str, loop: asyncio.AbstractEventLoop) -> dict`, and `stop(operation_id: str) -> dict`.
- Produces events through an injected async publisher with `type`, `operation_id`, `operation_type`, `video_id`, `status`, `progress_pct`, `message`, `eta_seconds`, and `partial_result`.

- [ ] **Step 1: Write failing service lifecycle tests**

Add `test_start_returns_before_worker_finishes`, `test_duplicate_start_reuses_operation`, `test_stop_retains_checkpoint_without_writing_final_file`, `test_resume_stopped_operation_reuses_partial_result`, `test_completion_atomically_writes_final_transcript_once`, and `test_worker_failure_retains_partial_and_sets_safe_error` using a controllable fake transcriber and a temporary directory under `main.TEMP_DIR`.

- [ ] **Step 2: Run the focused test and verify failure**

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests/test_transcription_service.py -q
```

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement orchestration and atomic final save**

Run workers through the event loop's executor, convert callbacks to operation updates, estimate remaining seconds from elapsed time and processed media fraction, publish copied snapshots through `asyncio.run_coroutine_threadsafe`, and write final JSON through a sibling temporary file followed by `Path.replace`. Paused workers remain alive; stopped workers exit and can be rescheduled from their checkpoint.

- [ ] **Step 4: Integrate manager/service singletons and operation event publishing in `main.py`**

Keep the current `broadcast` function for legacy events. Instantiate one manager and service beside the existing transcriber, and provide the running event loop when scheduling workers.

- [ ] **Step 5: Run service plus regression tests**

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests/test_transcription_service.py backend/tests/test_regressions.py backend/tests/test_media_regressions.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit only this task**

```powershell
git add -- backend/transcription_service.py backend/tests/test_transcription_service.py backend/main.py
git commit -m "feat: orchestrate background transcriptions"
```

### Task 4: FastAPI start, query, control, and conflict endpoints

**Files:**
- Modify: `backend/main.py`
- Create: `backend/tests/test_transcription_api.py`

**Interfaces:**
- Consumes: `TranscriptionService` from Task 3.
- Produces: `POST /api/video/transcribe`, `GET /api/operations/{operation_id}`, `GET /api/operations`, and `POST /api/operations/{operation_id}/{action}` for `pause`, `resume`, and `stop`.

- [ ] **Step 1: Write failing API contract tests**

Add tests for HTTP 202 on a new start, HTTP 200 with the existing operation on duplicate start, final-transcript compatibility, 404 for unknown operation, 409 for invalid transition, filtering by `video_id` and `active=true`, operation IDs in emitted WebSocket payloads, and 409 when transcript analysis is requested while a queued/running/paused/stopped explicit transcription exists.

- [ ] **Step 2: Run the focused API test and verify failure**

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests/test_transcription_api.py -q
```

Expected: FAIL because the new contracts are not implemented.

- [ ] **Step 3: Implement endpoint validation and response codes**

Map missing manager records to 404 and state conflicts to 409. Keep the current completed-transcript response unchanged. Return public snapshots only. Treat `stopped` as active/resumable in the filtered listing. Reject transcript analysis while an explicit resumable operation exists, but leave equal-chunk and frame analysis unchanged.

- [ ] **Step 4: Run all backend tests**

```powershell
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests -q
```

Expected: all backend tests PASS.

- [ ] **Step 5: Commit only this task**

```powershell
git add -- backend/main.py backend/tests/test_transcription_api.py
git commit -m "feat: expose transcription operation API"
```

### Task 5: React operation hook and Whisper control panel

**Files:**
- Create: `frontend/src/hooks/useOperation.js`
- Create: `frontend/src/components/OperationPanel.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Produces: `useOperation({ apiBase, videoId, onCompleted })` returning `{ operation, start, pause, resume, stop, refresh, error }`.
- Produces: `OperationPanel({ operation, error, onPause, onResume, onStop })`.
- Consumes operation snapshots and events from Task 4.

- [ ] **Step 1: Add the hook with operation-ID filtering and refresh recovery**

On video change, query `/api/operations?video_id=...&active=true`; adopt only the matching transcription operation. Open its own reconnecting WebSocket listener, ignore legacy and nonmatching operation events, and poll the status endpoint every three seconds only while the socket is disconnected or the operation is nonterminal. Clear timers and sockets during cleanup.

- [ ] **Step 2: Add the operation panel**

Render translated states, bounded progress, formatted ETA, incomplete partial text, inline error, and only valid actions: Pause+Stop for running, Continue+Stop for paused, and Continue for stopped. Show “приостанавливается/останавливается” while a control request is awaiting backend confirmation.

- [ ] **Step 3: Replace the explicit Whisper flow in `App.jsx`**

Make `handleTranscribe` call the hook's `start` and stop treating the start response as a completed transcript. Derive `transcribing` from queued/running/paused/stopped operation state for existing button/analysis guards. On completion fetch `/api/video/transcript/{video_id}`, set the existing transcript state, and keep SRT and legacy analysis/export flows unchanged. Reset operation UI when the selected video changes.

- [ ] **Step 4: Add focused responsive styles**

Style action rows, state badge, partial-text scroll area, ETA, pending-control state, and narrow-screen button wrapping in `App.css`, reusing existing color variables and button classes.

- [ ] **Step 5: Run the local frontend production build**

```powershell
$env:TEMP='E:\ArkaimVPNproject\temp'
$env:TMP=$env:TEMP
$env:npm_config_cache='E:\ArkaimVPNproject\cache\npm'
& 'E:\ArkaimVPNproject\runtime\node\npm.cmd' --prefix 'E:\ArkaimVPNproject\frontend' run build
```

Expected: TypeScript/Vite build succeeds with no compile errors.

- [ ] **Step 6: Commit only this task**

```powershell
git add -- frontend/src/hooks/useOperation.js frontend/src/components/OperationPanel.jsx frontend/src/App.jsx frontend/src/App.css
git commit -m "feat: add Whisper operation controls"
```

### Task 6: Integrated verification and operating documentation

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_INSTRUCTIONS.md`

**Interfaces:**
- Consumes the completed backend and frontend feature.
- Produces user-facing instructions describing pause, stop, resume, memory-only retention, and backend-restart behavior.

- [ ] **Step 1: Run the complete backend suite from the project-local runtime**

```powershell
$env:TEMP='E:\ArkaimVPNproject\temp'
$env:TMP=$env:TEMP
$env:TMPDIR=$env:TEMP
$env:HF_HOME='E:\ArkaimVPNproject\models_cache'
& 'E:\ArkaimVPNproject\runtime\python\python.exe' -m pytest backend/tests -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run the frontend production build again**

Use the Task 5 build command. Expected: build succeeds.

- [ ] **Step 3: Run a local API smoke test**

Start `backend/start_backend.bat`, verify `/api/health`, start a fake/patched test transcription through the automated API suite, and confirm operation snapshots are JSON-serializable. Do not trigger a real model download as part of verification.

- [ ] **Step 4: Update documentation**

Document the Whisper controls and explicitly state that unfinished progress survives browser refresh but not backend shutdown. Preserve the E-drive-only startup instructions.

- [ ] **Step 5: Inspect the final diff and verify no C-drive project paths were introduced**

```powershell
git diff --check
rg -n "C:\\\\|C:/" backend frontend README.md PROJECT_INSTRUCTIONS.md
```

Expected: no whitespace errors and no newly introduced C-drive runtime/cache paths.

- [ ] **Step 6: Commit documentation only**

```powershell
git add -- README.md PROJECT_INSTRUCTIONS.md
git commit -m "docs: explain resumable Whisper workflow"
```
