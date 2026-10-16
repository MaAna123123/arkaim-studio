# Persistent Projects and Whisper Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Arkaim Studio projects and incomplete Whisper progress under `E:\ArkaimVPNproject\data\projects`, then let the user explicitly reopen and continue work after a backend, browser, or computer restart.

**Architecture:** Add a filesystem-focused `ProjectStore` that owns UUID validation, schema validation, per-project locking, and atomic JSON writes. Keep `OperationManager` in memory but teach it to restore validated snapshots; let `TranscriptionService` coordinate checkpoint persistence and worker reconstruction. Expose a narrow project API, then add a frontend project mapper, recent-project cards, explicit operation adoption, and guarded debounced autosave.

**Tech Stack:** Python 3.12.10, FastAPI, Pydantic, `unittest`, React 19, Vite 8, Node's built-in test runner.

**Spec:** `docs/superpowers/specs/2026-10-17-project-persistence-design.md`

## Global Constraints

- Keep every runtime file, cache, temporary file, checkpoint, and model under `E:\ArkaimVPNproject`; never introduce a project dependency on drive `C:`.
- Treat `project_id` as equal to `video_id` in this version and validate it as a UUID before building any path.
- Never delete media, projects, exports, legacy transcripts, or damaged JSON automatically.
- Do not auto-start Whisper during backend startup or frontend hydration.
- Preserve the current upload, YouTube, transcript, analysis, and export behavior for projects that have no persisted record yet.
- Preserve existing uncommitted user changes. Before editing a dirty shared file, save its baseline diff; before each commit inspect `git diff` and use `git add -p` to stage only the new task's hunks. Never stage a whole pre-existing dirty shared file.
- Run focused tests after each red/green cycle and the complete affected suite (all backend tests for backend tasks; every frontend test plus build for frontend tasks) after every task before its commit. Run both complete suites at integration boundaries and before completion.

## Review Focus

- Atomic write ordering and failure recovery, especially final-transcript-before-checkpoint-removal.
- Path confinement, UUID validation, schema-version handling, and omission of absolute paths from API responses.
- Lock ordering across `ProjectStore`, `OperationManager`, and `TranscriptionService`; no duplicate workers after concurrent resume requests.
- Hydration/autosave races and stale network responses when switching projects.
- Backward compatibility with `backend/uploads` and `backend/transcripts`.

---

### Task 1: Build the atomic project storage foundation

**Files:**

- Create: `backend/project_store.py`
- Create: `backend/tests/test_project_store.py`

- [ ] Write failing tests for UUID-only project IDs, rejection of traversal-like IDs, and confinement of resolved paths below `data/projects`.

- [ ] Write failing tests that create a project and assert the exact version-1 `project.json` defaults, relative `source_path`, UTC timestamps, UTF-8 round-trip behavior, and an allow-list for persisted video metadata.

- [ ] Add a privacy test proving arbitrary `api_key`, OAuth token, subscription URL, VPN credential, absolute path, and unknown nested metadata are neither saved nor returned.

- [ ] Write failing tests for `_atomic_write_json`: same-directory unique temporary file, flush plus `os.fsync`, `os.replace`, temporary-file cleanup, and preservation of the previous valid destination when replacement raises.

- [ ] Run the focused tests and confirm they fail for missing implementation:

  ```powershell
  & "E:\ArkaimVPNproject\runtime\python\python.exe" -m unittest backend.tests.test_project_store -v
  ```

- [ ] Implement `ProjectStore(project_root, legacy_transcripts_dir=None)` with `_validate_id`, `_project_dir`, `_atomic_write_json`, `_read_json`, a per-project `RLock`, and constants for schema version and supported editor fields.

- [ ] Implement `create_project(video_id, title, source_filename, source_path, video)` and `get_project(project_id)` without exposing an absolute source path.

- [ ] Re-run the focused tests and confirm they pass.

- [ ] Run the complete backend test suite before committing.

- [ ] Commit only the storage foundation:

  ```powershell
  git add backend/project_store.py backend/tests/test_project_store.py
  git commit -m "feat: add atomic project storage"
  ```

### Task 2: Add project updates, summaries, damage isolation, and legacy transcript fallback

**Files:**

- Modify: `backend/project_store.py`
- Modify: `backend/tests/test_project_store.py`

- [ ] Add failing tests for allow-listed editor updates, rejection of unknown editor/path/identity fields, timestamp refresh, and preservation of fields omitted by a patch.

- [ ] Add failing tests for recent-project sorting by `updated_at`, response limiting without filesystem deletion, `source_exists`, `clips_count`, `has_transcript`, and transcription summary derivation. Implement and turn this cycle green before starting checkpoint work.

- [ ] Add one failing test proving a malformed `project.json` is reported and skipped without blocking healthy projects; implement and turn it green without rewriting the damaged file.

- [ ] Add a separate failing test proving a valid project with malformed `checkpoint.json` remains visible, resume is disabled with a diagnostic, and the checkpoint remains untouched; implement and turn it green.

- [ ] Add failing tests for unsupported future schema versions and missing source files; verify no project/checkpoint data is changed.

- [ ] Add failing tests for checkpoint save/load/remove and final transcript save/load; implement this storage cycle and turn it green before adding legacy behavior.

- [ ] Add failing tests for the legacy `backend/transcripts/<video-id>.json` fallback and copy-on-first-open behavior without deleting the legacy file; then implement and turn this cycle green.

- [ ] Run the focused tests and confirm the new cases fail.

- [ ] Implement `update_editor`, `list_projects`, `source_status`, `save_checkpoint`, `load_checkpoint`, `remove_checkpoint`, `save_transcript`, and `load_transcript` with narrow public dictionaries and structured diagnostics.

- [ ] Ensure list limits are clamped to a safe response range but never used as a retention policy.

- [ ] Re-run the focused tests and confirm they pass.

- [ ] Run the complete backend test suite before committing.

- [ ] Commit the completed store behavior:

  ```powershell
  git add backend/project_store.py backend/tests/test_project_store.py
  git commit -m "feat: persist project state and transcripts"
  ```

### Task 3: Restore operation snapshots without starting workers

**Files:**

- Modify: `backend/operation_manager.py`
- Modify: `backend/tests/test_operation_manager.py`

- [ ] Add failing tests for `OperationManager.restore(snapshot)`: preserved operation ID/timestamps/progress/partial result, fresh `OperationControl`, and no scheduling side effects.

- [ ] Add failing tests for rejecting duplicate operation IDs, invalid UUIDs, wrong operation types, unknown statuses, unsupported fields, and missing required identity fields.

- [ ] Add failing tests for normalization of `queued`, `running`, `paused`, and `stopped` into `stopped`, with cleared `pending_action`/ETA and the recovery message from the design.

- [ ] Add cases proving `failed` remains failed, while `completed` and `cancelled` checkpoint snapshots are not restored as resumable work.

- [ ] Run the focused tests and confirm they fail.

- [ ] Implement a strict restore-field allow-list and normalization helper in `OperationManager`; insert the restored record under the manager lock and construct a new control object.

- [ ] Re-run operation-manager tests and confirm they pass.

- [ ] Run the complete backend test suite before committing.

- [ ] Commit the restoration primitive:

  ```powershell
  git add backend/operation_manager.py backend/tests/test_operation_manager.py
  git commit -m "feat: restore interrupted operations"
  ```

### Task 4: Persist Whisper checkpoints and reconstruct resumable workers

**Files:**

- Modify: `backend/transcription_service.py`
- Modify: `backend/tests/test_transcription_service.py`
- Create: `backend/tests/test_transcription_recovery.py`

- [ ] Extend the service test fixture with an in-memory/fake project store. Keep `project_store` optional during this task so existing `main.py` construction remains compatible until Task 5 wires the production store.

- [ ] Add failing tests that every accepted `on_partial` saves the latest checkpoint and meaningful status transitions (`paused`, `stopped`, `failed`) persist the corresponding snapshot; implement and turn this cycle green.

- [ ] Add failing recovery tests that load checkpoints, normalize interrupted work to stopped, call `manager.restore`, rebuild `_metadata` and `_video_operations`, and never call the transcriber during recovery; implement and turn this cycle green before resume tests.

- [ ] Add a failing resume test asserting the worker receives the exact persisted `checkpoint` as `resume_from` and the exact `partial_result` as `retained_result`; implement and turn it green.

- [ ] Add a concurrency test issuing two resume requests and assert only one worker is launched; implement duplicate suppression and turn it green.

- [ ] Add completion-order tests: final transcript is atomically saved before checkpoint removal; if transcript writing fails, the operation fails safely and the checkpoint remains. Implement and turn this cycle green.

- [ ] Add a crash-window test with both a valid final `transcript.json` and stale `checkpoint.json`: final transcript wins, no resumable operation is restored, and no worker starts. Leave or safely remove the stale checkpoint according to one documented policy, without risking the final transcript.

- [ ] Add a missing-source test that rejects resume with `OperationConflictError` while preserving all saved data.

- [ ] Run focused service and recovery tests and confirm they fail.

- [ ] Inject `ProjectStore` into `TranscriptionService`; centralize checkpoint persistence in a small private method called outside conflicting manager/store lock scopes.

- [ ] Implement `recover_projects()` (or an equivalently explicit startup method) that reconstructs metadata from validated projects and checkpoints without scheduling workers.

- [ ] Update completion to save the final project transcript first, transition successfully, and only then remove the checkpoint. Keep the existing legacy transcript output until API migration is complete.

- [ ] Re-run focused tests, including existing pause/stop tests, and confirm they pass.

- [ ] Run the complete backend test suite to prove the optional constructor kept all `main.py` imports green.

- [ ] Commit service recovery:

  ```powershell
  git add backend/transcription_service.py backend/tests/test_transcription_service.py backend/tests/test_transcription_recovery.py
  git commit -m "feat: recover whisper checkpoints"
  ```

### Task 5: Expose the project API and connect project creation to ingestion

**Files:**

- Modify: `backend/schemas.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/test_transcription_api.py`
- Create: `backend/tests/test_project_api.py`

- [ ] Add request models with an allow-listed editor patch payload; do not accept source paths, project IDs, operation IDs, transcripts, or runtime state from clients.

- [ ] Add failing API tests for `GET /api/projects?limit=20`, `GET /api/projects/{project_id}`, and `PATCH /api/projects/{project_id}`, including 400/404 responses and hidden absolute paths. Implement these handlers and turn the API cycle green before changing ingestion.

- [ ] Add failing ingestion tests proving local upload and successful YouTube download/auto flows create or update a project record only after valid source metadata exists. Route each ingestion exit through one helper and turn this cycle green.

- [ ] Add failing tests that `POST /api/projects/{project_id}/transcription/restart` replaces recoverable operation state from zero only after a valid source is present, returns 409 for missing source, and leaves editor/export data intact. Implement and turn this cycle green.

- [ ] Add failing startup tests proving recovered operations are available via existing operation endpoints but no worker starts automatically. Wire the production store and startup recovery, then turn this cycle green.

- [ ] Add transcript API tests for project-local final transcripts, legacy fallback compatibility, and final-transcript precedence over a stale checkpoint; implement and turn this cycle green.

- [ ] Run the focused API tests and confirm they fail.

- [ ] Define `PROJECTS_DIR = PROJECT_ROOT / "data" / "projects"`, instantiate `ProjectStore`, inject it into `TranscriptionService`, and invoke recovery during FastAPI startup before accepting work.

- [ ] Add thin API handlers that translate store validation/damage/missing-source errors into clear 400, 404, 409, or 500 responses without mutating saved data on failure.

- [ ] Update upload and all successful YouTube ingestion exits through one helper that writes a consistent project record.

- [ ] Route transcription lookup/write through `ProjectStore` while retaining the legacy transcript fallback and current public response shape.

- [ ] Re-run the focused API tests and the full backend suite:

  ```powershell
  & "E:\ArkaimVPNproject\runtime\python\python.exe" -m unittest discover -s backend/tests -v
  ```

- [ ] Commit the backend API integration:

  ```powershell
  git add backend/schemas.py backend/tests/test_project_api.py
  git add -p backend/main.py backend/tests/test_transcription_api.py
  git commit -m "feat: expose persistent project api"
  ```

### Task 6: Add pure frontend project-state rules and recent-project cards

**Files:**

- Create: `frontend/src/lib/projectState.js`
- Create: `frontend/src/lib/projectState.test.js`
- Create: `frontend/src/components/RecentProjects.jsx`
- Modify: `frontend/src/App.css`

- [ ] Write failing Node tests for mapping project details into App state, including array-to-`Set` selection conversion and defaults for older/incomplete records.

- [ ] Write failing tests for card status/action precedence: missing source, damaged checkpoint, running, stopped/recovered, failed, completed, and uploaded-only.

- [ ] Assert resume/restart actions are disabled while an action is pending, the accessible resume label is exactly `Продолжить с N%`, and no mapper result requests automatic transcription.

- [ ] Run the frontend unit tests and confirm the new file fails:

  ```powershell
  & "E:\ArkaimVPNproject\runtime\node\node.exe" --test frontend/src/lib/projectState.test.js
  ```

- [ ] Implement pure helpers such as `mapProjectToEditorState(project)`, `getProjectCardState(project)`, and `formatProjectProgress(project)` without browser or React dependencies.

- [ ] Implement `RecentProjects` with accessible buttons for open, continue, and restart; require confirmation before restart and keep project details accessible when the source is missing.

- [ ] Add responsive Arkaim styling: three columns on desktop, two below 900 px, one below 680 px, two-line title clamp, and visible status/progress.

- [ ] Re-run the new tests and existing frontend tests.

- [ ] Run the complete backend suite, every frontend `*.test.js`, and the frontend build before committing.

- [ ] Commit the isolated frontend primitives:

  ```powershell
  git add frontend/src/lib/projectState.js frontend/src/lib/projectState.test.js frontend/src/components/RecentProjects.jsx
  git add -p frontend/src/App.css
  git commit -m "feat: add recent project cards"
  ```

### Task 7: Hydrate projects, explicitly adopt operations, and debounce autosave

**Files:**

- Modify: `frontend/src/hooks/useOperation.js`
- Modify: `frontend/src/hooks/operationState.test.js`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/lib/sessionStorage.js`
- Modify: `frontend/src/lib/sessionStorage.test.js`

- [ ] Add failing operation-hook helper tests for explicit adoption of a recovered snapshot and resume by operation ID, including stale-video rejection and duplicate-action suppression. Implement `adopt`/resume claiming and turn this cycle green before App integration.

- [ ] Add failing persistence tests for a 600–800 ms debounced patch payload, cancellation when project ID changes, and a hydration guard that prevents default state from overwriting loaded editor data. Implement the pure scheduling/guard helpers and turn this cycle green before wiring effects.

- [ ] Run the focused frontend tests and confirm they fail.

- [ ] Extend `useOperation` with an explicit `adopt(snapshot)` method and `resumeOperation(operationId)` path that first claims the matching recovered snapshot. Keep generation and abort-controller guards intact.

- [ ] In `App.jsx`, fetch recent projects only while the backend is online, render `RecentProjects` below the hero when no project is open, and fetch full details before hydrating a selected project.

- [ ] Apply the pure mapper in one state-update path, set the recovered operation explicitly, and never call `startTranscription` during hydration.

- [ ] Add a hydration ref/generation guard and a 700 ms project-editor autosave. Cancel pending timers and requests when switching/resetting projects; exclude playback time and transient progress.

- [ ] Keep `sessionStorage` as a per-tab fallback, but prefer successful backend project hydration and clear only the browser cache on reset.

- [ ] Wire Continue to the recovered operation ID and Restart to the new restart endpoint after confirmation; surface missing-source and damaged-checkpoint errors without hiding the card.

- [ ] Re-run all frontend unit tests and build:

  ```powershell
  $frontendTests = Get-ChildItem "E:\ArkaimVPNproject\frontend\src" -Recurse -Filter "*.test.js" | ForEach-Object FullName
  & "E:\ArkaimVPNproject\runtime\node\node.exe" --test $frontendTests
  & "E:\ArkaimVPNproject\runtime\node\npm.cmd" --prefix frontend run build
  ```

- [ ] Commit the frontend integration:

  ```powershell
  git add frontend/src/hooks/operationState.test.js frontend/src/lib/sessionStorage.test.js
  git add -p frontend/src/hooks/useOperation.js frontend/src/App.jsx frontend/src/lib/sessionStorage.js
  git commit -m "feat: restore and autosave projects"
  ```

### Task 8: Verify restart recovery end to end and document operation

**Files:**

- Create: `backend/tests/test_project_recovery_integration.py`
- Modify: `README.md`
- Modify: `PROJECT_INSTRUCTIONS.md`

- [ ] Write the integration test before any final cleanup: create a project, start transcription with a controllable fake, persist at least one partial segment, recreate store/manager/service, recover as stopped, explicitly resume, finish, then assert the final transcript is readable and `checkpoint.json` is gone.

- [ ] Add variants for backend shutdown before worker completion and source restoration after an initial missing-source conflict.

- [ ] Run the new integration test and fix only behavior required by the approved spec.

- [ ] Document where projects live on `E:`, how to open/continue/restart one, the meaning of missing-source and damaged-checkpoint messages, and that the 20-card limit never deletes data.

- [ ] Run the complete verification suite:

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
  & "E:\ArkaimVPNproject\runtime\node\npm.cmd" --prefix frontend run build
  ```

- [ ] Start the local app using its E-drive scripts and manually verify: recent projects, desktop/mobile card layout, no auto-resume, explicit continue from saved progress, restart confirmation, autosave after editing, missing-source state, and browser console with no errors.

- [ ] Review the resulting diff against every Goal, Non-goal, Error Handling, Compatibility, and Test Strategy item in the approved spec.

- [ ] Commit only integration coverage and documentation:

  ```powershell
  git add backend/tests/test_project_recovery_integration.py
  git add -p README.md PROJECT_INSTRUCTIONS.md
  git commit -m "test: verify project recovery workflow"
  ```

### Task 9: Independent code review and final correction pass

**Files:**

- Review all files changed in Tasks 1–8.
- Modify only files required to resolve verified review findings.

- [ ] Use `superpowers:requesting-code-review` with the approved spec, this plan, and the completed commit range.

- [ ] Classify findings by correctness, data-loss risk, security, concurrency, compatibility, and UI recovery behavior; reproduce each actionable issue before changing code.

- [ ] Address high- and medium-severity verified findings with focused regression tests first.

- [ ] Re-run the complete verification suite and inspect `git status` to ensure no runtime data, cache, model, temporary, or user-owned unrelated file is staged.

- [ ] Commit review corrections separately, if any:

  ```powershell
  git add <only-reviewed-files>
  git commit -m "fix: harden project recovery"
  ```
