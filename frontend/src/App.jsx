import { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';

import Header from './components/Header';
import VideoUploader from './components/VideoUploader';
import UrlInput from './components/UrlInput';
import PromptSelector from './components/PromptSelector';
import VideoPlayer from './components/VideoPlayer';
import HighlightList from './components/HighlightList';
import TranscriptView from './components/TranscriptView';
import SegmentEditor from './components/SegmentEditor';
import ExportPanel from './components/ExportPanel';
import SettingsModal from './components/SettingsModal';
import AdManager from './components/AdManager';
import SocialSharePanel from './components/SocialSharePanel';
import OperationPanel from './components/OperationPanel';
import RecentProjects from './components/RecentProjects';
import useOperation, { isUnfinishedTranscription } from './hooks/useOperation';
import { mapProjectToEditorState } from './lib/projectState';
import { serializeExportSegment } from './lib/clipComposition';
import {
  canUseRecentProjects,
  createProjectLifecycleController,
} from './lib/projectLifecycle';
import {
  buildHydratedProjectVideo,
  clearSession,
  createProjectAutosaveScheduler,
  loadSession,
  saveSession,
} from './lib/sessionStorage';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const SESSION_KEY = 'videopril.session.v1';
function loadStoredSession() {
  if (typeof window !== 'undefined' && window.location.search.includes('reset=1')) {
    clearSession(window.sessionStorage);
    window.history.replaceState({}, document.title, window.location.pathname);
    return {};
  }
  return loadSession(window.sessionStorage);
}

function recoveredOperationForProject(project) {
  const snapshot = project?.transcription;
  const videoId = project?.video_id ?? project?.project_id;
  if (!snapshot?.operation_id || !videoId) return null;
  return {
    ...snapshot,
    operation_type: 'transcription',
    video_id: videoId,
  };
}

function diagnosticMessage(diagnostic, fallback) {
  return typeof diagnostic?.message === 'string' && diagnostic.message
    ? diagnostic.message
    : fallback;
}

function apiUrl(path) {
  if (!path) return null;
  return /^https?:\/\//i.test(path) ? path : `${API}${path}`;
}

async function readApiResponse(response) {
  let data = null;
  try {
    data = await response.json();
  } catch {
    // Preserve a useful HTTP error when the server returns a non-JSON response.
  }
  if (!response.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item?.msg || JSON.stringify(item)).join('; ')
      : typeof detail === 'object' && detail !== null
        ? JSON.stringify(detail)
        : detail;
    throw new Error(message || `Ошибка сервера (${response.status})`);
  }
  return data;
}

export default function App({ recentProjectsOverride = null } = {}) {
  const [restoredSession] = useState(loadStoredSession);

  // ── State ──────────────────────────────────────────────────────────────────
  const [serverOnline, setServerOnline] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [recentProjects, setRecentProjects] = useState([]);
  const [projectDiagnostics, setProjectDiagnostics] = useState([]);
  const [projectError, setProjectError] = useState('');
  const [projectPendingAction, setProjectPendingAction] = useState(null);
  const [currentProjectId, setCurrentProjectId] = useState(
    () => restoredSession.projectId || restoredSession.video?.video_id || null,
  );
  const [hydratedOperation, setHydratedOperation] = useState(null);
  const [recentProjectsRevision, setRecentProjectsRevision] = useState(0);

  const [inputTab, setInputTab] = useState('url'); // 'file' | 'url'

  const [video, setVideo] = useState(() => restoredSession.video || null);
  const [videoUrl, setVideoUrl] = useState(() => apiUrl(restoredSession.video?.video_url));
  const [transcript, setTranscript] = useState(null);
  const [subtitleContext, setSubtitleContext] = useState(() => restoredSession.subtitleContext || null);
  const [importingSubtitles, setImportingSubtitles] = useState(false);

  const [analyzing, setAnalyzing] = useState(false);
  const [autoProcessing, setAutoProcessing] = useState(false);
  const [progress, setProgress] = useState({ pct: 0, msg: '', step: '' });
  const [subtitleNotice, setSubtitleNotice] = useState(() => restoredSession.subtitleNotice || '');

  const [segments, setSegments] = useState(() => (Array.isArray(restoredSession.segments) ? restoredSession.segments : []));
  const [selected, setSelected] = useState(() => new Set(Array.isArray(restoredSession.selected) ? restoredSession.selected : []));

  const [editingIdx, setEditingIdx] = useState(null);

  const [exporting, setExporting] = useState(false);
  const [clipResults, setClipResults] = useState(() => (Array.isArray(restoredSession.clipResults) ? restoredSession.clipResults : []));
  const [exportPadding, setExportPadding] = useState(() => (typeof restoredSession.exportPadding === 'number' ? restoredSession.exportPadding : 0.5));
  const [aspectRatio, setAspectRatio] = useState(() => restoredSession.aspectRatio || '9:16'); // default to Shorts 9:16!
  const [burnSubtitles, setBurnSubtitles] = useState(() => restoredSession.burnSubtitles ?? true);

  const [playerTime, setPlayerTime] = useState(0);
  const [playerSeekTo, setPlayerSeekTo] = useState(null);
  const [insertAd, setInsertAd] = useState(false);
  const [adMedia, setAdMedia] = useState(null);
  const hasAd = Boolean(adMedia?.has_media);

  // ── Text Ad Overlay State ──────────────────────────────────────────────────
  const [textAdEnabled, setTextAdEnabled] = useState(false);
  const [textAdText, setTextAdText] = useState(() => restoredSession.textAdText ?? "");
  const [textAdPosition, setTextAdPosition] = useState(() => restoredSession.textAdPosition || 'top');
  const [textAdInterval, setTextAdInterval] = useState(() => (typeof restoredSession.textAdInterval === 'number' ? restoredSession.textAdInterval : 4));
  const [textAdOpacity, setTextAdOpacity] = useState(() => (typeof restoredSession.textAdOpacity === 'number' ? restoredSession.textAdOpacity : 0.75));

  // ── Video Banner State ─────────────────────────────────────────────────────
  const [videoBannerEnabled, setVideoBannerEnabled] = useState(false);
  const [bannerMedia, setBannerMedia] = useState(null);
  const hasVideoBanner = Boolean(bannerMedia?.has_media);
  const [videoBannerHeight, setVideoBannerHeight] = useState(380);

  const wsRef = useRef(null);
  const subtitleInputRef = useRef(null);
  const afterChapterImportRef = useRef(false);
  const selectedVideoIdRef = useRef(video?.video_id);
  const pendingResumeOperationIdRef = useRef(null);
  const autosaveGenerationRef = useRef(0);
  const autosaveControllerRef = useRef(null);
  const autosaveRequestRef = useRef(null);
  const autosaveSchedulerRef = useRef(null);
  const suppressNextAutosaveRef = useRef(false);
  const projectLifecycleRef = useRef(null);
  const restoreCandidateRef = useRef(
    restoredSession.video?.video_id
      ? {
          initialAttemptStarted: false,
          projectId: restoredSession.video.video_id,
          retryable: false,
        }
      : null,
  );
  selectedVideoIdRef.current = video?.video_id;

  if (autosaveSchedulerRef.current === null) {
    autosaveSchedulerRef.current = createProjectAutosaveScheduler({
      save: (projectId, payload) => autosaveRequestRef.current?.(projectId, payload),
    });
  }

  const handleTranscriptionCompleted = useCallback(async (directTranscript = null) => {
    if (!video?.video_id) return;
    const requestedVideoId = video.video_id;
    try {
      const data = directTranscript?.full_text && !directTranscript?.operation_id
        ? directTranscript
        : await readApiResponse(
          await fetch(`${API}/api/video/transcript/${requestedVideoId}`),
        );
      if (selectedVideoIdRef.current !== requestedVideoId) return;
      if (!data?.full_text && !data?.has_transcript) return;
      setTranscript({ ...data, has_transcript: true });
      setSubtitleNotice(
        afterChapterImportRef.current
          ? 'Карта глав и полный транскрипт готовы. Выделяйте текст справа для ручной нарезки.'
          : 'Полный транскрипт готов. Выделяйте текст справа для ручной нарезки.',
      );
      afterChapterImportRef.current = false;
    } catch {
      // The operation panel keeps the completed state; polling can retry the file.
    }
  }, [video?.video_id]);

  const {
    operation: transcriptionOperation,
    error: operationError,
    pendingAction: operationPendingAction,
    adopt: adoptTranscriptionOperation,
    start: startTranscription,
    pause: pauseTranscription,
    resume: resumeTranscription,
    resumeOperation,
    stop: stopTranscription,
  } = useOperation({
    apiBase: API,
    videoId: video?.video_id,
    onCompleted: handleTranscriptionCompleted,
  });
  const transcribing = isUnfinishedTranscription(transcriptionOperation);

  const cancelProjectAutosave = useCallback(() => {
    autosaveGenerationRef.current += 1;
    autosaveSchedulerRef.current?.cancel();
    autosaveControllerRef.current?.abort();
    autosaveControllerRef.current = null;
  }, []);

  autosaveRequestRef.current = async (projectId, payload) => {
    const generation = autosaveGenerationRef.current;
    const controller = new AbortController();
    autosaveControllerRef.current?.abort();
    autosaveControllerRef.current = controller;
    try {
      const response = await fetch(`${API}/api/projects/${projectId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      await readApiResponse(response);
    } catch (saveError) {
      if (saveError.name !== 'AbortError' && generation === autosaveGenerationRef.current) {
        setProjectError(`Не удалось сохранить проект: ${saveError.message}`);
      }
    } finally {
      if (autosaveControllerRef.current === controller) autosaveControllerRef.current = null;
    }
  };

  const applyProjectDetails = useCallback((project, { resumeOperationId = null } = {}) => {
    const mapped = mapProjectToEditorState(project);
    const hydratedVideo = buildHydratedProjectVideo(project, mapped.video);
    cancelProjectAutosave();
    suppressNextAutosaveRef.current = true;
    pendingResumeOperationIdRef.current = resumeOperationId;

    setVideo(hydratedVideo);
    setVideoUrl(apiUrl(hydratedVideo?.video_url));
    setTranscript(mapped.transcript);
    setSubtitleContext(mapped.subtitleContext);
    setSubtitleNotice(mapped.subtitleNotice);
    setSegments(mapped.segments);
    setSelected(mapped.selected);
    setClipResults(mapped.clipResults);
    setExportPadding(mapped.exportPadding);
    setAspectRatio(mapped.aspectRatio);
    setBurnSubtitles(mapped.burnSubtitles);
    setInsertAd(mapped.insertAd);
    setTextAdEnabled(mapped.textAdEnabled);
    setTextAdText(mapped.textAdText);
    setTextAdPosition(mapped.textAdPosition);
    setTextAdInterval(mapped.textAdInterval);
    setTextAdOpacity(mapped.textAdOpacity);
    setVideoBannerEnabled(mapped.videoBannerEnabled);
    setVideoBannerHeight(mapped.videoBannerHeight);
    setEditingIdx(null);
    setPlayerTime(0);
    setPlayerSeekTo(null);
    setProgress({ pct: 0, msg: '', step: '' });
    setHydratedOperation(recoveredOperationForProject(project));
    setCurrentProjectId(mapped.projectId);

    const checkpointDiagnostic = project?.transcription?.diagnostic;
    setProjectError(checkpointDiagnostic
      ? diagnosticMessage(checkpointDiagnostic, 'Сохранённый прогресс нельзя продолжить')
      : '');
  }, [cancelProjectAutosave]);

  if (projectLifecycleRef.current === null) {
    projectLifecycleRef.current = createProjectLifecycleController({
      apiBase: API,
      readResponse: readApiResponse,
      autosave: autosaveSchedulerRef.current,
      cancelAutosaveRequest: cancelProjectAutosave,
      hydrateProject: applyProjectDetails,
      clearMissingProject: (project) => {
        pendingResumeOperationIdRef.current = null;
        setCurrentProjectId(null);
        setHydratedOperation(null);
        setVideo(null);
        setVideoUrl(null);
        setTranscript(null);
        setSubtitleContext(null);
        setSubtitleNotice('');
        setSegments([]);
        setSelected(new Set());
        setClipResults([]);
        clearSession(window.sessionStorage);
        setProjectError(diagnosticMessage(
          project.source_diagnostic,
          'Исходный файл проекта не найден; данные проекта сохранены на сервере',
        ));
      },
      receiveRecentProjects: (data) => {
        setRecentProjects(Array.isArray(data?.projects) ? data.projects : []);
        setProjectDiagnostics(Array.isArray(data?.diagnostics) ? data.diagnostics : []);
      },
      receiveRestart: (projectId, snapshot) => {
        setRecentProjects((projects) => projects.map((item) => (
          item.project_id === projectId
            ? { ...item, transcription: snapshot, has_transcript: false }
            : item
        )));
      },
      setPendingAction: setProjectPendingAction,
      setError: setProjectError,
    });
  }
  const projectLifecycle = projectLifecycleRef.current;
  const hasRecentProjectsOverride = Array.isArray(recentProjectsOverride);
  const recentProjectsForView = hasRecentProjectsOverride ? recentProjectsOverride : recentProjects;
  const showRecentProjects = hasRecentProjectsOverride || canUseRecentProjects({
    serverOnline,
    hasVideo: Boolean(video),
  });

  useEffect(() => {
    projectLifecycle.syncRecentProjects({ serverOnline, hasVideo: Boolean(video) });
    return () => projectLifecycle.cancelRecent();
  }, [projectLifecycle, serverOnline, Boolean(video), recentProjectsRevision]);

  useEffect(() => {
    const candidate = restoreCandidateRef.current;
    if (
      !serverOnline
      || !candidate
      || (candidate.initialAttemptStarted && !candidate.retryable)
    ) return undefined;

    candidate.initialAttemptStarted = true;
    candidate.retryable = false;
    let cancelled = false;
    projectLifecycle.loadProject(candidate.projectId, { origin: 'restore' })
      .then((project) => {
        if (cancelled || restoreCandidateRef.current !== candidate) return;
        if (project) {
          restoreCandidateRef.current = null;
        } else {
          candidate.retryable = true;
        }
      });
    return () => {
      cancelled = true;
      projectLifecycle.cancelDetail();
    };
  }, [projectLifecycle, serverOnline]);

  useEffect(() => {
    if (!hydratedOperation || hydratedOperation.video_id !== video?.video_id) return undefined;
    const adopted = adoptTranscriptionOperation(hydratedOperation);
    const resumeId = pendingResumeOperationIdRef.current;
    if (!resumeId) return undefined;
    pendingResumeOperationIdRef.current = null;

    if (!adopted || resumeId !== hydratedOperation.operation_id) {
      setProjectError('Сохранённую операцию не удалось привязать к этому проекту');
      projectLifecycle.completePending(video.video_id, 'resume');
      return undefined;
    }

    let disposed = false;
    resumeOperation(resumeId)
      .then((snapshot) => {
        if (!disposed && !snapshot) {
          setProjectError('Продолжение уже выполняется или сохранённая операция устарела');
        }
      })
      .catch((resumeError) => {
        if (!disposed && resumeError.name !== 'AbortError') setProjectError(resumeError.message);
      })
      .finally(() => {
        if (!disposed) projectLifecycle.completePending(video.video_id, 'resume');
      });
    return () => {
      disposed = true;
    };
  }, [adoptTranscriptionOperation, hydratedOperation, projectLifecycle, resumeOperation, video?.video_id]);

  useEffect(() => {
    if (!video) {
      sessionStorage.removeItem(SESSION_KEY);
      return;
    }

    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify({
        projectId: currentProjectId,
        video,
        subtitleContext,
        subtitleNotice,
        segments,
        selected: [...selected],
        clipResults,
        exportPadding,
        aspectRatio,
        textAdEnabled,
        textAdText,
        textAdPosition,
        textAdInterval,
        textAdOpacity,
      }));
    } catch {
      // Session persistence is optional; the active editor must keep working.
    }
  }, [currentProjectId, video, subtitleContext, subtitleNotice, segments, selected, clipResults, exportPadding, aspectRatio, textAdEnabled, textAdText, textAdPosition, textAdInterval, textAdOpacity]);

  useEffect(() => {
    if (!serverOnline) {
      autosaveSchedulerRef.current?.cancel();
      return undefined;
    }
    if (suppressNextAutosaveRef.current) {
      suppressNextAutosaveRef.current = false;
      autosaveSchedulerRef.current?.cancel();
      return undefined;
    }
    projectLifecycle.scheduleAutosave({
      projectId: currentProjectId,
      serverOnline,
      editor: {
        segments,
        selected,
        clipResults,
        subtitleContext,
        subtitleNotice,
        exportPadding,
        aspectRatio,
        burnSubtitles,
        insertAd,
        textAdEnabled,
        textAdText,
        textAdPosition,
        textAdInterval,
        textAdOpacity,
        videoBannerEnabled,
        videoBannerHeight,
      },
    });
    return () => autosaveSchedulerRef.current?.cancel();
  }, [currentProjectId, projectLifecycle, serverOnline, subtitleContext, subtitleNotice, segments, selected, clipResults, exportPadding, aspectRatio, burnSubtitles, insertAd, textAdEnabled, textAdText, textAdPosition, textAdInterval, textAdOpacity, videoBannerEnabled, videoBannerHeight]);

  useEffect(() => () => {
    projectLifecycle.dispose();
  }, [projectLifecycle]);

  // ── Server health ──────────────────────────────────────────────────────────
  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch(`${API}/api/health`);
        setServerOnline(r.ok);
      } catch {
        setServerOnline(false);
      }
    };
    check();
    const id = setInterval(check, 5000);
    return () => clearInterval(id);
  }, []);

  // ── WebSocket ──────────────────────────────────────────────────────────────
  useEffect(() => {
    let reconnectTimer;
    let disposed = false;
    const connect = () => {
      const apiUrl = API ? new URL(API, window.location.href) : new URL(window.location.href);
      const protocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${apiUrl.host}/ws/progress`);
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'progress') {
          setProgress({ pct: data.pct, msg: data.msg, step: data.step || '' });
        }
      };
      ws.onclose = () => {
        if (!disposed) reconnectTimer = setTimeout(connect, 3000);
      };
      wsRef.current = ws;
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []);

  useEffect(() => () => {
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  // If Whisper finished in the background (for example during AI analysis),
  // automatically reveal the selectable transcript without another upload.
  useEffect(() => {
    if (
      !video
      || (
        transcript?.has_transcript
        && (transcript.full_text || transcript.words?.length || transcript.segments?.length)
      )
    ) return;
    let cancelled = false;

    const loadReadyTranscript = async () => {
      try {
        const response = await fetch(`${API}/api/video/transcript/${video.video_id}`);
        const data = await readApiResponse(response);
        if (!cancelled && data.has_transcript) {
          setTranscript(data);
          setSubtitleNotice('Полный транскрипт готов. Теперь можно выделять любую фразу и скачивать её как отдельный клип.');
        }
      } catch {
        // The transcript may not exist yet; the active operation will create it.
      }
    };

    loadReadyTranscript();
    const timer = (analyzing || transcribing)
      ? setInterval(loadReadyTranscript, 3000)
      : null;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [video?.video_id, transcript?.has_transcript, analyzing, transcribing]);

  // ── Video loaded ───────────────────────────────────────────────────────────
  const hydrateRecentProject = async (project, { resume = false } = {}) => {
    const projectId = project?.project_id;
    if (!projectId) return;
    restoreCandidateRef.current = null;
    setProjectError('');
    await projectLifecycle.loadProject(projectId, { resume });
  };

  const handleRestartProject = (project) => {
    const projectId = project?.project_id;
    if (!projectId) return Promise.resolve(null);
    setProjectError('');
    return projectLifecycle.restartProject(projectId);
  };

  const handleVideoReady = (videoData, localFileUrl = null) => {
    restoreCandidateRef.current = null;
    projectLifecycle.cancelAll();
    pendingResumeOperationIdRef.current = null;
    projectLifecycle.adoptHydratedProject(videoData.video_id);
    setCurrentProjectId(videoData.video_id);
    setHydratedOperation(null);
    setProjectPendingAction(null);
    setProjectError('');
    setVideo(videoData);
    setSegments([]);
    setSelected(new Set());
    setClipResults([]);
    setTranscript(videoData.transcript || null);
    setSubtitleContext(null);

    const tInfo = videoData.transcript_info || {};
    if (tInfo.has_transcript || videoData.transcript?.has_transcript) {
      const kind = tInfo.is_auto ? 'автоматические' : 'встроенные';
      setSubtitleNotice(
        `Субтитры (${kind}) получены напрямую с YouTube — распознавание речи не потребовалось.`
      );
    } else if (videoData.auto) {
      setSubtitleNotice('У видео нет субтитров, речь распознана локально.');
    } else {
      setSubtitleNotice('');
    }

    if (localFileUrl) {
      setVideoUrl(localFileUrl);
    } else if (videoData.video_url) {
      setVideoUrl(apiUrl(videoData.video_url));
    } else {
      setVideoUrl(null);
    }

    const clips = videoData.segments || videoData.chapters || videoData.instant_clips || [];
    if (clips.length > 0) {
      setSegments(clips);
      setSelected(new Set(clips.map((_, i) => i)));
    }

    // Авто-конвейер уже нарезал файлы — показываем их сразу
    if (Array.isArray(videoData.clips) && videoData.clips.length > 0) {
      setClipResults(videoData.clips);
    }
  };

  const handleFileUpload = async (file) => {
    const localUrl = URL.createObjectURL(file);
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await fetch(`${API}/api/video/upload`, { method: 'POST', body: form });
      const data = await readApiResponse(r);
      handleVideoReady(data, localUrl);
    } catch (err) {
      URL.revokeObjectURL(localUrl);
      alert(`Ошибка загрузки видео: ${err.message}`);
    }
  };

  const handleResetVideo = () => {
    restoreCandidateRef.current = null;
    projectLifecycle.cancelAll();
    pendingResumeOperationIdRef.current = null;
    setCurrentProjectId(null);
    setHydratedOperation(null);
    setProjectPendingAction(null);
    setVideo(null);
    setVideoUrl(null);
    setSegments([]);
    setSelected(new Set());
    setClipResults([]);
    setTranscript(null);
    setSubtitleContext(null);
    setSubtitleNotice('');
    clearSession(window.sessionStorage);
    setRecentProjectsRevision((revision) => revision + 1);
  };

  // ── Transcribe (Whisper) ───────────────────────────────────────────────────
  const handleTranscribe = async ({ afterChapterImport = false } = {}) => {
    if (!video) return;
    afterChapterImportRef.current = afterChapterImport;
    try {
      await startTranscription();
    } catch {
      // The operation hook renders the server error without discarding progress.
    }
  };

  const handleSubtitleUpload = async (file) => {
    if (!video || !file) return;
    setImportingSubtitles(true);
    setSubtitleNotice('');
    setProgress({ pct: 20, msg: 'Импортирую текст и таймкоды из SRT...', step: 'importing' });
    const form = new FormData();
    form.append('video_id', video.video_id);
    form.append('file', file);

    try {
      const response = await fetch(`${API}/api/video/transcript/import`, { method: 'POST', body: form });
      const data = await readApiResponse(response);
      const context = {
        kind: data.transcript_kind || 'full',
        filename: data.source_filename || file.name,
        count: data.segments?.length || 0,
      };
      setSubtitleContext(context);
      if (data.has_transcript) {
        setTranscript({ ...data, has_transcript: true });
        setSubtitleNotice(`Полный SRT загружен: ${context.count} реплик. Whisper не потребуется.`);
      } else {
        setSubtitleNotice(`Загружена карта из ${context.count} глав. Готовлю полный выделяемый текст через Whisper...`);
        setImportingSubtitles(false);
        await handleTranscribe({ afterChapterImport: true });
      }
    } catch (err) {
      alert('Ошибка импорта SRT: ' + err.message);
    } finally {
      setImportingSubtitles(false);
      if (subtitleInputRef.current) subtitleInputRef.current.value = '';
    }
  };

  // ── Analyze (Vizard ИИ-поиск историй или нарезка на равные части) ───────────
  const handleAnalyze = async (options = {}) => {
    if (!video || importingSubtitles || transcribing) return;
    setAnalyzing(true);
    setSegments([]);
    setSelected(new Set());
    setClipResults([]);

    const isEqualMode = options.mode === 'equal_chunks';
    const chunkMinutes = options.chunk_minutes || 4.0;
    const needsWhisper = !isEqualMode && !transcript?.has_transcript;

    setProgress({
      pct: needsWhisper ? 5 : 10,
      msg: isEqualMode
        ? `Разбиваю всё видео от начала до конца на части по ${chunkMinutes} мин...`
        : needsWhisper
          ? subtitleContext?.kind === 'chapters'
            ? `Карта из ${subtitleContext.count} глав загружена. Запускаю Whisper для полной речи...`
            : 'Полного транскрипта нет. Запускаю Whisper...'
          : 'Транскрипт готов. ИИ определяет вопросы, истории и кульминации...',
      step: needsWhisper ? 'transcribing' : 'analyzing',
    });

    try {
      const r = await fetch(`${API}/api/video/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_id: video.video_id,
          mode: isEqualMode ? 'equal_chunks' : 'transcript',
          chunk_minutes: chunkMinutes,
          prompt: isEqualMode ? '' : [
            'Самостоятельно найди лучшие законченные истории и смысловые моменты.',
            'Ищи полную смысловую дугу: вопрос или подводку, начало рассказа, развитие, кульминацию и вывод.',
            'Если история началась с вопроса собеседника, включи вопрос в начало клипа.',
            'Не вырывай только яркую фразу без контекста. Убери воду до начала темы и после завершения мысли.',
            'Оцени юмор, эмоции, конфликт, неожиданность, пользу и виральность.',
          ].join(' '),
        }),
      });
      const data = await readApiResponse(r);
      const segs = data.segments || [];
      setSegments(segs);
      setSelected(new Set(segs.map((_, i) => i)));

      if (segs.length > 0) {
        setAnalyzing(false);
        setExporting(true);
        setProgress({ pct: 5, msg: `ИИ нашёл ${segs.length} моментов. Нарезаю готовые клипы...`, step: 'exporting' });
        const exportResponse = await fetch(`${API}/api/video/export`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_id: video.video_id,
            segments: segs.map((segment, index) => serializeExportSegment(segment, index)),
            mode: 'separate',
            padding: exportPadding,
            aspect_ratio: aspectRatio,
            burn_subtitles: burnSubtitles,
            insert_ad: insertAd,
          }),
        });
        const exportData = await readApiResponse(exportResponse);
        setClipResults(exportData.clips || []);
      }

      fetch(`${API}/api/video/transcript/${video.video_id}`)
        .then(res => res.json())
        .then(tData => { if (tData.has_transcript) setTranscript(tData); })
        .catch(() => {});
    } catch (err) {
      alert('Ошибка анализа: ' + err.message);
    } finally {
      setAnalyzing(false);
      setExporting(false);
    }
  };

  // ── Precise manual cut from selected transcript words ──────────────────────
  const handleCutSelection = (selection) => {
    const label = selection.text.length > 44 ? `${selection.text.slice(0, 44)}...` : selection.text;
    const newSeg = {
      start: selection.start,
      end: selection.end,
      label: `По тексту: ${label}`,
      description: 'Точно вырезано по первому и последнему выделенному слову',
      score: 1.0,
      source: 'manual',
      transcript_text: selection.text,
    };

    const nextSegs = [...segments, newSeg];
    const newIdx = nextSegs.length - 1;
    setSegments(nextSegs);

    const nextSel = new Set(selected);
    nextSel.add(newIdx);
    setSelected(nextSel);

    handleDownloadSingleIndex(newIdx, nextSegs, 0);
  };

  const downloadClipFile = async (clip) => {
    const response = await fetch(apiUrl(clip.url));
    if (!response.ok) {
      throw new Error(`Не удалось скачать клип (${response.status})`);
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const safeLabel = (clip.label || 'clip').replace(/[<>:"/\\|?*\u0000-\u001F]/g, '_').trim();
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = `${safeLabel || 'clip'}.mp4`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  };

  // ── Download single clip ───────────────────────────────────────────────────
  const handleDownloadSingleIndex = async (index, currentSegs = segments, padding = exportPadding) => {
    const seg = currentSegs[index];
    if (!seg || !video) return;

    setExporting(true);
    setProgress({ pct: 0, msg: `Нарезаю клип «${seg.label}»...` });

    try {
      const r = await fetch(`${API}/api/video/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_id: video.video_id,
          segments: [serializeExportSegment(seg, index)],
          mode: 'separate',
          padding,
          aspect_ratio: aspectRatio,
          burn_subtitles: burnSubtitles,
          insert_ad: insertAd,        }),
      });
      const data = await readApiResponse(r);
      const clips = data.clips || [];
      if (clips.length > 0) {
        setClipResults((prev) => [...clips, ...prev]);
        // Автоматически скачиваем клип
        await downloadClipFile(clips[0]);
      }
    } catch (err) {
      alert('Ошибка экспорта клипа: ' + err.message);
    } finally {
      setExporting(false);
    }
  };

  // ── Segment editor ─────────────────────────────────────────────────────────
  const handleSaveSegment = (index, updatedSeg) => {
    const copy = [...segments];
    copy[index] = updatedSeg;
    setSegments(copy);
    setEditingIdx(null);
  };

  // ── Batch Export ───────────────────────────────────────────────────────────
  const handleExport = async (mode) => {
    if (!video || selected.size === 0) return;
    setExporting(true);
    setClipResults([]);
    setProgress({ pct: 0, msg: 'Нарезка клипов...' });

    const segsToExport = segments
      .map((segment, index) => ({ segment, index }))
      .filter(({ index }) => selected.has(index))
      .map(({ segment, index }) => serializeExportSegment(segment, index));

    try {
      const r = await fetch(`${API}/api/video/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_id: video.video_id,
          segments: segsToExport,
          mode,
          padding: exportPadding,
          aspect_ratio: aspectRatio,
          burn_subtitles: burnSubtitles,
          insert_ad: insertAd,        }),
      });
      const data = await readApiResponse(r);
      setClipResults(data.clips || []);
    } catch (err) {
      alert('Ошибка экспорта: ' + err.message);
    } finally {
      setExporting(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="app-layout">
      <Header online={serverOnline} onSettings={() => setShowSettings(true)} />

      <main className="app-main" style={{ paddingTop: 32 }}>

        {projectError && (
          <div className="glass-card p-24" role="alert" style={{ marginBottom: 20 }}>
            {projectError}
          </div>
        )}

        {/* ── Tabs for Input ────────────────────────────────────────────── */}
        {(!video || hasRecentProjectsOverride) && (
          <section style={{ marginBottom: 28 }}>

            {projectDiagnostics.map((diagnostic) => (
              <div
                className="glass-card p-24"
                role="status"
                key={`${diagnostic.project_id || 'project'}-${diagnostic.code || diagnostic.message}`}
                style={{ marginBottom: 16 }}
              >
                {diagnosticMessage(diagnostic, 'Проект повреждён и не был изменён')}
              </div>
            ))}

            {showRecentProjects && (
              <RecentProjects
                projects={recentProjectsForView}
                pendingAction={projectPendingAction}
                onOpen={(project) => hydrateRecentProject(project)}
                onResume={(project) => hydrateRecentProject(project, { resume: true })}
                onRestart={handleRestartProject}
              />
            )}

            <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
              <button
                id="btn-tab-url"
                className={`btn ${inputTab === 'url' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setInputTab('url')}
              >
                🔗 YouTube / URL ссылка
              </button>
              <button
                id="btn-tab-file"
                className={`btn ${inputTab === 'file' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setInputTab('file')}
              >
                📁 Видео с ПК
              </button>
            </div>

            {inputTab === 'url' ? (
              <UrlInput apiBase={API} onVideoReady={handleVideoReady} onBusyChange={setAutoProcessing} />
            ) : (
              <VideoUploader onVideoSelect={handleFileUpload} onResetVideo={handleResetVideo} currentVideo={video} />
            )}
          </section>
        )}

        {/* If video loaded: Header bar */}
        {video && (
          <section style={{ marginBottom: 24 }}>
            <VideoUploader onVideoSelect={handleFileUpload} onResetVideo={handleResetVideo} currentVideo={video} />
            <input
              ref={subtitleInputRef}
              type="file"
              accept=".srt,application/x-subrip,text/plain"
              style={{ display: 'none' }}
              onChange={(event) => handleSubtitleUpload(event.target.files?.[0])}
            />
          </section>
        )}

        {/* ── Main Workspace: Player + Prompt + Transcript ────────────────── */}
        {video && (
          <div className="workspace-grid">

            {/* Left: Player + Prompt Search */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div className="glass-card" style={{ overflow: 'hidden' }}>
                <VideoPlayer
                  src={videoUrl}
                  segments={segments}
                  duration={video?.metadata?.duration || 0}
                  onTimeUpdate={setPlayerTime}
                  seekTo={playerSeekTo}
                  onSeekDone={() => setPlayerSeekTo(null)}
                  transcript={transcript}
                  showSubtitles={burnSubtitles}
                />
              </div>

              {/* Prompt Search */}
              <div className="glass-card p-24">
                <div className="section-title">
                  <div className="section-title-icon">🧠</div>
                  ИИ-монтаж лучших моментов
                </div>
                <PromptSelector
                  onAnalyze={handleAnalyze}
                  onPrepareTranscript={() => handleTranscribe({ afterChapterImport: true })}
                  loading={analyzing}
                  preparingTranscript={transcribing}
                  transcriptReady={Boolean(transcript?.has_transcript)}
                  disabled={importingSubtitles}
                  subtitleContext={subtitleContext}
                />
                {subtitleNotice && (
                  <div className="subtitle-upload-notice" role="status">
                    ✓ {subtitleNotice}
                  </div>
                )}
              </div>
            </div>

            {/* Right: Transcript View or Transcribe Button */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {transcript?.has_transcript ? (
                <TranscriptView
                  transcript={transcript}
                  currentTime={playerTime}
                  onWordClick={(time) => setPlayerSeekTo(time)}
                  onCutSelection={handleCutSelection}
                  onImportSrt={() => subtitleInputRef.current?.click()}
                  cutting={exporting}
                />
              ) : (
                <div className="glass-card p-24" style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', minHeight: 300 }}>
                  <div style={{ fontSize: 48, marginBottom: 12 }}>🎙️</div>
                  <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>
                    Транскрипция речи (Whisper)
                  </h3>
                  <p style={{ color: 'var(--text-secondary)', fontSize: 14, maxWidth: 420, marginBottom: 20 }}>
                    Загрузите готовый SRT с текстом и таймкодами или распознайте речь через Whisper. Для поиска лучших моментов вручную делать это необязательно.
                  </p>
                  <div className="transcript-source-actions">
                    <button
                      id="btn-import-srt"
                      className="btn btn-primary btn-lg"
                      onClick={() => subtitleInputRef.current?.click()}
                      disabled={importingSubtitles}
                    >
                      {importingSubtitles ? <><div className="spinner" /> Импорт...</> : <>📄 Загрузить SRT</>}
                    </button>
                    <button
                      id="btn-start-transcribe"
                      className="btn btn-secondary btn-lg"
                      onClick={handleTranscribe}
                      disabled={transcribing}
                    >
                      {transcribing ? <><div className="spinner" /> Распознавание...</> : <>🎙️ Запустить Whisper</>}
                    </button>
                  </div>
                </div>
              )}
            </div>

          </div>
        )}

        <OperationPanel
          operation={transcriptionOperation}
          error={operationError}
          pendingAction={operationPendingAction || transcriptionOperation?.pending_action}
          onPause={pauseTranscription}
          onResume={resumeTranscription}
          onStop={stopTranscription}
        />

        {/* ── Progress Overlay ────────────────────────────────────────────── */}
        {(analyzing || importingSubtitles || exporting || autoProcessing) && (
          <div className="glass-card progress-overlay" style={{ marginBottom: 28 }}>
            <div className="progress-pct">{Math.min(progress.pct, 100)}%</div>
            <div className="progress-overlay-title">
              {progress.step === 'downloading'
                ? '⬇️ Скачиваю видео...'
                : progress.step === 'transcribing'
                  ? '🎙️ Whisper распознаёт речь...'
                  : progress.step === 'importing' || importingSubtitles
                    ? '📄 Загружаю SRT...'
                    : progress.step === 'exporting' || exporting
                      ? '✂️ Нарезка клипов...'
                      : progress.step === 'analyzing'
                        ? '🧠 ИИ ищет истории...'
                        : autoProcessing
                          ? '⚡ Готовлю клипы...'
                          : '🧠 ИИ ищет истории...'}
            </div>
            <p className="progress-overlay-msg">{progress.msg}</p>
            {progress.step === 'transcribing' && (
              <p className="progress-overlay-note">Субтитров у видео не нашлось, поэтому речь распознаётся локально — это может занять 10–25 минут на CPU. Не закрывайте окно backend.</p>
            )}
            <div className="progress-bar-track" style={{ maxWidth: 400, margin: '0 auto' }}>
              <div className="progress-bar-fill" style={{ width: `${progress.pct}%` }} />
            </div>
          </div>
        )}

        {/* ── Highlight List ──────────────────────────────────────────────── */}
        {segments.length > 0 && (
          <section style={{ marginBottom: 28 }}>
            <div className="section-title" style={{ marginBottom: 20 }}>
              <div className="section-title-icon">⚡</div>
              Найденные клипы для Shorts ({segments.length})
            </div>
            <HighlightList
              segments={segments}
              selected={selected}
              onToggle={(i) => {
                const next = new Set(selected);
                next.has(i) ? next.delete(i) : next.add(i);
                setSelected(next);
              }}
              onPlayAt={(start) => setPlayerSeekTo(start)}
              onEditSegment={(i) => setEditingIdx(i)}
              onDownloadSingle={handleDownloadSingleIndex}
            />
          </section>
        )}

        {/* ── Export Panel ────────────────────────────────────────────────── */}
        {segments.length > 0 && (
          <section style={{ marginBottom: 28 }}>
            <ExportPanel
              selectedCount={selected.size}
              exporting={exporting}
              onExport={handleExport}
              clipResults={clipResults}
              padding={exportPadding}
              onPaddingChange={setExportPadding}
              aspectRatio={aspectRatio}
              onAspectRatioChange={setAspectRatio}
              burnSubtitles={burnSubtitles}
              onBurnSubtitlesChange={setBurnSubtitles}
              insertAd={insertAd}
              onInsertAdChange={setInsertAd}
              hasAd={hasAd}
              textAdEnabled={textAdEnabled}
              onTextAdEnabledChange={setTextAdEnabled}
              textAdText={textAdText}
              onTextAdTextChange={setTextAdText}
              textAdPosition={textAdPosition}
              onTextAdPositionChange={setTextAdPosition}
              textAdInterval={textAdInterval}
              onTextAdIntervalChange={setTextAdInterval}
              textAdOpacity={textAdOpacity}
              onTextAdOpacityChange={setTextAdOpacity}
              videoBannerEnabled={videoBannerEnabled}
              onVideoBannerEnabledChange={setVideoBannerEnabled}
              hasVideoBanner={hasVideoBanner}
              videoBannerHeight={videoBannerHeight}
              onVideoBannerHeightChange={setVideoBannerHeight}
              apiBase={API}
              onDownloadClip={downloadClipFile}
              transcript={transcript}
              onReEdit={(index) => {
                if (Number.isInteger(index) && segments[index]) setEditingIdx(index);
              }}
            />
          </section>
        )}

        {/* ── Ad Manager ─────────────────────────────────────────────── */}
        {video && segments.length > 0 && (
          <section style={{ marginBottom: 28 }}>
            <div className="glass-card p-24">
              <AdManager
                apiBase={API}
                onAdStatusChange={setAdMedia}
                onBannerStatusChange={(media, source) => {
                  setBannerMedia(media);
                  if (source === 'uploaded') setVideoBannerEnabled(true);
                  if (!media?.has_media) setVideoBannerEnabled(false);
                }}
              />
            </div>
          </section>
        )}

        {/* ── Social Share ────────────────────────────────────────────── */}
        {clipResults.length > 0 && (
          <section style={{ marginBottom: 28 }}>
            <div className="glass-card p-24">
              <SocialSharePanel
                apiBase={API}
                clipResults={clipResults}
              />
            </div>
          </section>
        )}

      </main>

      {/* ── Segment Editor Modal ────────────────────────────────────────── */}
      {editingIdx !== null && (
        <SegmentEditor
          segment={segments[editingIdx]}
          index={editingIdx}
          duration={video?.metadata?.duration || video?.duration || 0}
          sourceUrl={videoUrl}
          sourceWidth={video?.metadata?.width || video?.width || 0}
          sourceHeight={video?.metadata?.height || video?.height || 0}
          outputAspectRatio={aspectRatio}
          bannerMedia={bannerMedia}
          adMedia={adMedia}
          transcript={transcript}
          onSave={handleSaveSegment}
          onCancel={() => setEditingIdx(null)}
          onPreview={(time) => setPlayerSeekTo(time)}
        />
      )}

      {/* ── Settings Modal ──────────────────────────────────────────────── */}
      {showSettings && (
        <SettingsModal apiBase={API} onClose={() => setShowSettings(false)} />
      )}
    </div>
  );
}
