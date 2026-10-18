import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canUseRecentProjects,
  createProjectLifecycleController,
} from './projectLifecycle.js';


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function response(data, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => data };
}

function abortError() {
  return Object.assign(new Error('aborted'), { name: 'AbortError' });
}

function controlledFetch() {
  const requests = [];
  const fetchImpl = (url, options = {}) => {
    const result = deferred();
    requests.push({ url, options, ...result });
    return result.promise;
  };
  return { fetchImpl, requests };
}

async function readResponse(result) {
  const data = await result.json();
  if (!result.ok) throw new Error(data?.detail || `HTTP ${result.status}`);
  return data;
}

function createHarness(overrides = {}) {
  const transport = controlledFetch();
  const events = [];
  const errors = [];
  const pending = [];
  const autosave = {
    cancelCount: 0,
    schedules: [],
    cancel() { this.cancelCount += 1; },
    schedule(value) { this.schedules.push(value); return true; },
  };
  const controller = createProjectLifecycleController({
    apiBase: 'http://api.test',
    fetchImpl: transport.fetchImpl,
    readResponse,
    autosave,
    cancelAutosaveRequest: () => events.push(['cancel-autosave-request']),
    hydrateProject: (project, options) => events.push(['hydrate', project, options]),
    clearMissingProject: (project) => events.push(['missing', project]),
    receiveRecentProjects: (data) => events.push(['recent', data]),
    receiveRestart: (projectId, snapshot) => events.push(['restart', projectId, snapshot]),
    setPendingAction: (value) => pending.push(value),
    setError: (message) => errors.push(message),
    ...overrides,
  });
  return { controller, transport, events, errors, pending, autosave };
}

test('failed authoritative detail preserves fallback and a later retry replaces it only after success', async () => {
  let editor = { source: 'session', segments: ['cached'] };
  let sessionCleared = false;
  const harness = createHarness({
    hydrateProject: (project) => { editor = { source: 'backend', ...project.editor }; },
    clearMissingProject: () => { editor = null; sessionCleared = true; },
  });

  const failed = harness.controller.loadProject('project-a', { origin: 'restore' });
  assert.deepEqual(editor, { source: 'session', segments: ['cached'] });
  harness.transport.requests[0].resolve(response({ detail: 'temporary failure' }, { ok: false, status: 500 }));
  assert.equal(await failed, null);
  assert.deepEqual(editor, { source: 'session', segments: ['cached'] });
  assert.equal(sessionCleared, false);

  const retry = harness.controller.loadProject('project-a', { origin: 'restore' });
  assert.deepEqual(editor, { source: 'session', segments: ['cached'] });
  harness.transport.requests[1].resolve(response({
    project_id: 'project-a', video_id: 'project-a', source_exists: true,
    editor: { segments: ['authoritative'] },
  }));
  assert.equal((await retry).project_id, 'project-a');
  assert.deepEqual(editor, { source: 'backend', segments: ['authoritative'] });
  assert.equal(sessionCleared, false);
});

test('detail resolves before hydration and hydration never starts transcription', async () => {
  const harness = createHarness();
  const loading = harness.controller.loadProject('project-a');

  assert.deepEqual(harness.events.filter(([type]) => type === 'hydrate'), []);
  assert.deepEqual(harness.transport.requests.map(({ url }) => url), [
    'http://api.test/api/projects/project-a',
  ]);

  harness.transport.requests[0].resolve(response({
    project_id: 'project-a', video_id: 'project-a', source_exists: true,
  }));
  await loading;

  assert.equal(harness.events.filter(([type]) => type === 'hydrate').length, 1);
  assert.equal(
    harness.transport.requests.some(({ url }) => url.endsWith('/api/video/transcribe')),
    false,
  );
});

test('missing source is the authoritative detail case that clears the fallback', async () => {
  const harness = createHarness();
  const loading = harness.controller.loadProject('project-a', { origin: 'restore' });
  harness.transport.requests[0].resolve(response({
    project_id: 'project-a', video_id: 'project-a', source_exists: false,
  }));
  await loading;
  assert.equal(harness.events.some(([type]) => type === 'missing'), true);
  assert.equal(harness.events.some(([type]) => type === 'hydrate'), false);
});

test('recent projects fetch only online without an open project and stale lists are aborted', async () => {
  const harness = createHarness();

  assert.equal(canUseRecentProjects({ serverOnline: false, hasVideo: false }), false);
  assert.equal(canUseRecentProjects({ serverOnline: true, hasVideo: true }), false);
  assert.equal(canUseRecentProjects({ serverOnline: true, hasVideo: false }), true);
  assert.equal(harness.controller.syncRecentProjects({ serverOnline: false, hasVideo: false }), null);
  assert.equal(harness.controller.syncRecentProjects({ serverOnline: true, hasVideo: true }), null);
  assert.equal(harness.transport.requests.length, 0);

  const online = harness.controller.syncRecentProjects({ serverOnline: true, hasVideo: false });
  assert.equal(harness.transport.requests.length, 1);
  const staleList = harness.transport.requests[0];

  const opening = harness.controller.loadProject('project-a');
  assert.equal(staleList.options.signal.aborted, true);
  staleList.resolve(response({ projects: [{ project_id: 'stale' }], diagnostics: [] }));
  assert.equal(await online, null);
  assert.equal(harness.events.some(([type]) => type === 'recent'), false);

  harness.transport.requests[1].resolve(response({
    project_id: 'project-a', video_id: 'project-a', source_exists: true,
  }));
  await opening;

  const nextList = harness.controller.syncRecentProjects({ serverOnline: true, hasVideo: false });
  const offlineList = harness.transport.requests[2];
  harness.controller.syncRecentProjects({ serverOnline: false, hasVideo: false });
  assert.equal(offlineList.options.signal.aborted, true);
  offlineList.resolve(response({ projects: [{ project_id: 'offline-stale' }], diagnostics: [] }));
  assert.equal(await nextList, null);
  assert.equal(harness.events.some(([type, data]) => type === 'recent' && data.projects?.[0]?.project_id === 'offline-stale'), false);
});

test('restart A cannot mutate or clear pending restart B when responses complete out of order', async () => {
  const harness = createHarness();
  const restartA = harness.controller.restartProject('project-a');
  const requestA = harness.transport.requests[0];
  const restartB = harness.controller.restartProject('project-b');
  const requestB = harness.transport.requests[1];

  assert.equal(requestA.options.signal.aborted, true);
  assert.deepEqual(harness.pending.at(-1), { projectId: 'project-b', action: 'restart' });

  requestA.resolve(response({
    operation_id: 'operation-a', operation_type: 'transcription', video_id: 'project-a',
  }));
  assert.equal(await restartA, null);
  assert.deepEqual(harness.pending.at(-1), { projectId: 'project-b', action: 'restart' });
  assert.equal(harness.events.some(([type, projectId]) => type === 'restart' && projectId === 'project-a'), false);

  requestB.resolve(response({
    operation_id: 'operation-b', operation_type: 'transcription', video_id: 'project-b',
  }));
  assert.equal((await restartB).operation_id, 'operation-b');
  assert.deepEqual(harness.pending.at(-1), null);
  assert.equal(harness.events.some(([type, projectId]) => type === 'restart' && projectId === 'project-b'), true);
});

test('reset and dispose abort detail, restart, and autosave work without surfacing AbortError', async () => {
  const harness = createHarness();
  const detail = harness.controller.loadProject('project-a');
  const detailRequest = harness.transport.requests[0];

  harness.controller.cancelAll();
  assert.equal(detailRequest.options.signal.aborted, true);
  detailRequest.reject(abortError());
  assert.equal(await detail, null);

  const restart = harness.controller.restartProject('project-a');
  const restartRequest = harness.transport.requests[1];

  harness.controller.cancelAll();
  assert.equal(restartRequest.options.signal.aborted, true);
  assert.equal(harness.autosave.cancelCount > 0, true);
  assert.equal(harness.events.some(([type]) => type === 'cancel-autosave-request'), true);

  restartRequest.reject(abortError());
  assert.equal(await restart, null);
  assert.deepEqual(harness.errors, []);

  const later = harness.controller.loadProject('project-b');
  const laterRequest = harness.transport.requests[2];
  harness.controller.dispose();
  assert.equal(laterRequest.options.signal.aborted, true);
  laterRequest.reject(abortError());
  assert.equal(await later, null);
});

test('restart rejects mismatched video and missing operation identity', async () => {
  const harness = createHarness();
  const wrongVideo = harness.controller.restartProject('project-a');
  harness.transport.requests[0].resolve(response({
    operation_id: 'operation-a', operation_type: 'transcription', video_id: 'project-b',
  }));
  assert.equal(await wrongVideo, null);
  assert.equal(harness.events.some(([type]) => type === 'restart'), false);
  assert.match(harness.errors.at(-1), /ответ|операц/i);

  const missingOperation = harness.controller.restartProject('project-a');
  harness.transport.requests[1].resolve(response({
    operation_type: 'transcription', video_id: 'project-a',
  }));
  assert.equal(await missingOperation, null);
  assert.equal(harness.events.some(([type]) => type === 'restart'), false);
});

test('autosave stays guarded until detail hydration commits and switch/reset cancels it', async () => {
  const harness = createHarness();
  assert.equal(harness.controller.scheduleAutosave({
    projectId: 'project-a', serverOnline: true, editor: { segments: [] },
  }), false);
  assert.deepEqual(harness.autosave.schedules, []);

  const loadingA = harness.controller.loadProject('project-a');
  assert.equal(harness.controller.scheduleAutosave({
    projectId: 'project-a', serverOnline: true, editor: { segments: [] },
  }), false);
  harness.transport.requests[0].resolve(response({
    project_id: 'project-a', video_id: 'project-a', source_exists: true,
  }));
  await loadingA;
  assert.equal(harness.controller.scheduleAutosave({
    projectId: 'project-a', serverOnline: true, editor: { segments: ['mapped'] },
  }), true);
  assert.equal(harness.autosave.schedules.length, 1);
  assert.equal(harness.autosave.schedules[0].hydratedProjectId, 'project-a');

  const loadingB = harness.controller.loadProject('project-b');
  assert.equal(harness.controller.scheduleAutosave({
    projectId: 'project-b', serverOnline: true, editor: { segments: [] },
  }), false);
  harness.controller.cancelAll();
  assert.equal(harness.autosave.cancelCount > 0, true);
  harness.transport.requests[1].reject(abortError());
  assert.equal(await loadingB, null);
});
