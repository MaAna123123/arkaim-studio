import { useState } from 'react';

function fmt(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function scoreColor(score) {
  if (score >= 0.8) return 'var(--success)';
  if (score >= 0.6) return 'var(--warning)';
  return 'var(--accent-cyan)';
}

function sourceBadge(source) {
  if (source === 'transcript') return <span className="badge badge-purple">ИИ по транскрипту</span>;
  if (source === 'youtube_chapter') return <span className="badge badge-cyan">YouTube глава</span>;
  if (source === 'frames') return <span className="badge badge-yellow">ИИ по кадрам</span>;
  if (source === 'manual') return <span className="badge badge-purple">Ручная нарезка</span>;
  return <span className="badge badge-green">Локальный пик</span>;
}

export default function HighlightList({ segments, selected, onToggle, onPlayAt, onEditSegment, onDownloadSingle, exporting = false }) {
  const [expandedText, setExpandedText] = useState(new Set());
  if (!segments.length) return null;

  return (
    <div className="highlight-grid">
      {segments.map((seg, i) => {
        const isSelected = selected.has(i);
        const dur = (seg.end - seg.start).toFixed(1);

        return (
          <div
            key={i}
            id={`highlight-card-${i}`}
            className={`highlight-card glass-card ${isSelected ? 'selected' : ''}`}
            onClick={() => onPlayAt(seg.start)}
          >
            <div className="highlight-card-header">
              {/* Checkbox */}
              <div
                className={`highlight-card-check ${isSelected ? 'checked' : ''}`}
                onClick={(e) => { e.stopPropagation(); onToggle(i); }}
              >
                {isSelected && <span className="selection-mark" aria-hidden="true" />}
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <div className="highlight-card-title" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                    {seg.label || `Момент #${i + 1}`}
                  </div>
                  {sourceBadge(seg.source)}
                </div>

                <div className="highlight-card-timecode">
                  {fmt(seg.start)} → {fmt(seg.end)} &nbsp;•&nbsp; {dur}с
                </div>
              </div>
            </div>

            {/* Description */}
            {seg.description && (
              <p className="highlight-card-desc">{seg.description}</p>
            )}

            {/* Transcript text preview if available */}
            {seg.transcript_text && (
              <div className="highlight-transcript-wrap" onClick={(event) => event.stopPropagation()}>
                <div className={`highlight-transcript-text ${expandedText.has(i) ? 'expanded' : ''}`}>
                  «{seg.transcript_text}»
                </div>
                {seg.transcript_text.length > 180 && (
                  <button
                    className="btn btn-ghost btn-sm highlight-transcript-toggle"
                    onClick={() => {
                      const next = new Set(expandedText);
                      next.has(i) ? next.delete(i) : next.add(i);
                      setExpandedText(next);
                    }}
                  >
                    {expandedText.has(i) ? 'Свернуть текст' : 'Показать весь текст'}
                  </button>
                )}
              </div>
            )}

            {/* Score bar */}
            <div className="highlight-card-score">
              <span style={{ fontSize: 11, color: 'var(--text-muted)', width: 60, flexShrink: 0 }}>
                Виральность
              </span>
              <div className="score-bar-track">
                <div
                  className="score-bar-fill"
                  style={{
                    width: `${(seg.score || 0) * 100}%`,
                    background: scoreColor(seg.score || 0),
                  }}
                />
              </div>
              <span style={{ fontSize: 12, fontWeight: 700, color: scoreColor(seg.score || 0), width: 36, textAlign: 'right' }}>
                {Math.round((seg.score || 0) * 100)}%
              </span>
            </div>

            {/* Actions */}
            <div className="highlight-card-actions" style={{ flexWrap: 'wrap', gap: 6 }}>
              <button
                id={`btn-play-segment-${i}`}
                className="btn btn-ghost btn-sm"
                onClick={(e) => { e.stopPropagation(); onPlayAt(seg.start); }}
              >
                Смотреть
              </button>
              <button
                id={`btn-edit-segment-${i}`}
                className="btn btn-secondary btn-sm"
                onClick={(e) => { e.stopPropagation(); onEditSegment(i); }}
              >
                Править
              </button>
              <button
                id={`btn-download-single-${i}`}
                className="btn btn-primary btn-sm"
                onClick={(e) => { e.stopPropagation(); onDownloadSingle(i); }}
                disabled={exporting}
              >
                Скачать
              </button>
              <button
                id={`btn-toggle-segment-${i}`}
                className={`btn btn-sm ${isSelected ? 'btn-danger' : 'btn-success'}`}
                onClick={(e) => { e.stopPropagation(); onToggle(i); }}
                title={isSelected ? "Убрать из нарезки" : "Добавить в нарезку"}
              >
                {isSelected ? '×' : '+'}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
