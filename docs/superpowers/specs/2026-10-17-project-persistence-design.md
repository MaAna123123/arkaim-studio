# Persistent Projects and Whisper Recovery Design

Date: 2026-10-17

## Purpose

Arkaim Studio must preserve a user's project and partial Whisper transcription on drive `E:` so work can be continued after the backend, browser, or computer restarts. Recovery must never start Whisper automatically. The user explicitly chooses whether to continue from the saved percentage or begin again.

This design also introduces a recent-projects view and backend-owned editor autosave. Browser `sessionStorage` remains a fast per-tab cache, but project files on disk become the source of truth.

## Goals

- Persist project metadata, editor state, exports, and partial Whisper progress under `E:\ArkaimVPNproject\data\projects`.
- Restore interrupted transcription as `stopped`, with its original operation ID and checkpoint.
- Offer `Продолжить с N%` without automatically consuming CPU after startup.
- Show up to 20 recently updated projects without deleting older project data.
- Preserve projects when source media or a checkpoint is missing or damaged.
- Write all JSON atomically and keep temporary files on the same volume as their target.
- Preserve compatibility with existing uploads and `backend/transcripts` files.

## Non-goals

- Cloud synchronization or multi-user access.
- Automatic deletion, retention, or storage quotas.
- Moving existing source media into new directories.
- Resuming a Whisper model in the middle of its current decoder segment. Recovery starts at the last completed segment with the existing one-second overlap.
- A database, account system, or cross-device project sharing.

## Storage Layout

The first version uses `project_id = video_id`. IDs must be valid UUIDs before they are used in a path.

```text
E:\ArkaimVPNproject\data\projects\<project-id>\
├── project.json
├── checkpoint.json
└── transcript.json
```

No project runtime, checkpoint, temporary file, or cache is written to drive `C:`.

### `project.json`

```json
{
  "schema_version": 1,
  "project_id": "uuid",
  "video_id": "uuid",
  "title": "Source title",
  "source_filename": "video.mp4",
  "source_path": "backend/uploads/video.mp4",
  "video": {},
  "editor": {
    "segments": [],
    "selected": [],
    "clip_results": [],
    "subtitle_context": null,
    "subtitle_notice": "",
    "export_padding": 0.5,
    "aspect_ratio": "9:16",
    "burn_subtitles": true,
    "insert_ad": false,
    "text_ad_enabled": false,
    "text_ad_text": "",
    "text_ad_position": "top",
    "text_ad_interval": 4,
    "text_ad_opacity": 0.75,
    "video_banner_enabled": false,
    "video_banner_height": 380
  },
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC"
}
```

`source_path` is stored as a validated path relative to the project root. Public API responses do not expose unnecessary absolute paths.

### `checkpoint.json`

`checkpoint.json` is the only source of truth for incomplete transcription state. Whisper state is not duplicated in `project.json`.

```json
{
  "schema_version": 1,
  "operation_id": "uuid",
  "operation_type": "transcription",
  "video_id": "uuid",
  "status": "stopped",
  "progress_pct": 37,
  "message": "Прервано после закрытия приложения — можно продолжить",
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC",
  "started_at": "ISO-8601 UTC",
  "finished_at": "ISO-8601 UTC",
  "eta_seconds": null,
  "error": null,
  "pending_action": null,
  "language": null,
  "partial_result": {
    "checkpoint": 412.8,
    "full_text": "...",
    "segments": [],
    "words": [],
    "paragraphs": [],
    "duration": 1100,
    "is_complete": false
  }
}
```

Runtime-only objects such as `OperationControl`, futures, threads, and event loops are never serialized.

### `transcript.json`

The final transcript contains the current completed transcript payload and excludes incomplete-only fields such as `checkpoint` and `is_complete`.

## Atomic File Rules

`ProjectStore` owns all project filesystem access.

For every JSON write it:

1. Validates the project ID and confined destination path.
2. Creates a unique temporary file in the same project directory as the destination.
3. Writes UTF-8 JSON, flushes the file, and calls `os.fsync()`.
4. Replaces the destination with `os.replace()`.
5. Removes a leftover temporary file in `finally`.
6. Serializes writes with a per-project lock.

If replacement fails, the previous valid destination remains available. A damaged JSON file is reported and skipped; it is never silently overwritten or deleted during startup.

## Backend Components

### `ProjectStore`

A new `backend/project_store.py` module provides:

- project creation and atomic updates;
- checkpoint save, load, and removal;
- transcript save and lookup;
- recent-project summaries and project details;
- source existence checks;
- schema validation and safe handling of damaged records;
- fallback lookup for legacy `backend/transcripts/<video-id>.json`.

Filesystem I/O does not belong in `OperationManager`.

### `OperationManager`

Add a validated `restore(snapshot)` operation that:

- accepts only supported transcription fields and statuses;
- rejects duplicate or invalid identities;
- creates a fresh `OperationControl`;
- does not schedule work;
- preserves the operation ID, timestamps, progress, and partial result.

### `TranscriptionService`

The service receives `ProjectStore` and:

- saves the latest partial result from `on_partial`;
- persists meaningful status changes and worker failures;
- reconstructs `_metadata` and `_video_operations` for recovered work;
- resumes with the saved checkpoint and retained result;
- writes the final transcript before deleting the checkpoint;
- leaves the checkpoint intact when final transcript writing fails;
- prevents concurrent duplicate resumes under its existing lock.

The checkpoint is written after every completed segment. Reliability is preferred over premature write-throttling in the first version.

## Startup and Shutdown

At backend startup, `ProjectStore` scans valid project directories and checkpoints.

- `queued`, `running`, `paused`, and `stopped` become `stopped`.
- `pending_action` and `eta_seconds` become `null`.
- The message becomes `Прервано после закрытия приложения — можно продолжить`.
- The checkpoint, partial result, progress, and operation ID are preserved.
- No worker starts automatically.
- `failed` remains failed and can be restarted explicitly.
- `completed` is represented by a valid final transcript rather than an active operation.
- `cancelled` is not restored.

At graceful shutdown, current workers receive the existing cooperative stop request. The last fully persisted checkpoint remains valid even if the worker does not finish before the shutdown timeout.

## API

### List projects

`GET /api/projects?limit=20`

Returns project summaries ordered by `updated_at` descending:

```json
{
  "projects": [
    {
      "project_id": "uuid",
      "video_id": "uuid",
      "title": "Video title",
      "filename": "video.mp4",
      "updated_at": "ISO-8601 UTC",
      "duration": 1234,
      "source_exists": true,
      "has_transcript": false,
      "clips_count": 0,
      "transcription": {
        "operation_id": "uuid",
        "status": "stopped",
        "progress_pct": 37,
        "checkpoint": 412.8
      }
    }
  ]
}
```

The limit affects only the response. It never deletes directories or files.

### Read project

`GET /api/projects/{project_id}` returns the validated project, editor state, derived source status, final transcript status, and recoverable operation snapshot.

### Save editor state

`PATCH /api/projects/{project_id}` accepts only supported editor fields. It does not accept arbitrary paths, transcript blobs, operation identity, or runtime state.

### Resume and restart

- Existing `POST /api/operations/{operation_id}/resume` resumes a recovered operation.
- `POST /api/projects/{project_id}/transcription/restart` starts Whisper from zero after user confirmation. It replaces only the checkpoint/operation state and does not delete the source project or completed exports.
- A missing source returns a conflict with a clear message and preserves all project data.

## Frontend

### Recent projects

`RecentProjects.jsx` appears below the Arkaim hero and above the URL/file input when no project is open.

- Desktop: three-column grid.
- Up to 900 px: two columns.
- Up to 680 px: one column with full-width actions.
- Long names use a two-line clamp.
- Status and progress remain visible.

Card states:

- Missing source: `Исходный файл не найден`; open/resume disabled except for project details.
- Queued/running: current progress; open only.
- Paused/stopped/recovered: open, continue, and explicit restart.
- Failed: open and explicit restart.
- Completed: transcript ready and clip count.
- No operation: video uploaded.

Restart requires a short confirmation because it replaces recoverable checkpoint data.

### Project hydration

A single mapper converts project details into App state. It restores video, segments, selection, clip results, subtitle context, notices, and editor/export options. Selection is converted from an array to a `Set`.

The operation hook gains an explicit way to adopt a recovered snapshot or resume by operation ID. The UI does not rely on timing between project hydration and active-operation polling.

### Autosave

- Editor changes are saved with a 600–800 ms debounce.
- Pending autosave is cancelled when the project changes.
- Initial project hydration is guarded so default React state cannot overwrite stored data.
- Playback time is not autosaved on every tick.
- Browser `sessionStorage` stays as a fast fallback, while backend project JSON is authoritative.

## Error Handling

- A damaged project file does not block other projects or backend startup.
- A damaged checkpoint keeps the project visible but disables resume with a clear diagnostic.
- A missing source keeps metadata and checkpoint unchanged.
- If the source file becomes available again at its stored location, resume becomes available without losing progress.
- An incompatible future schema is reported as unsupported and left untouched.
- Failed writes return a clear API error and preserve the last valid version.

## Compatibility and Migration

- Existing uploads remain in `backend/uploads`; the project stores their validated relative paths.
- Existing `backend/transcripts/<video-id>.json` remains a read fallback.
- A legacy transcript is copied into `transcript.json` when its project is first opened, but the legacy file is not deleted.
- No project or media file is automatically removed.

## Security and Privacy

- Project IDs must be UUIDs.
- All resolved project paths must remain inside `data/projects`.
- Public responses omit internal absolute paths and sensitive settings.
- Subscription, VPN, API, and OAuth credentials are never written into project files.
- All persistence stays local to the project on drive `E:`.

## Test Strategy

Implementation follows test-driven development.

Critical backend tests cover:

- atomic replacement and cleanup of temporary files;
- preservation of the previous file after replacement failure;
- damaged project/checkpoint isolation;
- path confinement and traversal rejection;
- recent-project sorting and a non-destructive 20-item response limit;
- restart normalization for `queued`, `running`, `paused`, and `stopped`;
- no automatic worker launch after recovery;
- resume with the exact saved checkpoint and retained result;
- protection against duplicate concurrent resume;
- final transcript write before checkpoint removal;
- checkpoint retention when final writing fails;
- missing-source conflict without data loss;
- legacy transcript fallback.

Frontend tests cover:

- project-detail mapping into editor state;
- project status/action precedence;
- missing-source presentation;
- no automatic transcription start after hydration;
- disabled duplicate resume while an action is pending;
- completed and damaged-checkpoint states.

The final integration scenario is:

1. Start transcription.
2. Persist at least one partial segment.
3. Recreate the manager and service as if the backend restarted.
4. List and open the recovered project.
5. Resume from the saved checkpoint.
6. Complete transcription.
7. Confirm the final transcript is readable and the checkpoint is gone.

## Delivery Order

1. Atomic `ProjectStore` and tests.
2. Operation restoration and transcription checkpoint lifecycle.
3. Project API and legacy transcript compatibility.
4. Recent-project cards and project hydration.
5. Editor autosave and full restart integration verification.
