import assert from 'node:assert/strict';
import test, { after, before } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { act, createElement } from 'react';
import TestRenderer from 'react-test-renderer';
import { createServer } from 'vite';

const API = 'http://127.0.0.1:8000';
const SESSION_KEY = 'videopril.session.v1';
const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

let vite;
let App;
const originalConsoleError = console.error;

console.error = (...args) => {
  if (String(args[0]).includes('react-test-renderer is deprecated')) return;
  originalConsoleError(...args);
};

after(() => {
  console.error = originalConsoleError;
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function response(data, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => data };
}

function flush() {
  return Promise.resolve().then(() => Promise.resolve());
}

function project(id, title = `Project ${id}`) {
  return {
    project_id: id,
    video_id: id,
    source_exists: true,
    source_filename: `${title}.mp4`,
    title,
    video: {
      filename: `${title}.mp4`,
      metadata: { duration: 12, width: 720, height: 1280, fps: 30, size_bytes: 1024 },
    },
    editor: {
      segments: [{ start: 0, end: 3, label: title }],
      selected: [0],
      clip_results: [],
      export_padding: 0.5,
    },
    transcription: {},
  };
}

function restoredSession(id) {
  return {
    projectId: id,
    video: {
      video_id: id,
      filename: `Cached ${id}.mp4`,
      metadata: { duration: 8, width: 720, height: 1280, fps: 30, size_bytes: 512 },
    },
    segments: [{ start: 0, end: 2, label: `Cached ${id}` }],
    selected: [0],
  };
}

function installBrowser(t, session = null) {
  const originals = new Map();
  const replaceGlobal = (name, value) => {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  };
  const values = new Map();
  const storage = {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    removeItem(key) { values.delete(key); },
    setItem(key, value) { values.set(key, String(value)); },
  };
  if (session) storage.setItem(SESSION_KEY, JSON.stringify(session));

  const intervals = new Map();
  let nextInterval = 1;
  const setTrackedInterval = (callback) => {
    const id = nextInterval;
    nextInterval += 1;
    intervals.set(id, callback);
    return id;
  };
  const clearTrackedInterval = (id) => intervals.delete(id);
  const listeners = new Map();
  const browser = {
    confirm: () => true,
    location: { href: 'http://app.test/', pathname: '/', search: '' },
    history: { replaceState() {} },
    sessionStorage: storage,
    setInterval: setTrackedInterval,
    clearInterval: clearTrackedInterval,
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type) { listeners.delete(type); },
  };

  class FakeWebSocket {
    close() {
      this.onclose?.();
    }
  }

  replaceGlobal('window', browser);
  replaceGlobal('sessionStorage', storage);
  replaceGlobal('document', {
    title: 'test',
    body: { appendChild() {}, style: {} },
    createElement() { return { click() {}, remove() {} }; },
  });
  replaceGlobal('WebSocket', FakeWebSocket);
  replaceGlobal('setInterval', setTrackedInterval);
  replaceGlobal('clearInterval', clearTrackedInterval);
  replaceGlobal('IS_REACT_ACT_ENVIRONMENT', true);

  const restore = () => {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  };

  return {
    restore,
    storage,
    async pollHealth() {
      await act(async () => {
        for (const callback of [...intervals.values()]) callback();
        await flush();
      });
    },
  };
}

function createTransport() {
  const requests = [];
  const fetchImpl = (url, options = {}) => {
    const request = { ...deferred(), options, url: String(url) };
    requests.push(request);
    const parsed = new URL(request.url);
    if (
      parsed.pathname === '/api/health'
      || parsed.pathname === '/api/projects'
      || parsed.pathname.startsWith('/api/projects/')
    ) {
      return request.promise;
    }
    if (parsed.pathname.startsWith('/api/operations')) {
      return Promise.resolve(response({ operations: [] }));
    }
    if (parsed.pathname.startsWith('/api/video/transcript/')) {
      return Promise.resolve(response({ has_transcript: false }));
    }
    if (parsed.pathname === '/api/ad/current' || parsed.pathname === '/api/banner/current') {
      return Promise.resolve(response({ exists: false }));
    }
    return Promise.resolve(response({}));
  };
  return {
    fetchImpl,
    requests,
    detailRequests(id) {
      return requests.filter(({ url }) => new URL(url).pathname === `/api/projects/${id}`);
    },
    healthRequests() {
      return requests.filter(({ url }) => new URL(url).pathname === '/api/health');
    },
  };
}

async function mountApp(t, session) {
  const browser = installBrowser(t, session);
  const transport = createTransport();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = transport.fetchImpl;

  let renderer;
  await act(async () => {
    renderer = TestRenderer.create(createElement(App));
    await flush();
  });
  t.after(async () => {
    await act(async () => { renderer.unmount(); });
    globalThis.fetch = originalFetch;
    browser.restore();
  });
  return { browser, renderer, transport };
}

async function answerLatestHealth(transport, ok) {
  const request = transport.healthRequests().at(-1);
  assert.ok(request, 'the App should check backend health');
  await act(async () => {
    request.resolve(response({}, { ok }));
    await flush();
  });
}

async function answerDetail(request, data, options) {
  await act(async () => {
    request.resolve(response(data, options));
    await flush();
  });
}

before(async () => {
  vite = await createServer({
    appType: 'custom',
    configFile: false,
    logLevel: 'error',
    root: FRONTEND_ROOT,
    server: { hmr: false, middlewareMode: true },
  });
  ({ default: App } = await vite.ssrLoadModule('/src/App.jsx'));
  after(async () => {
    await vite.close();
  });
});

test('mounted App abandons a failed restore candidate on reset before health reconnects', async (t) => {
  const { browser, renderer, transport } = await mountApp(t, restoredSession('project-a'));

  await answerLatestHealth(transport, true);
  const failedDetail = transport.detailRequests('project-a').at(-1);
  await answerDetail(failedDetail, { detail: 'temporary outage' }, { ok: false, status: 503 });

  await act(async () => {
    renderer.root.findByProps({ id: 'btn-reset-video' }).props.onClick();
    await flush();
  });
  await browser.pollHealth();
  await answerLatestHealth(transport, false);
  await browser.pollHealth();
  await answerLatestHealth(transport, true);

  assert.equal(transport.detailRequests('project-a').length, 1);
});

test('mounted App hydrates authoritative detail without auto-transcribing and does not replay after an edit', async (t) => {
  const { browser, renderer, transport } = await mountApp(t, restoredSession('project-a'));

  await answerLatestHealth(transport, true);
  assert.equal(transport.detailRequests('project-a').length, 1);
  assert.equal(transport.requests.some(({ url, options }) => (
    new URL(url).pathname === '/api/video/transcribe' && options.method === 'POST'
  )), false);

  await answerDetail(transport.detailRequests('project-a')[0], project('project-a', 'Authoritative A'));
  assert.equal(renderer.root.findByProps({ className: 'video-info-name' }).children.join(''), 'Authoritative A.mp4');
  assert.equal(transport.requests.some(({ url, options }) => (
    new URL(url).pathname === '/api/video/transcribe' && options.method === 'POST'
  )), false);

  await act(async () => {
    renderer.root.findByProps({ id: 'export-padding-input' }).props.onChange({ target: { value: '0.9' } });
    await flush();
  });
  await browser.pollHealth();
  await answerLatestHealth(transport, false);
  await browser.pollHealth();
  await answerLatestHealth(transport, true);

  assert.equal(transport.detailRequests('project-a').length, 1);
  assert.equal(renderer.root.findByProps({ id: 'export-padding-input' }).props.value, 0.9);
});

test('mounted App does not restore A after switching to another project and aborts stale detail', async (t) => {
  const { browser, renderer, transport } = await mountApp(t, restoredSession('project-a'));

  await answerLatestHealth(transport, true);
  const restoreRequest = transport.detailRequests('project-a')[0];
  await act(async () => {
    renderer.root.findByProps({ id: 'btn-reset-video' }).props.onClick();
    await flush();
  });
  assert.equal(restoreRequest.options.signal.aborted, true);

  const listRequest = transport.requests.find(({ url }) => new URL(url).pathname === '/api/projects');
  assert.ok(listRequest, 'reset should request the recent-project list while online');
  await answerDetail(listRequest, {
    diagnostics: [],
    projects: [
      { project_id: 'project-b', video_id: 'project-b', title: 'Project B', source_exists: true, transcription: {} },
      { project_id: 'project-c', video_id: 'project-c', title: 'Project C', source_exists: true, transcription: {} },
    ],
  });

  const openButtons = renderer.root.findAllByType('button').filter((button) => button.children.join('') === 'Открыть');
  await act(async () => {
    openButtons[0].props.onClick();
    await flush();
  });
  const projectBRequest = transport.detailRequests('project-b')[0];
  await act(async () => {
    renderer.root.findAllByType('button').filter((button) => button.children.join('') === 'Открыть')[1].props.onClick();
    await flush();
  });

  assert.equal(projectBRequest.options.signal.aborted, true);
  const projectCRequest = transport.detailRequests('project-c')[0];
  await answerDetail(projectCRequest, project('project-c', 'Project C'));
  await browser.pollHealth();
  await answerLatestHealth(transport, false);
  await browser.pollHealth();
  await answerLatestHealth(transport, true);

  assert.equal(transport.detailRequests('project-a').length, 1);
  assert.equal(transport.detailRequests('project-c').length, 1);
});
