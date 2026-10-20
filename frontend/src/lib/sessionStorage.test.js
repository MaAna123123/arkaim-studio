import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildHydratedProjectVideo,
  buildProjectEditorPatch,
  canAutosaveProject,
  clearSession,
  createProjectAutosaveScheduler,
  loadSession,
  saveSession,
} from './sessionStorage.js';
import { createDefaultComposition } from './clipComposition.js';


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

test('session and autosave payloads deep-copy independent clip compositions', () => {
  const storage = memoryStorage();
  const editor = {
    segments: [
      { start: 1, end: 5, composition: createDefaultComposition({ start: 1, end: 5 }, '9:16') },
      { start: 10, end: 20, composition: createDefaultComposition({ start: 10, end: 20 }, '9:16') },
    ],
  };

  saveSession(storage, editor);
  const restored = loadSession(storage);
  const patch = buildProjectEditorPatch(editor);
  restored.segments[0].composition.main_layer.crop.x = 0.2;
  patch.segments[1].composition.main_layer.destination.y = 0.3;

  assert.equal(editor.segments[0].composition.main_layer.crop.x, 0);
  assert.equal(editor.segments[1].composition.main_layer.destination.y, 0);
});

test('invalid stored data is discarded safely', () => {
  const storage = memoryStorage({ 'videopril.session.v1': '{broken' });
  assert.deepEqual(loadSession(storage), {});
});

function fakeTimers() {
  let nextId = 1;
  const timers = new Map();
  return {
    setTimer(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer(id) {
      timers.delete(id);
    },
    delays() {
      return [...timers.values()].map((timer) => timer.delay);
    },
    flush() {
      const pending = [...timers.values()];
      timers.clear();
      pending.forEach((timer) => timer.callback());
    },
    size() {
      return timers.size;
    },
  };
}

test('project autosave waits 700 ms and sends only the allow-listed editor payload', () => {
  const timers = fakeTimers();
  const saves = [];
  const scheduler = createProjectAutosaveScheduler({
    save: (projectId, payload) => saves.push({ projectId, payload }),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  const editor = {
    segments: [{ start: 1, end: 3 }],
    selected: new Set([0]),
    clipResults: [{ filename: 'clip.mp4' }],
    subtitleContext: { kind: 'full' },
    subtitleNotice: 'Готово',
    exportPadding: 0.4,
    aspectRatio: '1:1',
    burnSubtitles: false,
    insertAd: true,
    textAdEnabled: true,
    textAdText: 'Реклама',
    textAdPosition: 'bottom',
    textAdInterval: 8,
    textAdOpacity: 0.6,
    videoBannerEnabled: true,
    videoBannerHeight: 240,
    playerTime: 91,
    progress: { pct: 37 },
    transcribing: true,
  };

  assert.equal(scheduler.schedule({
    projectId: 'project-1', hydratedProjectId: 'project-1', editor,
  }), true);
  assert.deepEqual(timers.delays(), [700]);
  assert.deepEqual(saves, []);

  timers.flush();
  assert.deepEqual(saves, [{
    projectId: 'project-1',
    payload: {
      segments: [{ start: 1, end: 3 }],
      selected: [0],
      clip_results: [{ filename: 'clip.mp4' }],
      subtitle_context: { kind: 'full' },
      subtitle_notice: 'Готово',
      export_padding: 0.4,
      aspect_ratio: '1:1',
      burn_subtitles: false,
      insert_ad: true,
      text_ad_enabled: true,
      text_ad_text: 'Реклама',
      text_ad_position: 'bottom',
      text_ad_interval: 8,
      text_ad_opacity: 0.6,
      video_banner_enabled: true,
      video_banner_height: 240,
    },
  }]);
  assert.deepEqual(buildProjectEditorPatch(editor), saves[0].payload);
});

test('project autosave cancels the old project when the project id changes', () => {
  const timers = fakeTimers();
  const saves = [];
  const scheduler = createProjectAutosaveScheduler({
    save: (projectId) => saves.push(projectId),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  scheduler.schedule({
    projectId: 'project-1', hydratedProjectId: 'project-1', editor: {},
  });
  scheduler.schedule({
    projectId: 'project-2', hydratedProjectId: 'project-2', editor: {},
  });

  assert.equal(timers.size(), 1);
  timers.flush();
  assert.deepEqual(saves, ['project-2']);
});

test('hydration guard prevents default editor state from overwriting a project', () => {
  const timers = fakeTimers();
  const scheduler = createProjectAutosaveScheduler({
    save: () => assert.fail('unhydrated state must not be saved'),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  assert.equal(canAutosaveProject('project-1', null), false);
  assert.equal(canAutosaveProject('project-1', 'project-2'), false);
  assert.equal(canAutosaveProject('project-1', 'project-1'), true);
  assert.equal(scheduler.schedule({
    projectId: 'project-1', hydratedProjectId: null, editor: {},
  }), false);
  assert.equal(timers.size(), 0);
});

test('project hydration adapts flat persisted metadata to the player video shape', () => {
  assert.deepEqual(buildHydratedProjectVideo({
    project_id: 'project-1',
    video_id: 'video-1',
    source_filename: 'interview.MP4',
  }, {
    video_id: 'video-1',
    title: 'Интервью',
    duration: 91,
    width: 1920,
    height: 1080,
  }), {
    video_id: 'video-1',
    title: 'Интервью',
    duration: 91,
    width: 1920,
    height: 1080,
    metadata: {
      duration: 91,
      width: 1920,
      height: 1080,
    },
    filename: 'interview.MP4',
    video_url: '/uploads/video-1.mp4',
  });
});
