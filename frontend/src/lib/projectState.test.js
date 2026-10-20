import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatProjectProgress,
  getProjectCardState,
  mapProjectToEditorState,
} from './projectState.js';
import { createDefaultComposition } from './clipComposition.js';


test('project details hydrate the editor with a Set selection and saved options', () => {
  const state = mapProjectToEditorState({
    project_id: 'project-1',
    video_id: 'video-1',
    title: 'Интервью',
    video: { duration: 90 },
    has_transcript: true,
    editor: {
      segments: [{ start: 4, end: 8 }],
      selected: [0, 3],
      clip_results: [{ filename: 'clip.mp4' }],
      subtitle_context: { type: 'srt' },
      subtitle_notice: 'Субтитры восстановлены',
      export_padding: 1,
      aspect_ratio: '1:1',
      burn_subtitles: false,
      insert_ad: true,
      text_ad_enabled: true,
      text_ad_text: 'Подпишитесь',
      text_ad_position: 'bottom',
      text_ad_interval: 7,
      text_ad_opacity: 0.5,
      video_banner_enabled: true,
      video_banner_height: 240,
    },
  });

  assert.equal(state.projectId, 'project-1');
  assert.deepEqual(state.video, { duration: 90, video_id: 'video-1', title: 'Интервью' });
  assert.deepEqual(state.segments, [{ start: 4, end: 8 }]);
  assert.deepEqual([...state.selected], [0, 3]);
  assert.ok(state.selected instanceof Set);
  assert.deepEqual(state.clipResults, [{ filename: 'clip.mp4' }]);
  assert.equal(state.subtitleContext.type, 'srt');
  assert.equal(state.subtitleNotice, 'Субтитры восстановлены');
  assert.equal(state.exportPadding, 1);
  assert.equal(state.aspectRatio, '1:1');
  assert.equal(state.burnSubtitles, false);
  assert.equal(state.insertAd, true);
  assert.equal(state.textAdEnabled, true);
  assert.equal(state.textAdText, 'Подпишитесь');
  assert.equal(state.textAdPosition, 'bottom');
  assert.equal(state.textAdInterval, 7);
  assert.equal(state.textAdOpacity, 0.5);
  assert.equal(state.videoBannerEnabled, true);
  assert.equal(state.videoBannerHeight, 240);
  assert.deepEqual(state.transcript, { has_transcript: true });
});

test('older incomplete project records receive safe editor defaults and never autostart Whisper', () => {
  const state = mapProjectToEditorState({ project_id: 'legacy-project' });

  assert.equal(state.projectId, 'legacy-project');
  assert.deepEqual(state.segments, []);
  assert.deepEqual([...state.selected], []);
  assert.deepEqual(state.clipResults, []);
  assert.equal(state.subtitleContext, null);
  assert.equal(state.subtitleNotice, '');
  assert.equal(state.exportPadding, 0.5);
  assert.equal(state.aspectRatio, '9:16');
  assert.equal(state.burnSubtitles, true);
  assert.equal(state.insertAd, false);
  assert.equal(state.textAdEnabled, false);
  assert.equal(state.textAdText, '');
  assert.equal(state.textAdPosition, 'top');
  assert.equal(state.textAdInterval, 4);
  assert.equal(state.textAdOpacity, 0.75);
  assert.equal(state.videoBannerEnabled, false);
  assert.equal(state.videoBannerHeight, 380);
  assert.equal(state.shouldStartTranscription, false);
});

test('project hydration returns independent mutable editor data', () => {
  const project = {
    video_id: 'video-1',
    video: { metadata: { duration: 90 } },
    transcription: { status: 'stopped', partial_result: { checkpoint: 12 } },
    editor: {
      segments: [{ start: 4, words: [{ text: 'Привет' }] }],
      selected: [0],
      clip_results: [{ filename: 'clip.mp4', metadata: { size: 12 } }],
      subtitle_context: { source: { language: 'ru' } },
    },
  };

  const state = mapProjectToEditorState(project);
  state.video.metadata.duration = 120;
  state.segments[0].words[0].text = 'Изменено';
  state.clipResults[0].metadata.size = 99;
  state.subtitleContext.source.language = 'en';
  state.transcription.partial_result.checkpoint = 33;

  assert.equal(project.video.metadata.duration, 90);
  assert.equal(project.editor.segments[0].words[0].text, 'Привет');
  assert.equal(project.editor.clip_results[0].metadata.size, 12);
  assert.equal(project.editor.subtitle_context.source.language, 'ru');
  assert.equal(project.transcription.partial_result.checkpoint, 12);
});

test('project hydration normalizes independent per-clip compositions', () => {
  const firstComposition = createDefaultComposition({ start: 4, end: 8 }, '9:16');
  const secondComposition = createDefaultComposition({ start: 20, end: 30 }, '9:16');
  secondComposition.main_layer.destination = { x: 0, y: 0.3, width: 1, height: 0.7 };
  const project = {
    video: { metadata: { duration: 90 } },
    editor: {
      segments: [
        { start: 4, end: 8, composition: firstComposition },
        { start: 20, end: 30, composition: secondComposition },
      ],
    },
  };

  const state = mapProjectToEditorState(project);
  state.segments[0].composition.main_layer.destination.y = 0.4;

  assert.equal(state.segments[1].composition.main_layer.destination.y, 0.3);
  assert.equal(project.editor.segments[0].composition.main_layer.destination.y, 0);
});

test('malformed saved composition is dropped without losing its segment', () => {
  const state = mapProjectToEditorState({
    video: { duration: 60 },
    editor: {
      segments: [{
        start: 4,
        end: 8,
        label: 'Legacy moment',
        composition: { source_start: 8, source_end: 4 },
      }],
    },
  });

  assert.equal(state.segments.length, 1);
  assert.equal(state.segments[0].label, 'Legacy moment');
  assert.equal('composition' in state.segments[0], false);
});

test('missing source wins card precedence while details remain available', () => {
  const state = getProjectCardState({
    source_exists: false,
    has_transcript: true,
    transcription: { status: 'stopped', progress_pct: 43, resume_available: true },
  });

  assert.equal(state.kind, 'missing-source');
  assert.equal(state.label, 'Исходный файл не найден');
  assert.equal(state.actions.details, true);
  assert.equal(state.actions.open, false);
  assert.equal(state.actions.resume, false);
  assert.equal(state.actions.restart, false);
});

test('damaged checkpoints disable continuation but retain project opening and restart', () => {
  const state = getProjectCardState({
    source_exists: true,
    transcription: {
      resume_available: false,
      diagnostic: { message: 'Сохранённый прогресс нельзя продолжить' },
    },
  });

  assert.equal(state.kind, 'damaged-checkpoint');
  assert.equal(state.label, 'Сохранённый прогресс нельзя продолжить');
  assert.deepEqual(state.actions, { details: true, open: true, resume: false, restart: true });
});

test('card states use the documented action precedence for active, recoverable, failed, completed, and uploaded projects', () => {
  const cases = [
    [{ transcription: { status: 'running', progress_pct: 42 } }, 'running', { details: true, open: true, resume: false, restart: false }],
    [{ transcription: { status: 'queued', progress_pct: 0 } }, 'running', { details: true, open: true, resume: false, restart: false }],
    [{ transcription: { status: 'paused', progress_pct: 42, resume_available: true } }, 'recoverable', { details: true, open: true, resume: true, restart: true }],
    [{ transcription: { status: 'stopped', progress_pct: 42, resume_available: true } }, 'recoverable', { details: true, open: true, resume: true, restart: true }],
    [{ transcription: { status: 'recovered', progress_pct: 42, resume_available: true } }, 'recoverable', { details: true, open: true, resume: true, restart: true }],
    [{ transcription: { status: 'failed', progress_pct: 42 } }, 'failed', { details: true, open: true, resume: false, restart: true }],
    [{ has_transcript: true, clips_count: 2 }, 'completed', { details: true, open: true, resume: false, restart: false }],
    [{}, 'uploaded', { details: true, open: true, resume: false, restart: false }],
  ];

  for (const [project, kind, actions] of cases) {
    const state = getProjectCardState(project);
    assert.equal(state.kind, kind);
    assert.deepEqual(state.actions, actions);
  }
});

test('pending actions disable only duplicate recovery commands and preserve the exact resume label', () => {
  const state = getProjectCardState({
    transcription: { status: 'stopped', progress_pct: 37, resume_available: true },
  }, { pendingAction: 'resume' });

  assert.equal(state.resumeLabel, 'Продолжить с 37%');
  assert.equal(state.actions.open, true);
  assert.equal(state.actions.resume, true);
  assert.equal(state.actions.restart, true);
  assert.equal(state.actionDisabled.resume, true);
  assert.equal(state.actionDisabled.restart, true);
  assert.equal(formatProjectProgress({ transcription: { progress_pct: 37.6 } }), 38);
});

test('progress accepts only valid numeric values and keeps unknown checkpoints out of resume labels', () => {
  const unknownValues = [undefined, null, '', '   ', false, true, -1, 101];
  for (const progressPct of unknownValues) {
    const project = progressPct === undefined
      ? { transcription: { status: 'stopped', resume_available: true } }
      : { transcription: { status: 'stopped', progress_pct: progressPct, resume_available: true } };
    assert.equal(formatProjectProgress(project), null);
    assert.equal(getProjectCardState(project).resumeLabel, 'Продолжить');
  }

  assert.equal(formatProjectProgress({ transcription: { progress_pct: '37.6' } }), 38);
  assert.equal(formatProjectProgress({ transcription: { progress_pct: 37.4 } }), 37);
  assert.equal(
    getProjectCardState({ transcription: { status: 'stopped', progress_pct: 37.6, resume_available: true } }).resumeLabel,
    'Продолжить с 38%',
  );
});
