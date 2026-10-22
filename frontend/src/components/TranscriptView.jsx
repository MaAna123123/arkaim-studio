import { useMemo, useRef, useState } from 'react';

function fmtTime(sec) {
  if (!sec && sec !== 0) return '00:00';
  const safe = Math.max(0, Number(sec) || 0);
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = Math.floor(safe % 60);
  return h > 0
    ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function getWordElement(node, root, direction) {
  const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  const directWord = element?.closest?.('[data-word-index]');
  if (directWord && root.contains(directWord)) return directWord;

  const probe = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (candidate) => candidate.matches?.('[data-word-index]')
      ? NodeFilter.FILTER_ACCEPT
      : NodeFilter.FILTER_SKIP,
  });
  let nearest = null;
  while (probe.nextNode()) {
    const candidate = probe.currentNode;
    const relation = node.compareDocumentPosition(candidate);
    if (direction === 'start' && (relation & Node.DOCUMENT_POSITION_FOLLOWING)) return candidate;
    if (direction === 'end' && (relation & Node.DOCUMENT_POSITION_PRECEDING)) nearest = candidate;
  }
  return nearest;
}

function joinWords(words) {
  return words.map((word) => word.word).join(' ').replace(/\s+([,.!?;:])/g, '$1').trim();
}

export default function TranscriptView({
  transcript,
  currentTime,
  onWordClick,
  onCutSelection,
  onImportSrt,
  cutting = false,
}) {
  const [search, setSearch] = useState('');
  const [selectedRange, setSelectedRange] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const textRef = useRef(null);

  const words = useMemo(() => {
    if (transcript?.words?.length) return transcript.words;
    return (transcript?.segments || []).flatMap((segment) => segment.words || []);
  }, [transcript]);

  if (!transcript || !transcript.has_transcript) return null;

  const clearSelection = () => {
    window.getSelection()?.removeAllRanges();
    setSelectedRange(null);
  };

  const captureSelection = () => {
    const selection = window.getSelection();
    const root = textRef.current;
    if (!selection || selection.isCollapsed || !root || !selection.rangeCount) return;

    const range = selection.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) return;

    const startElement = getWordElement(range.startContainer, root, 'start');
    const endElement = getWordElement(range.endContainer, root, 'end');
    if (!startElement || !endElement) return;

    const first = Math.min(Number(startElement.dataset.wordIndex), Number(endElement.dataset.wordIndex));
    const last = Math.max(Number(startElement.dataset.wordIndex), Number(endElement.dataset.wordIndex));
    const selectedWords = words.slice(first, last + 1);
    if (!selectedWords.length) return;

    setSelectedRange({
      first,
      last,
      start: selectedWords[0].start,
      end: selectedWords[selectedWords.length - 1].end,
      text: joinWords(selectedWords),
    });
    selection.removeAllRanges();
  };

  const searchTerm = search.trim().toLowerCase();

  return (
    <div className={`glass-card transcript-card ${expanded ? 'expanded' : ''}`}>
      <div className="transcript-header">
        <div>
          <div className="section-title" style={{ marginBottom: 2 }}>
            <div className="section-title-icon" aria-hidden="true" />
            Интерактивный транскрипт
          </div>
          <div className="transcript-hint">
            Выделите мышкой любую фразу — клип обрежется по таймкодам слов
            {transcript.word_timestamps_estimated ? ' (для SRT границы слов приблизительные)' : ''}
          </div>
        </div>
        <div className="transcript-header-actions">
          <span className="badge badge-green">
            {transcript.language ? transcript.language.toUpperCase() : 'RU'} • {transcript.source === 'srt' ? 'SRT' : 'Whisper'}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={onImportSrt}>Заменить SRT</button>
          <button className="btn btn-secondary btn-sm" onClick={() => setExpanded((value) => !value)}>
            {expanded ? '↙ Свернуть' : '↗ Открыть полностью'}
          </button>
        </div>
      </div>

      <input
        id="transcript-search-input"
        type="text"
        className="input text-sm"
        placeholder="Поиск по тексту..."
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      <div
        ref={textRef}
        className="transcript-selectable"
        onMouseUp={captureSelection}
        onKeyUp={captureSelection}
      >
        {words.map((word, index) => {
          const selected = selectedRange && index >= selectedRange.first && index <= selectedRange.last;
          const active = currentTime >= word.start && currentTime <= word.end;
          const matches = searchTerm && word.word.toLowerCase().includes(searchTerm);
          const previousWord = words[index - 1];
          const startsParagraph = previousWord && word.start - previousWord.end > 2;

          return (
            <span key={`${word.start}-${index}`}>
              {startsParagraph && <span className="transcript-break" aria-hidden="true" />}
              <span
                data-word-index={index}
                className={`transcript-word${selected ? ' selected' : ''}${active ? ' active' : ''}${matches ? ' search-match' : ''}`}
                onClick={() => {
                  if (window.getSelection()?.isCollapsed) onWordClick(word.start);
                }}
                title={`${fmtTime(word.start)} — перейти к слову`}
              >
                {word.word}
              </span>{' '}
            </span>
          );
        })}
      </div>

      <div className="transcript-selection-slot">
        {selectedRange ? (
          <div className="transcript-selection-bar">
          <div className="transcript-selection-copy">
            <div className="transcript-selection-time">
              {fmtTime(selectedRange.start)} → {fmtTime(selectedRange.end)}
              <span>{Math.max(0, selectedRange.end - selectedRange.start).toFixed(1)} сек.</span>
            </div>
            <div className="transcript-selection-preview">«{selectedRange.text}»</div>
          </div>
          <div className="transcript-selection-actions">
            <button className="btn btn-ghost btn-sm" onClick={() => onWordClick(selectedRange.start)}>
              Проверить
            </button>
            <button
              id="btn-cut-transcript-selection"
              className="btn btn-primary btn-sm"
              disabled={cutting}
              onClick={() => onCutSelection(selectedRange)}
            >
              {cutting ? <><div className="spinner" /> Нарезаю...</> : <>Вырезать и скачать</>}
            </button>
            <button className="btn btn-ghost btn-sm transcript-clear-selection" onClick={clearSelection} title="Сбросить выделение">
              ×
            </button>
          </div>
          </div>
        ) : (
          <div className="transcript-empty-selection">
            Совет: можно выделить одно предложение или целый абзац — начало и конец привяжутся к речи автоматически.
          </div>
        )}
      </div>
    </div>
  );
}
