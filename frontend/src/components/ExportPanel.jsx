import { useEffect, useState, useRef } from 'react';

function extractWords(transcript) {
  if (!transcript) return [];
  if (transcript.words && transcript.words.length > 0) {
    return transcript.words;
  }
  if (transcript.segments && transcript.segments.length > 0) {
    const flatWords = [];
    for (const seg of transcript.segments) {
      if (seg.words && seg.words.length > 0) {
        flatWords.push(...seg.words);
      } else if (seg.text && seg.start !== undefined && seg.end !== undefined) {
        const textWords = seg.text.trim().split(/\s+/).filter(Boolean);
        const segDur = Math.max(0.4, Number(seg.end) - Number(seg.start));
        const wStep = segDur / Math.max(1, textWords.length);
        textWords.forEach((wordStr, idx) => {
          flatWords.push({
            word: wordStr,
            start: Number(seg.start) + idx * wStep,
            end: Number(seg.start) + (idx + 1) * wStep,
          });
        });
      }
    }
    return flatWords;
  }
  return [];
}

function getActiveSubtitle(transcript, currentTime) {
  const words = extractWords(transcript);
  if (!words || words.length === 0) return null;

  const phrases = [];
  let currentPhrase = [];

  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    currentPhrase.push(w);

    const wordStr = (w.word || '').trim();
    const endsWithPunct = /[.!?;\u2026:]$/.test(wordStr);
    const nextWord = words[i + 1];
    const pauseToNext = nextWord ? (Number(nextWord.start) - Number(w.end)) : 0;

    if (endsWithPunct || pauseToNext > 0.6 || currentPhrase.length >= 3) {
      phrases.push(currentPhrase);
      currentPhrase = [];
    }
  }
  if (currentPhrase.length > 0) {
    phrases.push(currentPhrase);
  }

  let active = phrases.find(phrase => {
    if (!phrase.length) return false;
    const pStart = Number(phrase[0].start);
    const pEnd = Number(phrase[phrase.length - 1].end) + 0.3;
    return currentTime >= pStart && currentTime <= pEnd;
  });

  if (!active) {
    for (let i = 0; i < phrases.length; i++) {
      const p = phrases[i];
      const pStart = Number(p[0].start);
      const pEnd = Number(p[p.length - 1].end);
      const nextP = phrases[i + 1];
      const nextStart = nextP ? Number(nextP[0].start) : pEnd + 999;

      if (currentTime >= pEnd && currentTime < nextStart && (nextStart - pEnd) <= 1.0) {
        active = p;
        break;
      }
    }
  }

  if (!active || !active.length) return null;
  return active.map(w => w.word).join(' ').trim();
}

export default function ExportPanel({
  selectedCount,
  exporting,
  onExport,
  clipResults,
  padding,
  onPaddingChange,
  aspectRatio,
  onAspectRatioChange,
  burnSubtitles,
  onBurnSubtitlesChange,
  insertAd,
  onInsertAdChange,
  hasAd,
  textAdEnabled,
  onTextAdEnabledChange,
  textAdText,
  onTextAdTextChange,
  textAdPosition,
  onTextAdPositionChange,
  textAdInterval,
  onTextAdIntervalChange,
  textAdOpacity,
  onTextAdOpacityChange,
  videoBannerEnabled,
  onVideoBannerEnabledChange,
  hasVideoBanner,
  videoBannerHeight,
  onVideoBannerHeightChange,
  apiBase,
  onDownloadClip,
  transcript,
}) {
  const [previewClip, setPreviewClip] = useState(null);
  const previewVideoRef = useRef(null);
  const [previewTime, setPreviewTime] = useState(0);

  const handleDownload = async (clip) => {
    try {
      await onDownloadClip(clip);
    } catch (error) {
      window.alert(`Ошибка скачивания: ${error.message}`);
    }
  };

  useEffect(() => {
    if (!previewClip) return undefined;

    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setPreviewClip(null);
    };

    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [previewClip]);

function findClipStart(previewClip, transcript) {
  if (!previewClip) return 0;
  if (typeof previewClip.start === 'number' && previewClip.start > 0) {
    return previewClip.start;
  }
  if (previewClip.label && transcript?.segments) {
    const labelClean = previewClip.label.toLowerCase().trim();
    const matchedSeg = transcript.segments.find(s =>
      s.text && (s.text.toLowerCase().includes(labelClean) || labelClean.includes(s.text.toLowerCase()))
    );
    if (matchedSeg) return matchedSeg.start;
  }
  return 0;
}

  // Compute active subtitle for preview video player modal
  let activePreviewSub = null;
  if (burnSubtitles && previewClip && transcript) {
    const clipStart = findClipStart(previewClip, transcript);
    activePreviewSub = getActiveSubtitle(transcript, previewTime + clipStart);
  }

  return (
    <>
    <div className="glass-card">
      <div className="export-panel">
        <div className="export-info" style={{ flexWrap: 'wrap', gap: 16 }}>
          <div>
            <div className="export-count">{selectedCount}</div>
            <div className="export-label">клипов выбрано</div>
          </div>

          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Формат видео
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                id="btn-ratio-original"
                className={`btn btn-sm ${aspectRatio === 'original' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => onAspectRatioChange('original')}
              >
                📺 Оригинальный формат
              </button>
              <button
                id="btn-ratio-shorts"
                className={`btn btn-sm ${aspectRatio === '9:16' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => onAspectRatioChange('9:16')}
              >
                📱 Shorts: весь кадр (9:16)
              </button>
            </div>
          </div>

          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Субтитры на видео
            </div>
            <button
              id="btn-toggle-subtitles"
              className={`btn btn-sm ${burnSubtitles ? 'btn-success' : 'btn-secondary'}`}
              onClick={() => onBurnSubtitlesChange(!burnSubtitles)}
            >
              {burnSubtitles ? '💬 Субтитры ВКЛ (Vizard)' : '💬 Без субтитров'}
            </button>
          </div>

          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Видео реклама
            </div>
            <button
              id="btn-toggle-ad"
              className={`btn btn-sm ${insertAd && hasAd ? 'btn-success' : 'btn-secondary'}`}
              onClick={() => onInsertAdChange?.(!insertAd)}
              disabled={!hasAd}
              title={hasAd ? (insertAd ? 'Реклама будет вставлена в середину' : 'Нажмите для включения') : 'Сначала загрузите рекламный ролик'}
            >
              {!hasAd ? '📺 Нет видео-рекламы' : insertAd ? '📺 Видео-реклама ВКЛ' : '📺 Видео-реклама ВЫКЛ'}
            </button>
          </div>

          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Видео-баннер (сверху)
            </div>
            <button
              id="btn-toggle-video-banner"
              className={`btn btn-sm ${videoBannerEnabled && hasVideoBanner ? 'btn-success' : 'btn-secondary'}`}
              onClick={() => onVideoBannerEnabledChange?.(!videoBannerEnabled)}
              disabled={!hasVideoBanner}
              title={hasVideoBanner ? (videoBannerEnabled ? 'Мини-видео баннер включён' : 'Нажмите для включения') : 'Сначала загрузите видео-баннер'}
            >
              {!hasVideoBanner ? '🎥 Нет видео-баннера' : videoBannerEnabled ? '🎥 Видео-баннер ВКЛ' : '🎥 Видео-баннер ВЫКЛ'}
            </button>
          </div>

          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Текстовая реклама (Оверлей)
            </div>
            <button
              id="btn-toggle-text-ad"
              className={`btn btn-sm ${textAdEnabled ? 'btn-success' : 'btn-secondary'}`}
              onClick={() => onTextAdEnabledChange(!textAdEnabled)}
              title="Добавить полупрозрачный плавающий рекламный текст сверху клипов"
            >
              {textAdEnabled ? '📢 Плашка с текстом ВКЛ' : '📢 Плашка с текстом ВЫКЛ'}
            </button>
          </div>

          <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Отступ (сек)
            </div>
            <div className="export-padding-row">
              <input
                id="export-padding-input"
                type="number"
                className="input export-padding-input"
                min="0"
                max="10"
                step="0.5"
                value={padding}
                onChange={(event) => onPaddingChange(+event.target.value)}
              />
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>± секунд</span>
            </div>
          </div>
        </div>

        <div className="export-actions">
          <button
            id="btn-export-separate"
            className="btn btn-secondary"
            disabled={selectedCount === 0 || exporting}
            onClick={() => onExport('separate')}
          >
            {exporting ? <span style={{display:'flex',alignItems:'center',gap:8}}><div className="spinner" /> Нарезаю...</span> : <span>✂️ Нарезать клипы</span>}
          </button>
          <button
            id="btn-export-concat"
            className="btn btn-primary"
            disabled={selectedCount === 0 || exporting}
            onClick={() => onExport('concat')}
          >
            {exporting ? <span style={{display:'flex',alignItems:'center',gap:8}}><div className="spinner" /> Склеиваю...</span> : <span>🎬 Склеить в один ролик</span>}
          </button>
        </div>
      </div>

      {/* ── Текстовый Рекламный Оверлей Настройки ── */}
      {textAdEnabled && (
        <div style={{
          margin: '0 24px 20px',
          padding: '16px 20px',
          background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(168, 85, 247, 0.08) 100%)',
          borderRadius: 12,
          border: '1px solid rgba(168, 85, 247, 0.25)',
          display: 'flex',
          flexDirection: 'column',
          gap: 12
        }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>📢 Настройки полупрозрачной рекламной плашки (водяного знака)</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 20 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                Текст рекламы (по 1 фразе на строку — плашка будет автоматически сменяться):
              </label>
              <textarea
                id="text-ad-textarea"
                className="input"
                rows={3}
                value={textAdText}
                onChange={(e) => onTextAdTextChange(e.target.value)}
                placeholder="Подключайся к ArkaimVPN&#10;Промокод: MOVIE2026 (-50%)&#10;Ссылка в описании 🚀"
                style={{ width: '100%', resize: 'vertical', fontSize: 13, lineHeight: '1.5', fontFamily: 'inherit' }}
              />
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                💡 Если указать несколько строк, каждая фраза будет автоматически отображаться по очереди через заданный интервал.
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, justifyContent: 'center' }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>
                  Расположение на видео:
                </label>
                <select
                  id="select-text-ad-position"
                  className="input"
                  value={textAdPosition}
                  onChange={(e) => onTextAdPositionChange(e.target.value)}
                  style={{ width: '100%', fontSize: 13 }}
                >
                  <option value="top">⬆️ Сверху (как в Reels / Shorts)</option>
                  <option value="center">⏺️ По центру кадра</option>
                  <option value="bottom">⬇️ Внизу кадра</option>
                </select>
              </div>

              <div style={{ display: 'flex', gap: 16 }}>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>
                    Интервал смены:
                  </label>
                  <select
                    id="select-text-ad-interval"
                    className="input"
                    value={textAdInterval}
                    onChange={(e) => onTextAdIntervalChange(Number(e.target.value))}
                    style={{ width: '100%', fontSize: 13 }}
                  >
                    <option value={3}>3 секунды</option>
                    <option value={4}>4 секунды (стандарт)</option>
                    <option value={5}>5 секунд</option>
                    <option value={6}>6 секунд</option>
                  </select>
                </div>

                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>
                    Прозрачность: {Math.round(textAdOpacity * 100)}%
                  </label>
                  <input
                    id="range-text-ad-opacity"
                    type="range"
                    min="0.3"
                    max="1.0"
                    step="0.05"
                    value={textAdOpacity}
                    onChange={(e) => onTextAdOpacityChange(Number(e.target.value))}
                    style={{ width: '100%', marginTop: 8, cursor: 'pointer' }}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Видео-Баннер Настройки ── */}
      {videoBannerEnabled && hasVideoBanner && (
        <div style={{
          margin: '0 24px 20px',
          padding: '16px 20px',
          background: 'linear-gradient(135deg, rgba(14, 165, 233, 0.08) 0%, rgba(6, 182, 212, 0.08) 100%)',
          borderRadius: 12,
          border: '1px solid rgba(14, 165, 233, 0.3)',
          display: 'flex',
          flexDirection: 'column',
          gap: 14
        }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span>🎥 Настройки размера рекламного видео-баннера</span>
            <span style={{ fontSize: 12, padding: '2px 10px', borderRadius: 12, background: 'var(--accent-glow, rgba(14,165,233,0.2))', color: 'var(--accent, #0ea5e9)', fontWeight: 700 }}>
              Текущий размер: {videoBannerHeight} px
            </span>
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Зацикленный баннер накладывается в верхней чёрной области кадра 9:16. По умолчанию размер увеличен до <strong>380 px</strong>, чтобы он занимал всё верхнее поле и был отлично виден!
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>
                  Быстрый выбор размера:
                </label>
                <select
                  id="select-banner-height"
                  className="input"
                  value={videoBannerHeight}
                  onChange={(e) => onVideoBannerHeightChange(Number(e.target.value))}
                  style={{ fontSize: 13, minWidth: 260 }}
                >
                  <option value={200}>200 px — Компактный (маленький)</option>
                  <option value={280}>280 px — Средний</option>
                  <option value={350}>350 px — Большой</option>
                  <option value={380}>380 px — На всё верхнее поле (РЕКОМЕНДУЕТСЯ 🔥)</option>
                  <option value={430}>430 px — Огромный</option>
                  <option value={500}>500 px — Максимальный</option>
                </select>
              </div>

              <div style={{ flex: 1, minWidth: 220 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>Плавная регулировка высоты:</span>
                  <span style={{ color: 'var(--accent, #0ea5e9)' }}>{videoBannerHeight}px</span>
                </label>
                <input
                  type="range"
                  min={150}
                  max={520}
                  step={10}
                  value={videoBannerHeight}
                  onChange={(e) => onVideoBannerHeightChange(Number(e.target.value))}
                  style={{ width: '100%', cursor: 'pointer', accentColor: '#0ea5e9' }}
                />
              </div>
            </div>

            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              💡 Чем больше высота (например 380–430 px), тем более заметным и крупным будет ваш баннер в готовых Shorts.
            </div>
          </div>
        </div>
      )}

      {clipResults.length > 0 && (
        <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--success)', marginBottom: 4 }}>
            ✅ Готово! Клипы нарезаны:
          </div>
          {clipResults.map((clip, index) => (
            <div key={`${clip.url}-${index}`} className="clip-result-card">
              <div>
                <div className="clip-result-name">🎬 {clip.label}</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{apiBase}{clip.url}</div>
              </div>
              <div className="clip-result-actions">
                <button
                  id={`btn-preview-clip-${index}`}
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setPreviewClip(clip)}
                >
                  ▶ Смотреть
                </button>
                <button
                  id={`btn-download-clip-${index}`}
                  type="button"
                  className="btn btn-success btn-sm"
                  onClick={() => handleDownload(clip)}
                >
                  ⬇ Скачать клип
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {previewClip && (
        <div
          className="modal-overlay clip-preview-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPreviewClip(null);
          }}
        >
          <div className="clip-preview-modal" role="dialog" aria-modal="true" aria-label={`Просмотр ${previewClip.label}`}>
            <div className="clip-preview-header">
              <strong>{previewClip.label}</strong>
              <button
                type="button"
                className="clip-preview-close"
                aria-label="Закрыть просмотр"
                onClick={() => setPreviewClip(null)}
              >
                ✕
              </button>
            </div>
            
            <div className="clip-preview-video-wrap">
              <video
                ref={previewVideoRef}
                className="clip-preview-video"
                src={`${apiBase}${previewClip.url}`}
                controls
                autoPlay
                playsInline
                onTimeUpdate={() => setPreviewTime(previewVideoRef.current?.currentTime || 0)}
              />

              {activePreviewSub && (
                <div style={{
                  position: 'absolute',
                  bottom: '6%',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  color: '#FFFFFF',
                  fontWeight: 800,
                  fontSize: '20px',
                  letterSpacing: '0.02em',
                  textAlign: 'center',
                  maxWidth: '85%',
                  pointerEvents: 'none',
                  zIndex: 30,
                  lineHeight: 1.25,
                  fontFamily: '"Montserrat", "Inter", system-ui, sans-serif',
                  paintOrder: 'stroke fill',
                  WebkitPaintOrder: 'stroke fill',
                  WebkitTextStroke: '3px #000000',
                  filter: 'drop-shadow(0px 3px 6px rgba(0, 0, 0, 0.95))',
                }}>
                  {activePreviewSub}
                </div>
              )}
            </div>

            <div className="clip-preview-footer">
              <button type="button" className="btn btn-success btn-sm" onClick={() => handleDownload(previewClip)}>
                ⬇ Скачать
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </>
  );
}
