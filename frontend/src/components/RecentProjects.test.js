import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';


const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
let vite;
let RecentProjects;

before(async () => {
  vite = await createServer({
    root: frontendRoot,
    appType: 'custom',
    server: { middlewareMode: true },
  });
  ({ default: RecentProjects } = await vite.ssrLoadModule('/src/components/RecentProjects.jsx'));
});

after(async () => {
  await vite?.close();
});

function recoverableProject(projectId, progressPct = 37) {
  return {
    project_id: projectId,
    title: `Проект ${projectId}`,
    source_exists: true,
    transcription: { status: 'stopped', progress_pct: progressPct, resume_available: true },
  };
}

function descendants(element, predicate, found = []) {
  if (Array.isArray(element)) {
    element.forEach((child) => descendants(child, predicate, found));
    return found;
  }
  if (!element || typeof element !== 'object' || !('props' in element)) return found;
  if (predicate(element)) found.push(element);
  descendants(element.props.children, predicate, found);
  return found;
}

function buttons(element) {
  return descendants(element, (child) => child.type === 'button');
}

function buttonWithText(element, text) {
  return buttons(element).find((button) => button.props.children === text);
}

function buttonByAriaLabel(element, label) {
  return buttons(element).find((button) => button.props['aria-label'] === label);
}

test('project-scoped pending actions disable recovery controls only for the matching card', () => {
  const first = recoverableProject('first');
  const second = recoverableProject('second', 62);
  const tree = RecentProjects({
    projects: [first, second],
    pendingAction: { projectId: 'second', action: 'resume' },
  });
  const [firstCard, secondCard] = descendants(tree, (child) => child.type === 'article');

  assert.equal(buttonByAriaLabel(firstCard, 'Продолжить с 37%').props.disabled, false);
  assert.equal(buttonWithText(firstCard, 'Начать заново').props.disabled, false);
  assert.equal(buttonByAriaLabel(secondCard, 'Продолжить с 62%').props.disabled, true);
  assert.equal(buttonWithText(secondCard, 'Начать заново').props.disabled, true);

  const scalarTree = RecentProjects({ projects: [first, second], pendingAction: 'resume' });
  for (const button of buttons(scalarTree).filter((item) => item.props['aria-label'] || item.props.children === 'Начать заново')) {
    assert.equal(button.props.disabled, false);
  }
});

test('recent project buttons use native button semantics and pass the correct project to handlers', () => {
  const project = recoverableProject('project-1');
  const opened = [];
  const resumed = [];
  const tree = RecentProjects({
    projects: [project],
    onOpen: (value) => opened.push(value),
    onResume: (value) => resumed.push(value),
  });

  const open = buttonWithText(tree, 'Открыть');
  const resume = buttonByAriaLabel(tree, 'Продолжить с 37%');
  const restart = buttonWithText(tree, 'Начать заново');
  assert.equal(open.type, 'button');
  assert.equal(resume.type, 'button');
  assert.equal(restart.type, 'button');
  assert.equal(open.props.type, 'button');
  assert.equal(resume.props.type, 'button');
  assert.equal(restart.props.type, 'button');
  assert.equal(resume.props['aria-label'], 'Продолжить с 37%');
  assert.equal(resume.props.disabled, false);

  open.props.onClick();
  resume.props.onClick();
  assert.deepEqual(opened, [project]);
  assert.deepEqual(resumed, [project]);
});

test('a missing-source card exposes details only and preserves project identity', () => {
  const project = { project_id: 'missing', title: 'Нет файла', source_exists: false };
  const opened = [];
  const tree = RecentProjects({ projects: [project], onOpen: (value) => opened.push(value) });

  const details = buttonWithText(tree, 'Детали проекта');
  assert.equal(details.type, 'button');
  assert.equal(details.props.type, 'button');
  assert.equal(buttons(tree).length, 2);
  details.props.onClick();
  assert.deepEqual(opened, [project]);
});

test('restart calls its handler only after native confirmation', () => {
  const project = recoverableProject('restart');
  const restarted = [];
  const previousWindow = globalThis.window;
  const restartTree = RecentProjects({ projects: [project], onRestart: (value) => restarted.push(value) });
  const restart = buttonWithText(restartTree, 'Начать заново');

  try {
    globalThis.window = { confirm: () => false };
    restart.props.onClick();
    assert.deepEqual(restarted, []);

    globalThis.window = { confirm: () => true };
    restart.props.onClick();
    assert.deepEqual(restarted, [project]);
  } finally {
    globalThis.window = previousWindow;
  }
});

test('delete requires explicit confirmation and passes only the selected project', () => {
  const first = recoverableProject('first');
  const second = recoverableProject('second');
  const deleted = [];
  const previousWindow = globalThis.window;
  const tree = RecentProjects({
    projects: [first, second],
    onDelete: (value) => deleted.push(value),
  });
  const [firstCard, secondCard] = descendants(tree, (child) => child.type === 'article');

  try {
    globalThis.window = { confirm: () => false };
    buttonWithText(firstCard, 'Удалить').props.onClick();
    assert.deepEqual(deleted, []);

    globalThis.window = { confirm: () => true };
    buttonWithText(secondCard, 'Удалить').props.onClick();
    assert.deepEqual(deleted, [second]);
  } finally {
    globalThis.window = previousWindow;
  }
});
