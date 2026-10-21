import { useMemo, useRef, useState } from 'react';
import CompositionCanvas from './CompositionCanvas.jsx';
import { compositionDuration, createDefaultComposition, normalizeComposition, updateCompositionLayer } from '../lib/clipComposition.js';

const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
const round = (value) => Math.round(value * 1000) / 1000;

function fmtTime(sec) {
  const safe = Number.isFinite(Number(sec)) ? Math.max(0, Number(sec)) : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = Math.floor(safe % 60);
  const tenths = Math.floor((safe % 1) * 10);
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${tenths}`;
}

function defaultOverlay(duration, extra = {}) {
  return { enabled: true, destination: { x: 0, y: 0, width: 1, height: 0.25 }, fit: 'contain', start: 0, end: Math.max(0.5, duration), ...extra };
}

const mediaMatchesLayer = (layer, media) => Boolean(
  media?.url && media?.revision && layer?.asset_revision === media.revision
);

function clampOverlay(layer, duration) {
  if (!layer) return null;
  const end = clamp(Number(layer.end) || duration, 0.5, duration);
  const start = clamp(Number(layer.start) || 0, 0, Math.max(0, end - 0.1));
  return { ...layer, start: round(start), end: round(end) };
}

export default function ClipComposer({ segment, index = 0, sourceDuration = 0, sourceUrl = '', sourceWidth = 0, sourceHeight = 0, outputAspectRatio = '9:16', bannerMedia = null, adMedia = null, transcript = null, onPreview, onSave, onCancel }) {
  const durationLimit = Math.max(Number(sourceDuration) || Number(segment.end) || 0, Number(segment.end) || 0);
  const initial = normalizeComposition(segment.composition, segment, durationLimit) || createDefaultComposition(segment, outputAspectRatio);
  const [composition, setComposition] = useState(initial);
  const [selectedLayer, setSelectedLayer] = useState('main_layer');
  const [label, setLabel] = useState(segment.label || '');
  const [selectedText, setSelectedText] = useState(segment.transcript_text || '');
  const wordsRef = useRef(null);
  const duration = compositionDuration(composition);
  const activeLayer = composition[selectedLayer];
  const activeRect = activeLayer?.destination;
  const contextWords = useMemo(() => (transcript?.words || []).filter((word) => word.end >= composition.source_start - 15 && word.start <= composition.source_end + 15), [transcript, composition.source_start, composition.source_end]);
  const bannerMatches = mediaMatchesLayer(composition.banner_layer, bannerMedia);
  const adMatches = mediaMatchesLayer(composition.ad_layer, adMedia);

  const setSourceRange = (startValue, endValue) => {
    const start = clamp(Number(startValue), 0, Math.max(0, durationLimit - 0.5));
    const end = clamp(Number(endValue), start + 0.5, durationLimit);
    const nextDuration = end - start;
    setComposition((current) => ({ ...current, source_start: round(start), source_end: round(end), banner_layer: clampOverlay(current.banner_layer, nextDuration), ad_layer: clampOverlay(current.ad_layer, nextDuration) }));
    onPreview?.(start);
  };

  const captureTranscriptRange = () => {
    const selection = window.getSelection();
    const root = wordsRef.current;
    if (!selection || selection.isCollapsed || !selection.rangeCount || !root) return;
    const range = selection.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) return;
    const elementFor = (node) => (node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement)?.closest?.('[data-composer-word]');
    const firstElement = elementFor(range.startContainer);
    const lastElement = elementFor(range.endContainer);
    if (!firstElement || !lastElement) return;
    const first = Math.min(Number(firstElement.dataset.composerWord), Number(lastElement.dataset.composerWord));
    const last = Math.max(Number(firstElement.dataset.composerWord), Number(lastElement.dataset.composerWord));
    const words = contextWords.slice(first, last + 1);
    if (!words.length) return;
    setSourceRange(words[0].start, words[words.length - 1].end);
    setSelectedText(words.map((word) => word.word).join(' ').replace(/\s+([,.!?;:])/g, '$1').trim());
    selection.removeAllRanges();
  };

  const enableLayer = (name) => {
    const media = name === 'ad_layer' ? adMedia : bannerMedia;
    if (!media?.url || !media?.revision) return;
    const extra = name === 'ad_layer' ? { mode: 'overlay' } : {};
    setComposition((current) => updateCompositionLayer(current, name, {
      ...(current[name] || defaultOverlay(compositionDuration(current), extra)),
      enabled: true,
      asset_revision: media.revision,
    }));
    setSelectedLayer(name);
  };

  const updateRect = (key, percentValue) => {
    if (!activeRect) return;
    const value = Number(percentValue) / 100;
    if (!Number.isFinite(value)) return;
    const rect = { ...activeRect };
    if (key === 'x') rect.x = clamp(value, 0, 1 - rect.width);
    if (key === 'y') rect.y = clamp(value, 0, 1 - rect.height);
    if (key === 'width') rect.width = clamp(value, 0.03, 1 - rect.x);
    if (key === 'height') rect.height = clamp(value, 0.03, 1 - rect.y);
    setComposition(updateCompositionLayer(composition, selectedLayer, { destination: rect }));
  };

  const updateCrop = (key, percentValue) => {
    const crop = { ...composition.main_layer.crop };
    const value = Number(percentValue) / 100;
    if (!Number.isFinite(value)) return;
    if (key === 'x') crop.x = clamp(value, 0, 1 - crop.width);
    if (key === 'y') crop.y = clamp(value, 0, 1 - crop.height);
    if (key === 'width') crop.width = clamp(value, 0.03, 1 - crop.x);
    if (key === 'height') crop.height = clamp(value, 0.03, 1 - crop.y);
    setComposition(updateCompositionLayer(composition, 'main_layer', { crop }));
  };

  const updateTiming = (key, value) => {
    if (!activeLayer || selectedLayer === 'main_layer') return;
    setComposition(updateCompositionLayer(composition, selectedLayer, clampOverlay({ ...activeLayer, [key]: Number(value) }, duration)));
  };

  const save = () => onSave?.(index, { ...segment, start: composition.source_start, end: composition.source_end, label, transcript_text: selectedText, composition });
  const reset = () => { setComposition(createDefaultComposition(segment, outputAspectRatio)); setSelectedLayer('main_layer'); };
  const resetToLegacy = () => {
    const { composition: _discardedComposition, ...legacySegment } = segment;
    onSave?.(index, legacySegment);
  };

  return (
    <div className="modal-overlay composition-modal-overlay" onMouseDown={(event) => event.target === event.currentTarget && onCancel?.()}>
      <div className="modal-box clip-composer-modal" role="dialog" aria-modal="true" aria-label={`Редактор клипа ${index + 1}`}>
        <div className="segment-editor-header"><div><div className="arkaim-eyebrow">КОМПОЗИТОР КЛИПА</div><h3>Редактирование момента #{index + 1}</h3></div><button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>✕</button></div>
        <div className="clip-composer-grid">
          <div className="clip-composer-preview-column">
            <CompositionCanvas sourceUrl={sourceUrl} previewTime={composition.source_start} composition={composition} selectedLayer={selectedLayer} bannerMedia={bannerMatches ? bannerMedia : null} adMedia={adMatches ? adMedia : null} sourceWidth={sourceWidth} sourceHeight={sourceHeight} onCompositionChange={setComposition} />
            <div className="composition-layer-tabs">
              <button type="button" className={`btn btn-sm ${selectedLayer === 'main_layer' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSelectedLayer('main_layer')}>Основное видео</button>
              <button id="btn-enable-banner-layer" type="button" disabled={!bannerMedia?.url || !bannerMedia?.revision} className={`btn btn-sm ${selectedLayer === 'banner_layer' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => enableLayer('banner_layer')}>Видео-баннер</button>
              <button id="btn-enable-ad-layer" type="button" disabled={!adMedia?.url || !adMedia?.revision} className={`btn btn-sm ${selectedLayer === 'ad_layer' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => enableLayer('ad_layer')}>Видео-реклама</button>
            </div>
            {composition.banner_layer && !bannerMatches && <small className="composition-media-warning">Баннер был удалён или заменён. Выберите «Видео-баннер» заново.</small>}
            {composition.ad_layer && !adMatches && <small className="composition-media-warning">Реклама была удалена или заменена. Выберите «Видео-реклама» заново.</small>}
            {!bannerMedia?.url && !adMedia?.url && <small className="composition-media-hint">Загрузите баннер или рекламу ниже на странице, чтобы добавить видеослой.</small>}
          </div>
          <div className="clip-composer-controls">
            <label className="settings-label">Название клипа</label><input className="input" value={label} onChange={(event) => setLabel(event.target.value)} />
            <section className="composition-control-section">
              <div className="composition-section-title"><strong>Длительность исходного момента</strong><span>{duration.toFixed(1)} сек.</span></div>
              <div className="composition-range-track"><input type="range" min="0" max={durationLimit} step="0.1" value={composition.source_start} onChange={(event) => setSourceRange(event.target.value, composition.source_end)} /><input type="range" min="0" max={durationLimit} step="0.1" value={composition.source_end} onChange={(event) => setSourceRange(composition.source_start, event.target.value)} /></div>
              <div className="composition-time-inputs"><label>Начало<input id="composition-source-start" type="number" className="input" min="0" max={composition.source_end - 0.5} step="0.1" value={composition.source_start} onChange={(event) => setSourceRange(event.target.value, composition.source_end)} /></label><label>Конец<input id="composition-source-end" type="number" className="input" min={composition.source_start + 0.5} max={durationLimit} step="0.1" value={composition.source_end} onChange={(event) => setSourceRange(composition.source_start, event.target.value)} /></label></div>
              <div className="composition-time-summary">{fmtTime(composition.source_start)} → {fmtTime(composition.source_end)}</div>
            </section>
            {activeRect && <section className="composition-control-section">
              <div className="composition-section-title"><strong>Положение и размер</strong><span>проценты кадра</span></div>
              <div className="composition-number-grid">{['x', 'y', 'width', 'height'].map((key) => <label key={key}>{key.toUpperCase()}<input id={`composition-rect-${key}`} type="number" className="input" min="0" max="100" step="1" value={Math.round(activeRect[key] * 100)} onChange={(event) => updateRect(key, event.target.value)} /></label>)}</div>
              <label className="composition-inline-control">Заполнение<select className="input" value={activeLayer.fit} onChange={(event) => setComposition(updateCompositionLayer(composition, selectedLayer, { fit: event.target.value }))}><option value="cover">Заполнить с обрезкой</option><option value="contain">Вместить целиком</option>{selectedLayer !== 'main_layer' && <option value="stretch">Растянуть свободно</option>}</select></label>
              {selectedLayer !== 'main_layer' && <><div className="composition-time-inputs"><label>Показывать с<input type="number" className="input" min="0" max={duration - 0.1} step="0.1" value={activeLayer.start} onChange={(event) => updateTiming('start', event.target.value)} /></label><label>До<input type="number" className="input" min="0.1" max={duration} step="0.1" value={activeLayer.end} onChange={(event) => updateTiming('end', event.target.value)} /></label></div><button type="button" className="btn btn-secondary btn-sm" onClick={() => setComposition(updateCompositionLayer(composition, selectedLayer, { start: 0, end: duration }))}>Весь клип</button>{selectedLayer === 'ad_layer' && <label className="composition-inline-control">Режим рекламы<select className="input" value={activeLayer.mode} onChange={(event) => setComposition(updateCompositionLayer(composition, 'ad_layer', { mode: event.target.value }))}><option value="overlay">В свободной области</option><option value="insert">Отдельная вставка</option></select></label>}<button type="button" className="btn btn-danger btn-sm" onClick={() => { setComposition(updateCompositionLayer(composition, selectedLayer, null)); setSelectedLayer('main_layer'); }}>Убрать слой</button></>}
            </section>}
            {selectedLayer === 'main_layer' && <section className="composition-control-section"><div className="composition-section-title"><strong>Обрезка исходного видео</strong><span>проценты</span></div><div className="composition-number-grid">{['x', 'y', 'width', 'height'].map((key) => <label key={key}>{key.toUpperCase()}<input id={`composition-crop-${key}`} type="number" className="input" min="0" max="100" value={Math.round(composition.main_layer.crop[key] * 100)} onChange={(event) => updateCrop(key, event.target.value)} /></label>)}</div></section>}
          </div>
        </div>
        {contextWords.length > 0 && <div className="segment-editor-text-block"><div className="segment-editor-text-head"><strong>Точный выбор по словам</strong><small>Выделите мышкой фразу — границы клипа обновятся</small></div><div ref={wordsRef} className="segment-editor-words" onMouseUp={captureTranscriptRange}>{contextWords.map((word, wordIndex) => <span key={`${word.start}-${wordIndex}`} data-composer-word={wordIndex} className={word.end >= composition.source_start && word.start <= composition.source_end ? 'included' : ''}>{word.word}{' '}</span>)}</div></div>}
        <div className="segment-editor-footer"><div><button type="button" className="btn btn-ghost" onClick={reset}>Сбросить поля</button><button id="btn-reset-legacy-composition" type="button" className="btn btn-ghost" onClick={resetToLegacy}>Обычный экспорт без компоновки</button></div><div><button type="button" className="btn btn-secondary" onClick={onCancel}>Отмена</button><button id="btn-save-composition" type="button" className="btn btn-primary" onClick={save}>✓ Сохранить клип</button></div></div>
      </div>
    </div>
  );
}
