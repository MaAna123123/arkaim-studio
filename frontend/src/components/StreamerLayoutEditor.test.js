import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeCropRegion,
  normalizeWebcamHeightRatio,
  normalizedRectFromDrag,
  previewCoverWindow,
} from '../lib/streamerLayout.js';


test('drag selection is direction-independent and clamped to the video frame', () => {
  const bounds = { left: 100, top: 50, width: 500, height: 250 };

  assert.deepEqual(
    normalizedRectFromDrag(
      { clientX: 500, clientY: 240 },
      { clientX: 50, clientY: 25 },
      bounds,
    ),
    { x: 0, y: 0, width: 0.8, height: 0.76 },
  );
});

test('drag selection ignores accidental clicks smaller than three percent', () => {
  assert.equal(
    normalizedRectFromDrag(
      { clientX: 100, clientY: 100 },
      { clientX: 105, clientY: 104 },
      { left: 0, top: 0, width: 500, height: 250 },
    ),
    null,
  );
});

test('preview cover keeps crop aspect and trims the wider dimension like FFmpeg', () => {
  const window = previewCoverWindow(
    { x: 0, y: 0, width: 1, height: 1 },
    16 / 9,
    9 / (16 * 0.35),
  );

  assert.equal(window.height, 100);
  assert.ok(window.width > 100);
  assert.equal(window.left, -(window.width - 100) / 2);
  assert.equal(window.top, 0);
});

test('persisted streamer geometry is rejected when it falls outside the frame', () => {
  assert.equal(normalizeCropRegion({ x: 0.9, y: 0, width: 0.2, height: 0.2 }), null);
  assert.deepEqual(
    normalizeCropRegion({ x: 0.1, y: 0.2, width: 0.3, height: 0.4 }),
    { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
  );
  assert.equal(normalizeWebcamHeightRatio(0.9), 0.35);
});
