import assert from 'node:assert/strict';
import test from 'node:test';

import { clearSession, loadSession, saveSession } from './sessionStorage.js';


function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test('session helpers round-trip editor state in the current tab', () => {
  const storage = memoryStorage();
  saveSession(storage, { video: { video_id: 'video-1' }, segments: [] });
  assert.deepEqual(loadSession(storage), {
    video: { video_id: 'video-1' }, segments: [],
  });
  clearSession(storage);
  assert.deepEqual(loadSession(storage), {});
});

test('invalid stored data is discarded safely', () => {
  const storage = memoryStorage({ 'videopril.session.v1': '{broken' });
  assert.deepEqual(loadSession(storage), {});
});
