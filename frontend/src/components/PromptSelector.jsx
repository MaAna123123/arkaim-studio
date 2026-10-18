import { useState } from 'react';

export default function PromptSelector({
  onAnalyze,
  onPrepareTranscript,
  loading,
  preparingTranscript = false,
  transcriptReady = false,
  disabled = false,
  subtitleContext,
}) {
  const [mode, setMode] = useState('ai'); // 'ai' | 'equal'
  const [chunkMinutes, setChunkMinutes] = useState(4.0);

  const handleRun = () => {
    if (mode === 'equal') {
      onAnalyze({ mode: 'equal_chunks', chunk_minutes: chunkMinutes });
    } else {
      onAnalyze({ mode: 'transcript' });
    }
  };

  return (
    <div className="auto-highlight-panel">
      {/* Mode Switcher Tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'ai' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('ai')}
        >
          🧠 ИИ-поиск моментов
        </button>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'equal' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('equal')}
        >
          ⏱ Нарезка на равные части (сериалы)
        </button>
      </div>

      {mode === 'ai' ? (
        <div className="auto-highlight-copy">
          <div className="auto-highlight-kicker">Автоматический ИИ-монтаж</div>
          <p>
            ИИ сам оценит смысл, эмоции, юмор, конфликт и динамику, выберет
            сильнейшие законченные фрагменты и подготовит клипы для скачивания.
          </p>
        </div>
      ) : (
        <div className="auto-highlight-copy">
          <div className="auto-highlight-kicker">Разбиение всего видео на серии/части</div>
          <p>
            Разбивает всё видео целиком от 00:00 до конца на равные кусочки выбранной длины.
            Отлично подходит для нарезки сериалов, фильмов и длинных подкастов.
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Длительность одной части:</span>
            <select
              className="input"
              style={{ width: 120, padding: '6px 10px', fontSize: 13 }}
              value={chunkMinutes}
              onChange={(e) => setChunkMinutes(parseFloat(e.target.value))}
            >
              <option value={1.0}>1 минута</option>
              <option value={2.0}>2 минуты</option>
              <option value={3.0}>3 минуты</option>
              <option value={4.0}>4 минуты</option>
              <option value={5.0}>5 минут</option>
              <option value={10.0}>10 минут</option>
            </select>
          </div>
        </div>
      )}

      {subtitleContext && (
        <div className={`subtitle-context-badge ${subtitleContext.kind === 'chapters' ? 'chapters' : 'full'}`}>
          <span>{subtitleContext.kind === 'chapters' ? '🗺️' : '📄'}</span>
          <div>
            <strong>{subtitleContext.kind === 'chapters' ? 'Карта глав подключена' : 'Полный SRT подключён'}</strong>
            <small>{subtitleContext.filename}</small>
          </div>
        </div>
      )}

      {subtitleContext?.kind === 'chapters' && !transcriptReady && mode === 'ai' && (
        <button
          className="btn btn-secondary auto-highlight-button"
          onClick={onPrepareTranscript}
          disabled={preparingTranscript || disabled}
        >
          {preparingTranscript ? (
            <><div className="spinner" /> Готовлю выделяемый текст...</>
          ) : (
            <>📝 Подготовить текст для ручной нарезки</>
          )}
        </button>
      )}

      <button
        id="btn-analyze"
        className="btn btn-primary btn-lg auto-highlight-button"
        onClick={handleRun}
        disabled={loading || preparingTranscript || disabled}
        style={mode === 'equal' ? { background: 'linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%)' } : {}}
      >
        {loading ? (
          <><div className="spinner" /> Нарезаю на части...</>
        ) : mode === 'equal' ? (
          <>✂️ Разбить всё видео по {chunkMinutes} мин</>
        ) : (
          <>✨ Найти и нарезать лучшие моменты</>
        )}
      </button>
    </div>
  );
}

