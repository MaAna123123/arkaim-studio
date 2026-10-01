import { useMemo, useRef, useState } from 'react';

function fmtTime(sec) {
  if (!sec && sec !== 0) return '00:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const ms = Math.floor((sec % 1) * 10);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${ms}`;
}

function joinWords(words) {
  return words.map((word) => word.word).join(' ').replace(/\s+([,.!?;:])/g, '$1').trim();
}

function closestWord(node, root) {
  const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  const direct = element?.closest?.('[data-editor-word]');
  return direct && root.contains(direct) ? direct : null;
}

export default function SegmentEditor({ segment, index, duration, transcript, onSave, onCancel, onPreview }) {
  const [start, setStart] = useState(segment.start);
  const [end, setEnd] = useState(segment.end);
  const [label, setLabel] = useState(segment.label || '');
  const [selectedText, setSelectedText] = useState(segment.transcript_text || '');
  const textRef = useRef(null);

  const contextWords = useMemo(() => {
    const words = transcript?.words || [];
    return words.filter((word) => word.end >= segment.start - 15 && word.start <= segment.end + 15);
  }, [transcript, segment.start, segment.end]);

  const dur = Math.max(0.1, end - start).toFixed(1);

  const captureTextRange = () => {
    const selection = window.getSelection();
    const root = textRef.current;
    if (!selection || selection.isCollapsed || !selection.rangeCount || !root) return;
    const range = selection.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) return;
    const startNode = closestWord(range.startContainer, root);
    const endNode = closestWord(range.endContainer, root);
    if (!startNode || !endNode) return;
    const first = Math.min(Number(startNode.dataset.editorWord), Number(endNode.dataset.editorWord));
    const last = Math.max(Number(startNode.dataset.editorWord), Number(endNode.dataset.editorWord));
    const words = contextWords.slice(first, last + 1);
    if (!words.length) return;
    setStart(words[0].start);
    setEnd(words[words.length - 1].end);
    setSelectedText(joinWords(words));
    onPreview?.(words[0].start);
    selection.removeAllRanges();
  };

  const handleStartShift = (delta) => {
    const next = Math.max(0, Math.min(end - 0.5, start + delta));
    setStart(next);
    onPreview?.(next);
  };

  const handleEndShift = (delta) => {
    const next = Math.min(duration || 99999, Math.max(start + 0.5, end + delta));
    setEnd(next);
    onPreview?.(Math.max(start, next - 2));
  };

  return (
    <div className="modal-overlay" onClick={(event) => event.target === event.currentTarget && onCancel()}>
      <div className="modal-box segment-editor-modal">
        <div className="segment-editor-header">
          <h3>✏️ Редактирование клипа #{index + 1}</h3>
          <button className="btn btn-ghost btn-sm" onClick={onCancel}>✕</button>
        </div>

        <label className="settings-label">Заголовок над видео</label>
        <input className="input" value={label} onChange={(event) => setLabel(event.target.value)} />

        <div className="segment-editor-text-block">
          <div className="segment-editor-text-head">
            <div>
              <strong>Текст выбранного фрагмента</strong>
              <small>Выделите мышкой новую фразу, чтобы изменить начало и конец клипа</small>
            </div>
            <span>{fmtTime(start)} → {fmtTime(end)}</span>
          </div>
          {contextWords.length ? (
            <div ref={textRef} className="segment-editor-words" onMouseUp={captureTextRange}>
              {contextWords.map((word, wordIndex) => {
                const included = word.end >= start && word.start <= end;
                return (
                  <span
                    key={`${word.start}-${wordIndex}`}
                    data-editor-word={wordIndex}
                    className={included ? 'included' : ''}
                  >
                    {word.word}{' '}
                  </span>
                );
              })}
            </div>
          ) : (
            <textarea className="input segment-editor-textarea" value={selectedText} onChange={(event) => setSelectedText(event.target.value)} />
          )}
          <div className="segment-editor-selected">«{selectedText || segment.transcript_text || 'Текст недоступен'}»</div>
        </div>

        <div className="segment-editor-times">
          <div className="segment-time-control">
            <strong>🟢 Начало: {fmtTime(start)}</strong>
            <div>{[-5, -1, -0.2, 0.2, 1, 5].map((delta) => <button key={delta} className="btn btn-secondary btn-sm" onClick={() => handleStartShift(delta)}>{delta > 0 ? '+' : ''}{delta}с</button>)}</div>
          </div>
          <div className="segment-time-control">
            <strong>🔴 Конец: {fmtTime(end)}</strong>
            <div>{[-5, -1, -0.2, 0.2, 1, 5].map((delta) => <button key={delta} className="btn btn-secondary btn-sm" onClick={() => handleEndShift(delta)}>{delta > 0 ? '+' : ''}{delta}с</button>)}</div>
          </div>
        </div>

        <div className="segment-editor-footer">
          <span>Длительность: <strong>{dur} сек.</strong></span>
          <div>
            <button className="btn btn-secondary" onClick={onCancel}>Отмена</button>
            <button className="btn btn-primary" onClick={() => onSave(index, { ...segment, start, end, label, transcript_text: selectedText })}>✓ Сохранить</button>
          </div>
        </div>
      </div>
    </div>
  );
}
