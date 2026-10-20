import test from 'node:test';
import assert from 'node:assert/strict';

import {
  compositionDuration,
  createDefaultComposition,
  normalizeComposition,
  updateCompositionLayer,
} from './clipComposition.js';

test('creates independent default compositions from segment bounds', () => {
  const segment = { start: 12.5, end: 48.2 };
  const first = createDefaultComposition(segment, '9:16');
  const second = createDefaultComposition(segment, '9:16');

  assert.deepEqual(first.main_layer.crop, { x: 0, y: 0, width: 1, height: 1 });
  assert.deepEqual(first.main_layer.destination, { x: 0, y: 0, width: 1, height: 1 });
  assert.equal(first.source_start, 12.5);
  assert.equal(first.source_end, 48.2);
  assert.equal(first.banner_layer, null);
  assert.equal(first.ad_layer, null);
  first.main_layer.crop.x = 0.2;
  assert.equal(second.main_layer.crop.x, 0);
});

test('normalizes finite geometry and clamps source range to duration', () => {
  const value = createDefaultComposition({ start: -3, end: 110 }, '9:16');
  value.main_layer.destination = { x: 0.1, y: 0.2, width: 0.8, height: 0.7 };

  const normalized = normalizeComposition(value, { start: 0, end: 100 }, 100);

  assert.equal(normalized.source_start, 0);
  assert.equal(normalized.source_end, 100);
  assert.deepEqual(normalized.main_layer.destination, value.main_layer.destination);
});

test('rejects crossed ranges, non-finite values, small rectangles, and unknown fits', () => {
  const base = createDefaultComposition({ start: 10, end: 20 }, '9:16');
  assert.equal(normalizeComposition({ ...base, source_start: 15, source_end: 14 }, { start: 10, end: 20 }, 30), null);
  assert.equal(normalizeComposition({ ...base, source_start: Number.NaN }, { start: 10, end: 20 }, 30), null);
  assert.equal(normalizeComposition({ ...base, main_layer: { ...base.main_layer, crop: { x: 0, y: 0, width: 0.02, height: 1 } } }, { start: 10, end: 20 }, 30), null);
  assert.equal(normalizeComposition({ ...base, main_layer: { ...base.main_layer, fit: 'stretch' } }, { start: 10, end: 20 }, 30), null);
});

test('rejects overlay timing outside edited duration', () => {
  const base = createDefaultComposition({ start: 10, end: 20 }, '9:16');
  base.banner_layer = {
    enabled: true,
    destination: { x: 0, y: 0, width: 1, height: 0.25 },
    fit: 'stretch',
    start: 0,
    end: 11,
  };
  assert.equal(normalizeComposition(base, { start: 10, end: 20 }, 30), null);
});

test('updates one layer without mutating the previous composition', () => {
  const base = createDefaultComposition({ start: 10, end: 20 }, '9:16');
  const updated = updateCompositionLayer(base, 'main_layer', {
    destination: { x: 0, y: 0.25, width: 1, height: 0.75 },
  });

  assert.equal(base.main_layer.destination.height, 1);
  assert.equal(updated.main_layer.destination.height, 0.75);
  assert.equal(compositionDuration(updated), 10);
});
