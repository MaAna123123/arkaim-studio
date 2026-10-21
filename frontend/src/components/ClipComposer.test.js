import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
let vite;
let ClipComposer;

before(async () => {
  vite = await createServer({ root: frontendRoot, appType: 'custom', server: { middlewareMode: true } });
  ({ default: ClipComposer } = await vite.ssrLoadModule('/src/components/ClipComposer.jsx'));
});

after(async () => vite?.close());

const byId = (root, id) => root.find((node) => node.props.id === id);

test('opens from segment defaults and saves an independently extended source range', async () => {
  const saved = [];
  let renderer;
  await act(async () => {
    renderer = TestRenderer.create(React.createElement(ClipComposer, {
      segment: { start: 10, end: 20, label: 'Moment' },
      index: 0,
      sourceDuration: 60,
      sourceUrl: '/source.mp4',
      outputAspectRatio: '9:16',
      onSave: (_index, value) => saved.push(value),
      onCancel: () => {},
    }));
  });

  await act(async () => byId(renderer.root, 'composition-source-start').props.onChange({ target: { value: '0' } }));
  await act(async () => byId(renderer.root, 'composition-source-end').props.onChange({ target: { value: '60' } }));
  await act(async () => byId(renderer.root, 'btn-save-composition').props.onClick());

  assert.equal(saved[0].start, 0);
  assert.equal(saved[0].end, 60);
  assert.equal(saved[0].composition.source_start, 0);
  assert.equal(saved[0].composition.source_end, 60);
});

test('banner and advertising controls are disabled until media exists', async () => {
  let renderer;
  await act(async () => {
    renderer = TestRenderer.create(React.createElement(ClipComposer, {
      segment: { start: 2, end: 8 }, index: 0, sourceDuration: 20,
      onSave: () => {}, onCancel: () => {},
    }));
  });
  assert.equal(byId(renderer.root, 'btn-enable-banner-layer').props.disabled, true);
  assert.equal(byId(renderer.root, 'btn-enable-ad-layer').props.disabled, true);
});

test('numeric geometry fields resize width and height independently', async () => {
  let saved;
  let renderer;
  await act(async () => {
    renderer = TestRenderer.create(React.createElement(ClipComposer, {
      segment: { start: 2, end: 8 }, index: 0, sourceDuration: 20,
      onSave: (_index, value) => { saved = value; }, onCancel: () => {},
    }));
  });
  await act(async () => byId(renderer.root, 'composition-rect-width').props.onChange({ target: { value: '80' } }));
  await act(async () => byId(renderer.root, 'composition-rect-height').props.onChange({ target: { value: '60' } }));
  await act(async () => byId(renderer.root, 'btn-save-composition').props.onClick());
  assert.equal(saved.composition.main_layer.destination.width, 0.8);
  assert.equal(saved.composition.main_layer.destination.height, 0.6);
});

test('can remove a saved composition and return the clip to legacy export', async () => {
  const segment = { start: 2, end: 8, composition: { source_start: 2, source_end: 8 } };
  let saved;
  let renderer;
  await act(async () => {
    renderer = TestRenderer.create(React.createElement(ClipComposer, {
      segment, index: 0, sourceDuration: 20,
      onSave: (_index, value) => { saved = value; }, onCancel: () => {},
    }));
  });

  await act(async () => byId(renderer.root, 'btn-reset-legacy-composition').props.onClick());

  assert.equal('composition' in saved, false);
  assert.equal(saved.start, 2);
  assert.equal(saved.end, 8);
});
