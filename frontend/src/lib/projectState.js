import { normalizeCropRegion, normalizeWebcamHeightRatio } from './streamerLayout.js';
import { normalizeComposition } from './clipComposition.js';

const DEFAULT_EDITOR_STATE = {
  segments: [],
  selected: [],
  clip_results: [],
  subtitle_context: null,
  subtitle_notice: '',
  export_padding: 0.5,
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
  streamer_layout_enabled: false,
  webcam_region: null,
  gameplay_region: null,
  webcam_height_ratio: 0.35,
};

function objectOrEmpty(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function listOrEmpty(value) {
  return Array.isArray(value) ? value : [];
}

function cloneProjectValue(value) {
  if (Array.isArray(value)) return value.map(cloneProjectValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneProjectValue(item)]),
    );
  }
  return value;
}

function numberOrDefault(value, fallback) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function booleanOrDefault(value, fallback) {
  return typeof value === 'boolean' ? value : fallback;
}

function normalizeSegments(value, sourceDuration) {
  return listOrEmpty(value).map((item) => {
    const segment = cloneProjectValue(objectOrEmpty(item));
    if (segment.composition == null) return segment;
    const composition = normalizeComposition(segment.composition, segment, sourceDuration);
    if (composition) {
      return {
        ...segment,
        start: composition.source_start,
        end: composition.source_end,
        composition,
      };
    }
    delete segment.composition;
    return segment;
  });
}

export function mapProjectToEditorState(project) {
  const source = objectOrEmpty(project);
  const editor = objectOrEmpty(source.editor);
  const savedVideo = cloneProjectValue(objectOrEmpty(source.video));
  const videoId = source.video_id ?? source.project_id ?? savedVideo.video_id;
  const title = source.title ?? savedVideo.title;
  const sourceDuration = numberOrDefault(
    objectOrEmpty(savedVideo.metadata).duration,
    numberOrDefault(savedVideo.duration, 0),
  );
  const video = videoId || title || Object.keys(savedVideo).length
    ? { ...savedVideo, ...(videoId ? { video_id: videoId } : {}), ...(title ? { title } : {}) }
    : null;

  return {
    projectId: source.project_id ?? null,
    video,
    transcript: source.has_transcript === true ? { has_transcript: true } : null,
    subtitleContext: editor.subtitle_context == null
      ? DEFAULT_EDITOR_STATE.subtitle_context
      : cloneProjectValue(editor.subtitle_context),
    subtitleNotice: typeof editor.subtitle_notice === 'string'
      ? editor.subtitle_notice
      : DEFAULT_EDITOR_STATE.subtitle_notice,
    segments: normalizeSegments(editor.segments, sourceDuration),
    selected: new Set(cloneProjectValue(listOrEmpty(editor.selected))),
    clipResults: cloneProjectValue(listOrEmpty(editor.clip_results)),
    exportPadding: numberOrDefault(editor.export_padding, DEFAULT_EDITOR_STATE.export_padding),
    aspectRatio: typeof editor.aspect_ratio === 'string' && editor.aspect_ratio
      ? editor.aspect_ratio
      : DEFAULT_EDITOR_STATE.aspect_ratio,
    burnSubtitles: booleanOrDefault(editor.burn_subtitles, DEFAULT_EDITOR_STATE.burn_subtitles),
    insertAd: booleanOrDefault(editor.insert_ad, DEFAULT_EDITOR_STATE.insert_ad),
    textAdEnabled: booleanOrDefault(editor.text_ad_enabled, DEFAULT_EDITOR_STATE.text_ad_enabled),
    textAdText: typeof editor.text_ad_text === 'string'
      ? editor.text_ad_text
      : DEFAULT_EDITOR_STATE.text_ad_text,
    textAdPosition: typeof editor.text_ad_position === 'string' && editor.text_ad_position
      ? editor.text_ad_position
      : DEFAULT_EDITOR_STATE.text_ad_position,
    textAdInterval: numberOrDefault(editor.text_ad_interval, DEFAULT_EDITOR_STATE.text_ad_interval),
    textAdOpacity: numberOrDefault(editor.text_ad_opacity, DEFAULT_EDITOR_STATE.text_ad_opacity),
    videoBannerEnabled: booleanOrDefault(
      editor.video_banner_enabled,
      DEFAULT_EDITOR_STATE.video_banner_enabled,
    ),
    videoBannerHeight: numberOrDefault(
      editor.video_banner_height,
      DEFAULT_EDITOR_STATE.video_banner_height,
    ),
    streamerLayoutEnabled: booleanOrDefault(
      editor.streamer_layout_enabled,
      DEFAULT_EDITOR_STATE.streamer_layout_enabled,
    ),
    webcamRegion: normalizeCropRegion(editor.webcam_region),
    gameplayRegion: normalizeCropRegion(editor.gameplay_region),
    webcamHeightRatio: normalizeWebcamHeightRatio(
      editor.webcam_height_ratio,
      DEFAULT_EDITOR_STATE.webcam_height_ratio,
    ),
    transcription: cloneProjectValue(objectOrEmpty(source.transcription)),
    shouldStartTranscription: false,
  };
}

export function formatProjectProgress(project) {
  const transcription = objectOrEmpty(objectOrEmpty(project).transcription);
  const rawProgress = transcription.progress_pct;
  if (typeof rawProgress !== 'number' && typeof rawProgress !== 'string') return null;
  if (typeof rawProgress === 'string' && rawProgress.trim() === '') return null;
  const progress = Number(rawProgress);
  if (!Number.isFinite(progress) || progress < 0 || progress > 100) return null;
  return Math.round(progress);
}

function cardState(kind, label, actions, project, options = {}) {
  const pending = Boolean(options.pendingAction || objectOrEmpty(project.transcription).pending_action);
  const progress = formatProjectProgress(project);
  return {
    kind,
    label,
    progress,
    actions,
    actionDisabled: {
      resume: Boolean(actions.resume && pending),
      restart: Boolean(actions.restart && pending),
    },
    resumeLabel: progress === null ? 'Продолжить' : `Продолжить с ${progress}%`,
  };
}

export function getProjectCardState(project, options = {}) {
  const source = objectOrEmpty(project);
  const transcription = objectOrEmpty(source.transcription);
  const status = transcription.status;
  const normalActions = { details: true, open: true, resume: false, restart: false };

  if (source.source_exists === false) {
    return cardState(
      'missing-source',
      'Исходный файл не найден',
      { details: true, open: false, resume: false, restart: false },
      source,
      options,
    );
  }

  if (transcription.diagnostic) {
    const diagnostic = objectOrEmpty(transcription.diagnostic);
    return cardState(
      'damaged-checkpoint',
      typeof diagnostic.message === 'string' && diagnostic.message
        ? diagnostic.message
        : 'Сохранённый прогресс нельзя продолжить',
      { ...normalActions, restart: true },
      source,
      options,
    );
  }

  if (status === 'queued' || status === 'running') {
    return cardState('running', status === 'queued' ? 'В очереди' : 'Распознавание выполняется', normalActions, source, options);
  }

  if (status === 'paused' || status === 'stopped' || status === 'recovered') {
    return cardState(
      'recoverable',
      'Прогресс сохранён',
      { ...normalActions, resume: transcription.resume_available !== false, restart: true },
      source,
      options,
    );
  }

  if (status === 'failed') {
    return cardState('failed', 'Транскрипция завершилась с ошибкой', { ...normalActions, restart: true }, source, options);
  }

  if (source.has_transcript === true) {
    const clipsCount = Number.isFinite(Number(source.clips_count)) ? Number(source.clips_count) : 0;
    return cardState('completed', `Транскрипт готов · клипов: ${clipsCount}`, normalActions, source, options);
  }

  return cardState('uploaded', 'Видео загружено', normalActions, source, options);
}
