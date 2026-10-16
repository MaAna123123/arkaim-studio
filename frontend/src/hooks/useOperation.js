import { useCallback, useEffect, useRef, useState } from 'react';


const UNFINISHED_STATUSES = new Set(['queued', 'running', 'paused', 'stopped']);

export function isUnfinishedTranscription(operation) {
  return operation?.operation_type === 'transcription'
    && UNFINISHED_STATUSES.has(operation?.status);
}

export function getOperationActions(status) {
  if (status === 'running') return ['pause', 'stop'];
  if (status === 'paused') return ['resume', 'stop'];
  if (status === 'stopped') return ['resume'];
  return [];
}

export function matchesOperationEvent(event, currentOperation, videoId) {
  if (event?.type !== 'operation' || event.video_id !== videoId) return false;
  return Boolean(currentOperation?.operation_id)
    && event.operation_id === currentOperation.operation_id;
}

export function canApplyOperationSnapshot(
  snapshot,
  currentOperation,
  videoId,
  { claim = false } = {},
) {
  if (!snapshot?.operation_id || snapshot.video_id !== videoId) return false;
  if (claim) return true;
  return Boolean(currentOperation?.operation_id)
    && currentOperation.operation_id === snapshot.operation_id;
}

export function formatEta(value) {
  if (value === null || value === undefined || value === '') return '—';
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return minutes ? `${minutes} мин ${remainder} сек` : `${remainder} сек`;
}

async function readResponse(response) {
  let data = null;
  try {
    data = await response.json();
  } catch {
    // A useful HTTP status is returned below when a proxy emits plain text.
  }
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : `Ошибка сервера (${response.status})`);
  }
  return data;
}

export default function useOperation({ apiBase, videoId, onCompleted }) {
  const [operation, setOperation] = useState(null);
  const [error, setError] = useState('');
  const [pendingAction, setPendingAction] = useState('');
  const [socketConnected, setSocketConnected] = useState(false);
  const operationRef = useRef(null);
  const completedRef = useRef(null);
  const generationRef = useRef(0);
  const requestControllersRef = useRef(new Set());

  const applySnapshot = useCallback((snapshot, options = {}) => {
    const {
      claim = false,
      generation = generationRef.current,
      expectedVideo = videoId,
    } = options;
    if (generation !== generationRef.current) return false;
    if (!canApplyOperationSnapshot(
      snapshot,
      operationRef.current,
      expectedVideo,
      { claim },
    )) return false;
    operationRef.current = snapshot;
    setOperation(snapshot);
    setError(snapshot.error || '');
    if (snapshot.status === 'completed' && completedRef.current !== snapshot.operation_id) {
      completedRef.current = snapshot.operation_id;
      onCompleted?.(snapshot);
    }
    return true;
  }, [onCompleted, videoId]);

  const refresh = useCallback(async (operationId = operationRef.current?.operation_id) => {
    if (!operationId) return null;
    const generation = generationRef.current;
    const expectedVideo = videoId;
    const controller = new AbortController();
    requestControllersRef.current.add(controller);
    try {
      const response = await fetch(`${apiBase}/api/operations/${operationId}`, {
        signal: controller.signal,
      });
      const snapshot = await readResponse(response);
      applySnapshot(snapshot, { generation, expectedVideo });
      return snapshot;
    } finally {
      requestControllersRef.current.delete(controller);
    }
  }, [apiBase, applySnapshot, videoId]);

  useEffect(() => {
    generationRef.current += 1;
    const generation = generationRef.current;
    requestControllersRef.current.forEach((controller) => controller.abort());
    requestControllersRef.current.clear();
    operationRef.current = null;
    completedRef.current = null;
    setOperation(null);
    setError('');
    setPendingAction('');
    if (!videoId) return undefined;

    const controller = new AbortController();
    const restore = async () => {
      try {
        const response = await fetch(
          `${apiBase}/api/operations?video_id=${encodeURIComponent(videoId)}&active=true`,
          { signal: controller.signal },
        );
        const data = await readResponse(response);
        const restored = data?.operations?.find(
          (item) => item.operation_type === 'transcription' && item.video_id === videoId,
        );
        if (restored) applySnapshot(restored, {
          claim: true,
          generation,
          expectedVideo: videoId,
        });
      } catch (restoreError) {
        if (restoreError.name !== 'AbortError') setError(restoreError.message);
      }
    };
    restore();
    return () => {
      controller.abort();
      if (generationRef.current === generation) generationRef.current += 1;
    };
  }, [apiBase, videoId, applySnapshot]);

  useEffect(() => {
    if (!videoId) return undefined;
    let disposed = false;
    let reconnectTimer = null;
    let socket = null;
    const generation = generationRef.current;

    const connect = () => {
      const baseUrl = new URL(apiBase, window.location.href);
      const protocol = baseUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(`${protocol}//${baseUrl.host}/ws/progress`);
      socket.onopen = () => setSocketConnected(true);
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data);
          if (
            !disposed
            && generation === generationRef.current
            && matchesOperationEvent(event, operationRef.current, videoId)
          ) {
            applySnapshot(event, { generation, expectedVideo: videoId });
          }
        } catch {
          // Ignore malformed progress events and recover through status polling.
        }
      };
      socket.onclose = () => {
        if (!disposed && generation === generationRef.current) {
          setSocketConnected(false);
          reconnectTimer = window.setTimeout(connect, 3000);
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [apiBase, videoId, applySnapshot]);

  useEffect(() => {
    if (!operation?.operation_id || !isUnfinishedTranscription(operation)) return undefined;
    const intervalMs = socketConnected ? 5000 : 3000;
    const timer = window.setInterval(() => {
      refresh().catch((refreshError) => {
        if (refreshError.name !== 'AbortError') setError(refreshError.message);
      });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [operation?.operation_id, operation?.status, socketConnected, refresh]);

  const start = useCallback(async () => {
    if (!videoId) return null;
    const generation = generationRef.current;
    const expectedVideo = videoId;
    const controller = new AbortController();
    requestControllersRef.current.add(controller);
    setError('');
    setPendingAction('start');
    try {
      const response = await fetch(`${apiBase}/api/video/transcribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: videoId }),
        signal: controller.signal,
      });
      const data = await readResponse(response);
      if (generation !== generationRef.current || expectedVideo !== videoId) return null;
      if (data?.operation_id) applySnapshot(data, {
        claim: true,
        generation,
        expectedVideo,
      });
      else onCompleted?.(data);
      return data;
    } catch (startError) {
      if (startError.name === 'AbortError') return null;
      if (generation === generationRef.current) setError(startError.message);
      throw startError;
    } finally {
      requestControllersRef.current.delete(controller);
      if (generation === generationRef.current) setPendingAction('');
    }
  }, [apiBase, videoId, applySnapshot, onCompleted]);

  const control = useCallback(async (action) => {
    const operationId = operationRef.current?.operation_id;
    if (!operationId) return null;
    const generation = generationRef.current;
    const expectedVideo = videoId;
    const controller = new AbortController();
    requestControllersRef.current.add(controller);
    setError('');
    setPendingAction(action);
    try {
      const response = await fetch(`${apiBase}/api/operations/${operationId}/${action}`, {
        method: 'POST',
        signal: controller.signal,
      });
      const snapshot = await readResponse(response);
      applySnapshot(snapshot, { generation, expectedVideo });
      return snapshot;
    } catch (controlError) {
      if (controlError.name === 'AbortError') return null;
      if (generation === generationRef.current) setError(controlError.message);
      throw controlError;
    } finally {
      requestControllersRef.current.delete(controller);
      if (generation === generationRef.current) setPendingAction('');
    }
  }, [apiBase, applySnapshot, videoId]);

  return {
    operation,
    error,
    pendingAction,
    start,
    pause: () => control('pause'),
    resume: () => control('resume'),
    stop: () => control('stop'),
    refresh,
  };
}
