# Whisper Operation Manager Design

## Purpose

Add controllable, observable long-running operations to videopril, starting with local Whisper transcription. A user must be able to pause transcription, stop it without losing the in-memory checkpoint, continue from that checkpoint, refresh the frontend without losing the current operation view, and see partial transcript text while localhost remains running.

This is the first of three agreed priorities. It creates the operation boundary needed for the later backend and frontend refactors without attempting those broad refactors in the same change.

## User-visible behavior

Starting Whisper returns immediately and displays a live operation panel. The panel shows state, percentage, current message, approximate remaining time, and partial transcript text.

For Whisper the panel offers:

- **Pause** while the operation is running. The worker pauses at a segment boundary and retains the loaded process and all accumulated segments.
- **Continue** while paused. The same worker resumes.
- **Stop** while running or paused. The worker exits, releases CPU-intensive transcription work, and retains the accumulated transcript and last processed media timestamp in backend memory.
- **Continue from stopped position** while stopped. A new Whisper pass starts slightly before the saved timestamp and merges new segments with the retained transcript without duplicate text at the boundary.

Completed transcripts continue to use the existing transcript JSON format and storage path. Partial transcripts are not written as completed transcripts and cannot be used as if processing finished.

Refreshing the page restores the active or stopped operation for the selected video by querying the backend. Closing or restarting the backend discards unfinished operation state. This is intentional: persistence is guaranteed only while localhost remains running.

## Scope

### Included

- An in-memory backend operation manager.
- Background execution for the explicit `/api/video/transcribe` flow.
- Whisper pause, resume, stop, and continue-from-checkpoint controls.
- Partial transcript snapshots and operation-specific progress.
- WebSocket progress messages scoped by operation ID.
- HTTP status and control endpoints used for recovery after a page refresh.
- A focused frontend operation hook and Whisper controls.
- Regression tests for state transitions, checkpoint merging, API behavior, WebSocket scoping, and frontend production build.

### Not included in this stage

- Recovery after backend restart or computer restart.
- Persisting partial transcripts as final transcript files.
- Pausing FFmpeg exports or network downloads.
- Migrating every existing download, analysis, and export route to background operations. The manager's types allow those operation kinds, but those migrations belong to later refactor stages.
- A complete split of `backend/main.py` or `frontend/src/App.jsx`; only feature-sized extraction needed for clean boundaries is included.

## Architecture

### Operation model

`OperationManager` owns operation records in memory. Each record contains:

- `operation_id`: opaque UUID string.
- `operation_type`: initially `transcription`; reserved values include `download`, `analysis`, and `export`.
- `video_id`.
- `status`: `queued`, `running`, `paused`, `stopped`, `completed`, `failed`, or `cancelled`.
- `progress_pct`, `message`, `created_at`, `updated_at`, `started_at`, and `finished_at`.
- `eta_seconds`, when enough progress data exists.
- `error`, populated only for failed operations.
- a serializable partial result containing transcript segments, words, text, language, duration, and checkpoint timestamp.
- internal pause and stop controls that are never returned directly through the API.

State transitions are validated centrally:

```text
queued -> running -> paused -> running -> completed
                    |          |
                    v          v
                  stopped <- stopped

queued/running/paused -> failed
stopped -> running (continue from checkpoint)
```

Invalid transitions return HTTP 409 and leave the operation unchanged. Only one unfinished transcription operation may exist for a video. A repeated start request returns the existing operation rather than starting a second model pass.

Completed, failed, cancelled, and stopped records remain queryable for the lifetime of the backend. To avoid unbounded memory use, the manager retains at most 100 terminal records and evicts the oldest terminal records first; active records are never evicted.

### Whisper control boundary

`VideoTranscriber.transcribe` gains an optional control object and a partial-result callback. It checks control state between yielded Whisper segments:

1. wait while paused;
2. exit cleanly when stop is requested;
3. append the next segment;
4. publish an immutable partial snapshot and checkpoint timestamp.

The checkpoint is the greatest completed segment end timestamp. Continuing a stopped operation starts at `max(0, checkpoint - 1.0)` seconds by using faster-whisper's clip timestamp support. The merge discards a resumed segment when its normalized time range substantially overlaps an existing retained segment, then rebuilds full text and word arrays in chronological order. The one-second overlap protects words spoken across a segment boundary.

Pause is cooperative rather than instantaneous: a currently executing Whisper segment may finish first. Stop is also observed at the next segment boundary. The UI describes these states as “приостанавливается” or “останавливается” until the worker confirms the final state.

### Background worker

The transcription HTTP route creates an operation and schedules its synchronous Whisper worker through the existing executor boundary. The request returns HTTP 202 without waiting for transcription to finish.

The worker owns transcript assembly. On successful completion it atomically writes the existing final transcript JSON and marks the operation completed. On pause or stop it does not write a final transcript. On failure it retains the last partial snapshot, stores a safe error message, and marks the operation failed.

The existing analysis flow may continue to create a transcript synchronously in this first stage. It must not falsely attach to or overwrite an unfinished explicit transcription operation. If analysis is requested while an explicit transcription for the same video is queued, running, paused, or stopped, the API returns HTTP 409 with a message directing the user to finish transcription first.

## API contract

### Start

`POST /api/video/transcribe`

Request remains compatible with the current `video_id` and optional language fields. Response changes to HTTP 202:

```json
{
  "operation_id": "uuid",
  "status": "queued",
  "video_id": "video-id"
}
```

If a final transcript already exists, the endpoint may return the existing transcript using the current successful response shape. If an unfinished operation already exists, it returns that operation with HTTP 200.

### Query

- `GET /api/operations/{operation_id}` returns a public operation snapshot.
- `GET /api/operations?video_id={video_id}&active=true` returns matching non-terminal or resumable stopped operations. `stopped` is included because it has retained progress that the user can continue.

The partial result is included for transcription operations. Internal threading primitives and local filesystem paths are never serialized.

### Control

- `POST /api/operations/{operation_id}/pause`
- `POST /api/operations/{operation_id}/resume`
- `POST /api/operations/{operation_id}/stop`

Each returns the updated public snapshot. Control endpoints accept only valid transitions and only transcription operations in this stage.

For a paused operation, `resume` releases the existing worker. For a stopped operation, `resume` schedules a new worker from the saved checkpoint. The client uses the same endpoint and decides only from the returned/current status.

### WebSocket

Operation events use this shape:

```json
{
  "type": "operation",
  "operation_id": "uuid",
  "operation_type": "transcription",
  "video_id": "video-id",
  "status": "running",
  "progress_pct": 42,
  "message": "Распознаю речь…",
  "eta_seconds": 310,
  "partial_result": { "text": "…", "segments": [] }
}
```

The frontend ignores events whose operation ID does not match the operation it is displaying. Existing legacy progress events remain supported for other flows during this stage.

## Frontend design

A focused `useOperation` hook owns:

- the current operation snapshot;
- WebSocket filtering by operation ID;
- polling fallback/status refresh;
- start and control requests;
- restoration by `video_id` after page reload;
- cleanup when the selected video changes.

`App.jsx` uses the hook for explicit Whisper transcription while retaining the current state handling for other flows. A small operation control component renders status, progress, ETA, partial text, and state-appropriate buttons.

When an operation completes, the frontend loads the saved transcript through the existing transcript endpoint and enters the current completed-transcript flow. When stopped, the partial text remains visibly labelled as incomplete. Analysis controls are disabled until transcription completes.

Network or WebSocket interruption does not stop backend work. The hook reconnects and refreshes the operation snapshot. API errors are shown in the operation panel and do not erase a partial result.

## Error handling and concurrency

- Missing operations return HTTP 404.
- Invalid transitions and conflicting work for the same video return HTTP 409.
- A missing video file fails the operation before model loading.
- A worker exception marks the operation failed and preserves its partial result.
- A failed operation is not automatically resumed. The user can start a new operation; retained failed text is diagnostic only.
- Concurrent updates to an operation are protected by manager-level locking. Public snapshots are copies, so frontend serialization cannot mutate worker state.
- Progress callbacks publish through the main asyncio event loop, preserving thread safety for WebSocket sends.

## Testing strategy

Backend tests use a fake segment generator and do not download or load a real Whisper model. They verify:

- legal and illegal operation state transitions;
- pause blocks consumption after a segment boundary and resume continues it;
- stop retains partial text and checkpoint;
- resume begins with a one-second overlap and removes duplicated boundary segments;
- completion writes the final transcript only once;
- duplicate starts for one video do not create duplicate workers;
- analysis returns 409 while resumable transcription exists;
- WebSocket events contain and preserve operation IDs;
- terminal-record eviction never removes active operations.

API tests use FastAPI's test client with the transcriber and executor boundary replaced by deterministic fakes. Existing backend regression and media tests must remain green.

The frontend is verified with its production build. Hook behavior is kept in small pure helpers where possible so operation selection and button-state rules can be tested without adding a large browser-test dependency in this stage.

## Storage and environment constraints

No new project runtime, cache, model, dependency, or temporary file may be placed on drive `C:`. All commands use the bundled runtimes and set temporary/cache environment variables to directories under `E:\ArkaimVPNproject`. This stage adds no dependency that requires installation.

## Acceptance criteria

The stage is complete when:

1. Starting Whisper no longer keeps the HTTP request open for the duration of transcription.
2. The user can pause, resume, stop, and continue a stopped transcription from its retained checkpoint.
3. Partial text and progress survive a frontend refresh while the backend remains running.
4. Restarting the backend intentionally clears unfinished progress.
5. Two frontend tabs do not mix operation progress.
6. A partial transcript is never mistaken for a completed saved transcript.
7. Existing completed-transcript, analysis, export, and media behavior remains operational.
8. Backend tests and the frontend production build pass using only project-local runtimes on drive `E:`.
