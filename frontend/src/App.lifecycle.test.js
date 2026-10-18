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
  const timeouts = new Map();
  let clock = 0;
  let nextTimeout = 1;
  const setTrackedTimeout = (callback, delay = 0) => {
    const id = nextTimeout;
    nextTimeout += 1;
    timeouts.set(id, {
      callback,
      due: clock + Math.max(0, Number(delay) || 0),
    });
    return id;
  };
  const clearTrackedTimeout = (id) => timeouts.delete(id);
  const listeners = new Map();
  const browser = {
    confirm: () => true,
    location: { href: 'http://app.test/', pathname: '/', search: '' },
    history: { replaceState() {} },
    sessionStorage: storage,
    setInterval: setTrackedInterval,
    clearInterval: clearTrackedInterval,
    setTimeout: setTrackedTimeout,
    clearTimeout: clearTrackedTimeout,
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
  replaceGlobal('setTimeout', setTrackedTimeout);
  replaceGlobal('clearTimeout', clearTrackedTimeout);
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
    async advanceTimers(milliseconds) {
      const target = clock + milliseconds;
      while (true) {
        const due = [...timeouts.entries()]
          .filter(([, timer]) => timer.due <= target)
          .sort(([, first], [, second]) => first.due - second.due)[0];
        if (!due) break;
        const [id, timer] = due;
        timeouts.delete(id);
        clock = timer.due;
        await act(async () => {
          timer.callback();
          await flush();
        });
      }
      clock = target;
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
    if (parsed.pathname.startsWith('/api/operations') && options.method !== 'POST') {
      return Promise.resolve(response({ operations: [] }));
    }
    if (parsed.pathname.startsWith('/api/operations')) {
      return request.promise;
    }
    if (parsed.pathname === '/api/video/upload') {
      return request.promise;
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
    requestsFor(pathname, method) {
      return requests.filter((request) => (
        new URL(request.url).pathname === pathname
        && (!method || request.options.method === method)
      ));
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

function recentProject(id, title = `Project ${id}`, transcription = {}) {
  return {
    project_id: id,
    video_id: id,
    title,
    source_exists: true,
    transcription,
  };
}

function buttonByText(renderer, text, index = 0) {
  const buttons = renderer.root.findAllByType('button').filter(
    (button) => button.children.join('') === text,
  );
  assert.ok(buttons[index], `expected visible button: ${text}`);
  return buttons[index];
}

async function answerRecentProjects(transport, projects) {
  const request = transport.requestsFor('/api/projects').at(-1);
  assert.ok(request, 'the App should request recent projects');
  await answerDetail(request, { diagnostics: [], projects });
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

test('mounted App adopts a recovered operation and delegates Continue only to its recovered id', async (t) => {
  const { renderer, transport } = await mountApp(t);

  await answerLatestHealth(transport, true);
  await answerRecentProjects(transport, [recentProject('project-a', 'Recovered A', {
    operation_id: 'operation-a',
    operation_type: 'transcription',
    video_id: 'project-a',
    status: 'stopped',
    progress_pct: 42,
    resume_available: true,
  })]);

  await act(async () => {
    buttonByText(renderer, 'Продолжить с 42%').props.onClick();
    await flush();
  });
  const detail = transport.detailRequests('project-a').at(-1);
  const recoveredDetail = {
    ...project('project-a', 'Recovered A'),
    transcription: {
      operation_id: 'operation-a',
      operation_type: 'transcription',
      video_id: 'project-a',
      status: 'stopped',
      progress_pct: 42,
      resume_available: true,
    },
  };
  await answerDetail(detail, recoveredDetail);

  const resumeRequests = transport.requestsFor('/api/operations/operation-a/resume', 'POST');
  assert.equal(resumeRequests.length, 1, 'Continue must post one recovered operation resume request');
  assert.equal(transport.requests.some(({ url, options }) => (
    new URL(url).pathname === '/api/video/transcribe' && options.method === 'POST'
  )), false, 'hydration must not auto-start transcription');

  await answerDetail(resumeRequests[0], {
    operation_id: 'operation-other',
    operation_type: 'transcription',
    video_id: 'project-a',
    status: 'running',
  });

  assert.equal(
    renderer.root.findByProps({ className: 'operation-status operation-status-stopped' }).children.join(''),
    'Остановлено — прогресс сохранён',
    'a mismatched success must not replace the recovered operation',
  );
  assert.match(
    renderer.root.findByProps({ role: 'alert' }).children.join(''),
    /сохранённая операция устарела/,
  );
});

test('mounted App only autosaves an editor change after 700ms and cancels pending saves on reset and unmount', async (t) => {
  const { browser, renderer, transport } = await mountApp(t);

  await answerLatestHealth(transport, true);
  await answerRecentProjects(transport, [
    recentProject('project-a', 'Autosave A'),
    recentProject('project-b', 'Autosave B'),
  ]);
  await act(async () => {
    buttonByText(renderer, 'Открыть').props.onClick();
    await flush();
  });
  await answerDetail(transport.detailRequests('project-a').at(-1), project('project-a', 'Autosave A'));

  await browser.advanceTimers(700);
  assert.equal(transport.requestsFor('/api/projects/project-a', 'PATCH').length, 0, 'hydration defaults must not be saved');

  await act(async () => {
    renderer.root.findByProps({ id: 'export-padding-input' }).props.onChange({ target: { value: '0.9' } });
    await flush();
  });
  await browser.advanceTimers(699);
  assert.equal(transport.requestsFor('/api/projects/project-a', 'PATCH').length, 0);
  await browser.advanceTimers(1);

  const firstPatch = transport.requestsFor('/api/projects/project-a', 'PATCH')[0];
  assert.ok(firstPatch, 'an editor change should autosave after the configured delay');
  assert.deepEqual(JSON.parse(firstPatch.options.body), {
    segments: [{ start: 0, end: 3, label: 'Autosave A' }],
    selected: [0],
    clip_results: [],
    subtitle_context: null,
    subtitle_notice: '',
    export_padding: 0.9,
    aspect_ratio: '9:16',
    burn_subtitles: true,
    insert_ad: false,
    text_ad_enabled: false,
    text_ad_text: '',
    text_ad_position: 'top',
    text_ad_interval: 4,
    text_ad_opacity: 0.75,
    video_banner_enabled: false,
    video_banner_height: 380,
  });
  await answerDetail(firstPatch, {});

  await act(async () => {
    renderer.root.findByProps({ id: 'export-padding-input' }).props.onChange({ target: { value: '1.1' } });
    await flush();
  });
  await browser.advanceTimers(700);
  const resetPatch = transport.requestsFor('/api/projects/project-a', 'PATCH')[1];
  assert.ok(resetPatch, 'the second edit should have an in-flight autosave to cancel');
  await act(async () => {
    renderer.root.findByProps({ id: 'btn-reset-video' }).props.onClick();
    await flush();
  });
  assert.equal(resetPatch.options.signal.aborted, true, 'reset must abort an in-flight autosave');

  await answerRecentProjects(transport, [recentProject('project-b', 'Autosave B')]);
  await act(async () => {
    buttonByText(renderer, 'Открыть').props.onClick();
    await flush();
  });
  await answerDetail(transport.detailRequests('project-b').at(-1), project('project-b', 'Autosave B'));
  await act(async () => {
    renderer.root.findByProps({ id: 'export-padding-input' }).props.onChange({ target: { value: '1.3' } });
    await flush();
  });
  await act(async () => {
    renderer.unmount();
    await flush();
  });
  await browser.advanceTimers(700);
  assert.equal(transport.requestsFor('/api/projects/project-b', 'PATCH').length, 0, 'unmount must cancel an unsent autosave');
});

test('mounted App scopes restart pending state to the project and ignores late restart snapshots', async (t) => {
  const { renderer, transport } = await mountApp(t);
  const projects = [
    recentProject('project-a', 'Restart A', { status: 'failed' }),
    recentProject('project-b', 'Restart B', { status: 'failed' }),
  ];

  await answerLatestHealth(transport, true);
  await answerRecentProjects(transport, projects);
  await act(async () => {
    buttonByText(renderer, 'Начать заново', 0).props.onClick();
    await flush();
  });
  const restartA = transport.requestsFor('/api/projects/project-a/transcription/restart', 'POST')[0];
  assert.ok(restartA, 'restart should use the selected project endpoint');

  await act(async () => {
    buttonByText(renderer, 'Начать заново', 1).props.onClick();
    await flush();
  });
  const restartB = transport.requestsFor('/api/projects/project-b/transcription/restart', 'POST')[0];
  assert.ok(restartB, 'the second project should receive its own restart request');
  assert.equal(restartA.options.signal.aborted, true, 'starting B must abort A restart');
  assert.equal(buttonByText(renderer, 'Начать заново', 1).props.disabled, true, 'only B remains pending');

  await answerDetail(restartA, {
    operation_id: 'operation-a',
    operation_type: 'transcription',
    video_id: 'project-a',
    status: 'queued',
  });
  assert.equal(buttonByText(renderer, 'Начать заново', 1).props.disabled, true, 'late A cannot clear B pending state');

  await act(async () => {
    buttonByText(renderer, 'Открыть', 1).props.onClick();
    await flush();
  });
  assert.equal(restartB.options.signal.aborted, true, 'switching to B must abort B restart');
  await answerDetail(transport.detailRequests('project-b').at(-1), project('project-b', 'Restart B'));
  await act(async () => {
    renderer.root.findByProps({ id: 'btn-reset-video' }).props.onClick();
    await flush();
  });
  await answerRecentProjects(transport, projects);

  await act(async () => {
    buttonByText(renderer, 'Начать заново', 1).props.onClick();
    await flush();
  });
  const acceptedRestart = transport.requestsFor('/api/projects/project-b/transcription/restart', 'POST').at(-1);
  await answerDetail(acceptedRestart, {
    operation_id: 'operation-b',
    operation_type: 'transcription',
    video_id: 'project-b',
    status: 'queued',
  });
  assert.equal(
    renderer.root.findAllByProps({ className: 'recent-project-status recent-project-status-running' })
      .some((node) => node.children.join('') === 'В очереди'),
    true,
    'only the correctly identified restart snapshot updates its project',
  );
});

test('mounted App discards a retryable startup restore candidate when a local upload replaces it', async (t) => {
  const { browser, renderer, transport } = await mountApp(t, restoredSession('project-a'));

  await answerLatestHealth(transport, true);
  await answerDetail(
    transport.detailRequests('project-a').at(-1),
    { detail: 'temporary outage' },
    { ok: false, status: 503 },
  );

  const replacementInput = renderer.root.findAllByType('input').find(
    (input) => input.props.type === 'file'
      && input.props.accept === 'video/*'
      && typeof input.props.onChange === 'function',
  );
  assert.ok(replacementInput, 'loaded video should expose the local replacement upload input');
  await act(async () => {
    replacementInput.props.onChange({
      target: { files: [new File(['replacement'], 'replacement-b.mp4', { type: 'video/mp4' })] },
    });
    await flush();
  });
  const upload = transport.requestsFor('/api/video/upload', 'POST')[0];
  assert.ok(upload, 'local file selection must use the production upload path');
  await answerDetail(upload, {
    video_id: 'project-b',
    filename: 'Replacement B.mp4',
    metadata: { duration: 7, width: 720, height: 1280, fps: 30, size_bytes: 128 },
  });
  await act(async () => {
    await flush();
  });

  await browser.pollHealth();
  await answerLatestHealth(transport, false);
  await browser.pollHealth();
  await answerLatestHealth(transport, true);
  assert.equal(transport.detailRequests('project-a').length, 1, 'a reconnect must not restore the replaced startup project');
  assert.equal(renderer.root.findByProps({ className: 'video-info-name' }).children.join(''), 'Replacement B.mp4');
});

test('mounted App does not retry failed startup A after the user explicitly opens B', async (t) => {
  const { browser, renderer, transport } = await mountApp(t, restoredSession('project-a'));

  await answerLatestHealth(transport, true);
  await answerDetail(
    transport.detailRequests('project-a').at(-1),
    { detail: 'temporary outage' },
    { ok: false, status: 503 },
  );
  await act(async () => {
    renderer.root.findByProps({ id: 'btn-reset-video' }).props.onClick();
    await flush();
  });
  await answerRecentProjects(transport, [recentProject('project-b', 'Explicit B')]);
  await act(async () => {
    buttonByText(renderer, 'Открыть').props.onClick();
    await flush();
  });
  await answerDetail(transport.detailRequests('project-b').at(-1), project('project-b', 'Explicit B'));

  await browser.pollHealth();
  await answerLatestHealth(transport, false);
  await browser.pollHealth();
  await answerLatestHealth(transport, true);
  assert.equal(transport.detailRequests('project-a').length, 1, 'the explicit B navigation must win over the retryable startup candidate');
  assert.equal(renderer.root.findByProps({ className: 'video-info-name' }).children.join(''), 'Explicit B.mp4');
});
