import { useRef, useEffect, useState, useCallback } from 'react';

function fmt(sec) {
  if (!sec && sec !== 0) return '00:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

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

  // 1. Direct match with 0.3s buffer
  let active = phrases.find(phrase => {
    if (!phrase.length) return false;
    const pStart = Number(phrase[0].start);
    const pEnd = Number(phrase[phrase.length - 1].end) + 0.3;
    return currentTime >= pStart && currentTime <= pEnd;
  });

  // 2. Continuous speech gap bridge (< 1.0s gap between phrases)
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

export default function VideoPlayer({ src, segments, duration, onTimeUpdate, seekTo, onSeekDone, transcript, showSubtitles = true }) {
  const videoRef = useRef(null);
  const seekBarRef = useRef(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [paused, setPaused] = useState(true);
  const [vol, setVol] = useState(1);

  // External seek
  useEffect(() => {
    if (seekTo !== null && videoRef.current) {
      videoRef.current.currentTime = seekTo;
      videoRef.current.play();
      onSeekDone?.();
    }
  }, [seekTo]);

  const handleTimeUpdate = () => {
    const t = videoRef.current?.currentTime || 0;
    setCurrentTime(t);
    onTimeUpdate?.(t);
  };

  const handleSeekClick = useCallback((e) => {
    const rect = seekBarRef.current?.getBoundingClientRect();
    if (!rect || !videoRef.current) return;
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const dur = videoRef.current.duration || duration;
    videoRef.current.currentTime = pct * dur;
  }, [duration]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      videoRef.current.play();
    } else {
      videoRef.current.pause();
    }
  };

  const dur = videoRef.current?.duration || duration || 1;
  const pct = (currentTime / dur) * 100;

  // Active phrase subtitle
  const activeSub = getActiveSubtitle(transcript, currentTime);

  return (
    <div className="player-wrap" style={{ position: 'relative' }}>
      {src ? (
        <>
          <video
            ref={videoRef}
            src={src}
            onTimeUpdate={handleTimeUpdate}
            onPlay={() => setPaused(false)}
            onPause={() => setPaused(true)}
            onVolumeChange={() => setVol(videoRef.current?.volume ?? 1)}
          />

          {/* Compact Clean Subtitle Overlay at Bottom */}
          {showSubtitles && activeSub && (
            <div style={{
              position: 'absolute',
              bottom: '8%',
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
              {activeSub}
            </div>
          )}

          <div className="player-controls">
            {/* Seek bar */}
            <div
              ref={seekBarRef}
              className="player-seek"
              id="player-seek-bar"
              onClick={handleSeekClick}
            >
              <div className="player-seek-fill" style={{ width: `${pct}%` }} />
              {/* Segment markers */}
              {segments.map((seg, i) => (
                <div
                  key={i}
                  className="player-marker"
                  style={{ left: `${(seg.start / dur) * 100}%` }}
                />
              ))}
            </div>

            {/* Controls row */}
            <div className="player-controls-row">
              <button id="btn-player-play" className="btn btn-ghost btn-sm" onClick={togglePlay}>
                {paused ? 'Пуск' : 'Пауза'}
              </button>
              <span className="player-time">{fmt(currentTime)} / {fmt(dur)}</span>
              <span style={{ flex: 1 }} />
              <input
                id="player-volume"
                type="range" min="0" max="1" step="0.05"
                value={vol}
                style={{ width: 80, accentColor: 'var(--accent-primary)' }}
                onChange={(e) => { const v = +e.target.value; setVol(v); if (videoRef.current) videoRef.current.volume = v; }}
              />
              <span className="player-volume-label">Звук</span>
            </div>
          </div>
        </>
      ) : (
        <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 14 }}>
          Загрузите видео для просмотра
        </div>
      )}
    </div>
  );
}
