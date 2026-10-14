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
  if (currentOperation?.operation_id) {
    return event.operation_id === currentOperation.operation_id;
  }
  return event.operation_type === 'transcription';
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

  const applySnapshot = useCallback((snapshot) => {
    if (!snapshot?.operation_id) return;
    operationRef.current = snapshot;
    setOperation(snapshot);
    setError(snapshot.error || '');
    if (snapshot.status === 'completed' && completedRef.current !== snapshot.operation_id) {
      completedRef.current = snapshot.operation_id;
      onCompleted?.(snapshot);
    }
  }, [onCompleted]);

  const refresh = useCallback(async (operationId = operationRef.current?.operation_id) => {
    if (!operationId) return null;
    const response = await fetch(`${apiBase}/api/operations/${operationId}`);
    const snapshot = await readResponse(response);
    applySnapshot(snapshot);
    return snapshot;
  }, [apiBase, applySnapshot]);

  useEffect(() => {
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
        if (restored) applySnapshot(restored);
      } catch (restoreError) {
        if (restoreError.name !== 'AbortError') setError(restoreError.message);
      }
    };
    restore();
    return () => controller.abort();
  }, [apiBase, videoId, applySnapshot]);

  useEffect(() => {
    if (!videoId) return undefined;
    let disposed = false;
    let reconnectTimer = null;
    let socket = null;

    const connect = () => {
      const baseUrl = new URL(apiBase, window.location.href);
      const protocol = baseUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(`${protocol}//${baseUrl.host}/ws/progress`);
      socket.onopen = () => setSocketConnected(true);
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data);
          if (matchesOperationEvent(event, operationRef.current, videoId)) {
            applySnapshot(event);
          }
        } catch {
          // Ignore malformed progress events and recover through status polling.
        }
      };
      socket.onclose = () => {
        setSocketConnected(false);
        if (!disposed) reconnectTimer = window.setTimeout(connect, 3000);
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
      refresh().catch((refreshError) => setError(refreshError.message));
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [operation?.operation_id, operation?.status, socketConnected, refresh]);

  const start = useCallback(async () => {
    if (!videoId) return null;
    setError('');
    setPendingAction('start');
    try {
      const response = await fetch(`${apiBase}/api/video/transcribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: videoId }),
      });
      const data = await readResponse(response);
      if (data?.operation_id) applySnapshot(data);
      else onCompleted?.(data);
      return data;
    } catch (startError) {
      setError(startError.message);
      throw startError;
    } finally {
      setPendingAction('');
    }
  }, [apiBase, videoId, applySnapshot, onCompleted]);

  const control = useCallback(async (action) => {
    const operationId = operationRef.current?.operation_id;
    if (!operationId) return null;
    setError('');
    setPendingAction(action);
    try {
      const response = await fetch(`${apiBase}/api/operations/${operationId}/${action}`, {
        method: 'POST',
      });
      const snapshot = await readResponse(response);
      applySnapshot(snapshot);
      return snapshot;
    } catch (controlError) {
      setError(controlError.message);
      throw controlError;
    } finally {
      setPendingAction('');
    }
  }, [apiBase, applySnapshot]);

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
