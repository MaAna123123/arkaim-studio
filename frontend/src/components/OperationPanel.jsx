import { formatEta, getOperationActions } from '../hooks/useOperation';


const STATUS_LABELS = {
  queued: 'В очереди',
  running: 'Выполняется',
  paused: 'На паузе',
  stopped: 'Остановлено — прогресс сохранён',
  completed: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменено',
};

export default function OperationPanel({
  operation,
  error,
  pendingAction,
  onPause,
  onResume,
  onStop,
}) {
  if (!operation && !error) return null;

  const actions = getOperationActions(operation?.status);
  const progress = Math.max(0, Math.min(Number(operation?.progress_pct) || 0, 100));
  const partialText = operation?.partial_result?.full_text || operation?.partial_result?.text || '';
  const run = (handler) => Promise.resolve(handler?.()).catch(() => {});

  return (
    <div className="glass-card operation-panel" aria-live="polite">
      <div className="operation-panel-header">
        <div>
          <div className="operation-panel-title">🎙️ Whisper распознаёт речь</div>
          <div className={`operation-status operation-status-${operation?.status || 'failed'}`}>
            {pendingAction === 'pause'
              ? 'Приостанавливается…'
              : pendingAction === 'stop'
                ? 'Останавливается…'
                : pendingAction === 'resume'
                  ? 'Продолжается…'
                  : STATUS_LABELS[operation?.status] || 'Подготовка'}
          </div>
        </div>
        <div className="operation-progress-value">{Math.round(progress)}%</div>
      </div>

      <p className="operation-message">
        {operation?.message || 'Подготавливаю локальное распознавание…'}
      </p>
      <div className="progress-bar-track">
        <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="operation-meta">
        <span>Осталось примерно: {formatEta(operation?.eta_seconds)}</span>
        {operation?.partial_result?.checkpoint > 0 && (
          <span>Обработано до {Math.round(operation.partial_result.checkpoint)} сек</span>
        )}
      </div>

      {partialText && (
        <div className="operation-partial">
          <strong>Уже распознано (неполный текст)</strong>
          <p>{partialText}</p>
        </div>
      )}

      {(error || operation?.error) && (
        <div className="operation-error">{error || operation.error}</div>
      )}

      {actions.length > 0 && (
        <div className="operation-actions">
          {actions.includes('pause') && (
            <button
              className="btn btn-secondary"
              disabled={Boolean(pendingAction)}
              onClick={() => run(onPause)}
            >
              ⏸ Пауза
            </button>
          )}
          {actions.includes('resume') && (
            <button
              className="btn btn-success"
              disabled={Boolean(pendingAction)}
              onClick={() => run(onResume)}
            >
              ▶ Продолжить
            </button>
          )}
          {actions.includes('stop') && (
            <button
              className="btn btn-danger"
              disabled={Boolean(pendingAction)}
              onClick={() => run(onStop)}
            >
              ⏹ Остановить с сохранением прогресса
            </button>
          )}
        </div>
      )}

      <p className="operation-hint">
        Прогресс хранится в памяти, пока запущен backend. После закрытия localhost он исчезнет.
      </p>
    </div>
  );
}
