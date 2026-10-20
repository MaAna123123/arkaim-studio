const SESSION_KEY = 'videopril.session.v1';

const PROJECT_AUTOSAVE_DELAY_MS = 700;

function cloneEditorValue(value) {
  if (Array.isArray(value)) return value.map(cloneEditorValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneEditorValue(item)]),
    );
  }
  return value;
}

export function buildHydratedProjectVideo(project, mappedVideo) {
  const source = mappedVideo && typeof mappedVideo === 'object' ? mappedVideo : {};
  const {
    video_id: ignoredVideoId,
    title: ignoredTitle,
    filename: ignoredFilename,
    video_url: ignoredVideoUrl,
    metadata: nestedMetadata,
    ...flatMetadata
  } = source;
  const filename = project?.source_filename || project?.filename || '';
  const extension = filename.match(/\.[a-z0-9]+$/i)?.[0]?.toLowerCase() || '';
  const videoId = project?.video_id ?? project?.project_id;
  return {
    ...source,
    metadata: {
      ...flatMetadata,
      ...(nestedMetadata && typeof nestedMetadata === 'object' ? nestedMetadata : {}),
    },
    ...(filename ? { filename } : {}),
    ...(videoId && extension ? { video_url: `/uploads/${videoId}${extension}` } : {}),
  };
}

export function buildProjectEditorPatch(editor = {}) {
  return {
    segments: cloneEditorValue(editor.segments),
    selected: editor.selected instanceof Set ? [...editor.selected] : editor.selected,
    clip_results: editor.clipResults,
    subtitle_context: editor.subtitleContext,
    subtitle_notice: editor.subtitleNotice,
    export_padding: editor.exportPadding,
    aspect_ratio: editor.aspectRatio,
    burn_subtitles: editor.burnSubtitles,
    insert_ad: editor.insertAd,
    text_ad_enabled: editor.textAdEnabled,
    text_ad_text: editor.textAdText,
    text_ad_position: editor.textAdPosition,
    text_ad_interval: editor.textAdInterval,
    text_ad_opacity: editor.textAdOpacity,
    video_banner_enabled: editor.videoBannerEnabled,
    video_banner_height: editor.videoBannerHeight,
  };
}

export function canAutosaveProject(projectId, hydratedProjectId) {
  return Boolean(projectId) && projectId === hydratedProjectId;
}

export function createProjectAutosaveScheduler({
  save,
  delay = PROJECT_AUTOSAVE_DELAY_MS,
  setTimer = (callback, timeout) => setTimeout(callback, timeout),
  clearTimer = (timer) => clearTimeout(timer),
}) {
  let timer = null;

  const cancel = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };

  return {
    cancel,
    schedule({ projectId, hydratedProjectId, editor }) {
      cancel();
      if (!canAutosaveProject(projectId, hydratedProjectId)) return false;
      const payload = buildProjectEditorPatch(editor);
      timer = setTimer(() => {
        timer = null;
        save(projectId, payload);
      }, delay);
      return true;
    },
  };
}


export function clearSession(storage) {
  try {
    storage?.removeItem(SESSION_KEY);
  } catch {
    // Session persistence is optional.
  }
}

export function loadSession(storage) {
  try {
    const raw = storage?.getItem(SESSION_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return {};
    if (parsed.video && typeof parsed.video !== 'object') {
      clearSession(storage);
      return {};
    }
    return parsed;
  } catch {
    clearSession(storage);
    return {};
  }
}

export function saveSession(storage, session) {
  try {
    storage?.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // The active editor continues even when browser storage is unavailable.
  }
}
