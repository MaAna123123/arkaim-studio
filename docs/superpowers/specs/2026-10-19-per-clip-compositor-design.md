# Per-clip video compositor design

Date: 2026-10-19

## Goal

Add a non-destructive editor for every generated clip. A user must be able to extend the clip before and after the detected moment, crop and reposition the source video, reserve empty canvas space, and place a video banner or advertising video into that space. Each clip keeps its own layout and timing without modifying the source file.

## User workflow

1. Every generated segment has an **Edit clip** action before export and every rendered clip has a **Re-edit** action.
2. The action opens a modal editor for that segment.
3. A source timeline displays the detected moment and adjustable left and right handles. Moving either handle changes the source start or end time and therefore the final duration. Handles cannot move outside the source duration.
4. The preview canvas represents the selected output aspect ratio. The main video layer can be cropped, scaled, and moved horizontally or vertically.
5. The user can enable a video banner layer, drag it, resize it independently on both axes, and choose `contain`, `cover`, or `stretch` fitting.
6. The banner has start and end controls relative to the edited clip. It may cover the whole clip or any shorter interval.
7. The uploaded advertising video keeps its existing **insert in the middle** mode and gains an **overlay layer** mode with the same position, size, fitting, start, and end controls as the banner.
8. Saving closes the modal and stores changes for that clip. Export always renders from the original source video, not from an already encoded clip.

## Scope

### Included

- Independent edit settings for every segment.
- Extending or shortening segment start and end times.
- Main-video crop and destination rectangle editing.
- Banner and advertising-video overlay rectangles.
- Independent width and height resizing.
- Overlay timing and fitting modes.
- Fast browser preview and matching FFmpeg export.
- Persistence in the existing project editor state and local session recovery.
- Existing streamer layout remains available outside this editor.

### Not included

- Multi-track arbitrary numbers of overlays.
- Keyframe animation of position or scale.
- Transitions, filters, color grading, or audio mixing controls.
- Destructive modification of source uploads.
- Frame-by-frame editing.

## Data model

Each segment may contain an optional `composition` object:

```json
{
  "source_start": 12.5,
  "source_end": 48.2,
  "output_aspect_ratio": "9:16",
  "main_layer": {
    "crop": { "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0 },
    "destination": { "x": 0.0, "y": 0.25, "width": 1.0, "height": 0.75 },
    "fit": "cover"
  },
  "banner_layer": {
    "enabled": true,
    "destination": { "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.25 },
    "fit": "stretch",
    "start": 0.0,
    "end": 12.0
  },
  "ad_layer": {
    "mode": "overlay",
    "destination": { "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.25 },
    "fit": "cover",
    "start": 12.0,
    "end": 18.0
  }
}
```

All rectangles use normalized values from `0` to `1`. This makes browser preview and backend rendering independent of preview size and supports both 720×1280 and 1080×1920 outputs. `source_start` and `source_end` are absolute source-video seconds. Overlay times are relative to the edited clip.

Segments without `composition` use the current export behavior unchanged. Existing projects therefore remain compatible.

## Frontend design

### Editor shell

Create a focused `ClipComposer` component opened from `HighlightList` and `ExportPanel`. The modal contains:

- a preview canvas;
- a tool selector for main video, video banner, and advertising video;
- numeric inputs for precise position and size;
- fit-mode controls;
- a source-range timeline;
- overlay start/end inputs;
- Reset, Cancel, and Save actions.

### Preview canvas

The preview uses regular video elements and CSS transforms rather than encoding on every interaction. The canvas clips layers to the output bounds. Dragging changes normalized `x` and `y`; corner handles change normalized width and height. Main-video crop uses the established normalized crop-region utilities where possible.

The preview must show empty canvas regions as dark background. Layer selection adds a visible outline that is never part of the exported video.

### Timeline behavior

The detected segment is the initial range. The user can move the left handle toward zero and the right handle toward the source duration. The editor displays final duration continuously. Invalid or crossed handles are prevented in the UI.

### State and persistence

Composition is stored directly on its segment so indexes cannot accidentally transfer settings after segment deletion or reordering. Project autosave and session serialization include the nested object. Incoming values are normalized before use; invalid saved values fall back to defaults.

## Backend design

### API contract

Extend each export segment schema with optional composition data. Validate:

- finite source and overlay times;
- `0 <= source_start < source_end <= source_duration` after source metadata is read;
- normalized rectangles remain within the output canvas;
- crop rectangles have a minimum usable size;
- fit is one of `contain`, `cover`, or `stretch`;
- overlay timing satisfies `0 <= start < end <= edited_duration`;
- ad mode is `insert` or `overlay`.

Malformed composition returns HTTP 422 with a clear Russian error instead of silently producing a wrong clip.

### Rendering pipeline

For a composed segment, FFmpeg performs one primary render from the original source:

1. Trim the source using `source_start` and `source_end`.
2. Crop the main layer from normalized source coordinates.
3. Scale it according to its fit mode and place it on a transparent/dark output canvas using its destination rectangle.
4. Loop the banner or ad input only as required for the selected overlay interval.
5. Crop/scale each overlay according to `contain`, `cover`, or `stretch` and place it with `overlay=enable='between(t,start,end)'`.
6. Apply subtitles and text advertising after visual composition so they remain readable above all video layers.
7. Encode once using the existing high-quality H.264 settings.

The existing midpoint advertising insertion remains a separate final mode when `ad_layer.mode` is `insert`. It is not combined with the overlay mode for the same clip.

### Output dimensions

- Streamer layout and composed vertical output: 1080×1920.
- Standard vertical output outside the compositor retains the existing compatibility path.
- Original aspect output uses the source resolution unless the composition explicitly selects vertical output.

## Error handling and recovery

- Closing or cancelling the editor discards only unsaved modal changes.
- Saved composition is included in project autosave.
- Missing banner/ad media disables that layer in the editor and produces a clear warning before export.
- A failed clip render reports the affected clip label while leaving other completed clips available, matching current batch behavior.
- Source range and overlay range are clamped only in the interactive preview; the backend still validates and rejects malformed requests.

## Compatibility and migration

No database migration is required because editor state already stores JSON-compatible segment data. Old segments without composition keep the current behavior. Existing global banner-height controls remain available for legacy exports; a per-clip composition takes precedence for that clip.

## Verification strategy

Automated coverage should include:

- normalization and validation of all rectangles and time ranges;
- session/project round-trip of per-segment composition;
- extending both sides of a source segment;
- FFmpeg filter construction for `contain`, `cover`, and `stretch`;
- banner and ad overlay timing;
- legacy segment export without composition;
- independent settings for two clips from the same source.

Per the current operator request, implementation verification will be limited to static syntax, conflict, and diff checks unless the operator later asks Codex to run the automated or media tests. The operator will perform visual and runtime testing.

## Acceptance criteria

- Every clip can be edited independently.
- The clip can be extended before and after the detected moment up to source bounds.
- Main video can be cropped, resized, and moved in both axes.
- Empty output areas can hold a freely positioned and independently resized video banner.
- Banner and overlay advertising have adjustable start and end times.
- Advertising can be either a midpoint insertion or an overlay.
- Saved preview geometry matches exported geometry.
- Existing projects and exports without composition continue to work.
- Source media is never modified.
