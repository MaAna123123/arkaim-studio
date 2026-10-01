import { useState, useRef, useEffect } from 'react';

const URL_RE = /^https?:\/\/[^\s]+$/i;

export default function UrlInput({ apiBase, onVideoReady, onBusyChange }) {
  const [url, setUrl] = useState('');
  const [quality, setQuality] = useState('720');
  const [aspectRatio, setAspectRatio] = useState('9:16');
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [maxClips, setMaxClips] = useState(10);

  const [loadingInfo, setLoadingInfo] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState('');

  const lastFetched = useRef('');
  const busy = loadingInfo || processing || downloading;

  useEffect(() => {
    if (onBusyChange) onBusyChange(busy);
  }, [busy, onBusyChange]);

  const readError = async (r, fallback) => {
    try {
      const data = await r.json();
      return data.detail || fallback;
    } catch {
      return fallback;
    }
  };

  const fetchInfo = async (targetUrl) => {
    const clean = (targetUrl || '').trim();
    if (!clean || lastFetched.current === clean) return;
    lastFetched.current = clean;

    setLoadingInfo(true);
    setError('');
    setInfo(null);

    try {
      const r = await fetch(`${apiBase}/api/youtube/info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: clean }),
      });
      if (!r.ok) throw new Error(await readError(r, 'Не удалось получить информацию о видео'));
      setInfo(await r.json());
    } catch (err) {
      setError(err.message);
      lastFetched.current = '';
    } finally {
      setLoadingInfo(false);
    }
  };

  // Вставили ссылку — сразу подтягиваем информацию, не дожидаясь нажатия кнопки
  const handlePaste = (e) => {
    const pasted = (e.clipboardData?.getData('text') || '').trim();
    if (URL_RE.test(pasted)) {
      setUrl(pasted);
      setTimeout(() => fetchInfo(pasted), 0);
    }
  };

  const handleChange = (e) => {
    const value = e.target.value;
    setUrl(value);
    if (!value.trim()) {
      setInfo(null);
      setError('');
      lastFetched.current = '';
    }
  };

  // Главное действие: ссылка → готовые клипы одним запросом
  const handleAutoClips = async () => {
    const clean = url.trim();
    if (!clean) return;

    setProcessing(true);
    setError('');

    try {
      const r = await fetch(`${apiBase}/api/youtube/auto`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: clean,
          quality,
          aspect_ratio: aspectRatio,
          burn_subtitles: burnSubtitles,
          max_clips: Number(maxClips) || 10,
        }),
      });
      if (!r.ok) throw new Error(await readError(r, 'Не удалось нарезать клипы'));
      onVideoReady(await r.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setProcessing(false);
    }
  };

  // Запасной путь: только скачать видео и резать вручную
  const handleDownloadOnly = async () => {
    const clean = url.trim();
    if (!clean) return;

    setDownloading(true);
    setError('');

    try {
      const r = await fetch(`${apiBase}/api/youtube/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: clean, quality }),
      });
      if (!r.ok) throw new Error(await readError(r, 'Ошибка скачивания'));
      onVideoReady(await r.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloading(false);
    }
  };

  const tInfo = info?.transcript_info || {};
  const clipCount = info?.instant_clips?.length || 0;

  return (
    <div className="glass-card p-24">
      <div className="section-title">
        <div className="section-title-icon">🔗</div>
        Вставить ссылку (YouTube, VK, TikTok, Rutube)
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <input
          id="youtube-url-input"
          type="text"
          className="input"
          placeholder="Вставьте ссылку — клипы соберутся автоматически"
          value={url}
          onChange={handleChange}
          onPaste={handlePaste}
          onBlur={() => url.trim() && fetchInfo(url)}
          onKeyDown={(e) => e.key === 'Enter' && (info ? handleAutoClips() : fetchInfo(url))}
          disabled={busy}
        />
        <button
          id="btn-youtube-instant"
          className="btn btn-primary btn-lg"
          onClick={handleAutoClips}
          disabled={busy || !url.trim()}
          style={{
            flexShrink: 0,
            background: 'linear-gradient(135deg, #7c3aed 0%, #a855f7 100%)',
            boxShadow: '0 4px 14px rgba(124, 58, 237, 0.4)',
          }}
        >
          {processing ? <><div className="spinner" /> Нарезаю...</> : <>⚡ Нарезать клипы</>}
        </button>
      </div>

      {/* Настройки нарезки */}
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          Качество
          <select
            className="input"
            style={{ width: 92, padding: '6px 8px', fontSize: 13 }}
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
            disabled={busy}
          >
            <option value="480">480p</option>
            <option value="720">720p</option>
            <option value="1080">1080p</option>
            <option value="best">Максимум</option>
          </select>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          Формат
          <select
            className="input"
            style={{ width: 148, padding: '6px 8px', fontSize: 13 }}
            value={aspectRatio}
            onChange={(e) => setAspectRatio(e.target.value)}
            disabled={busy}
          >
            <option value="9:16">9:16 — Shorts/Reels</option>
            <option value="original">Исходный</option>
          </select>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          Клипов
          <select
            className="input"
            style={{ width: 70, padding: '6px 8px', fontSize: 13 }}
            value={maxClips}
            onChange={(e) => setMaxClips(e.target.value)}
            disabled={busy}
          >
            {[5, 8, 10, 12, 15].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={burnSubtitles}
            onChange={(e) => setBurnSubtitles(e.target.checked)}
            disabled={busy}
          />
          Вшить субтитры
        </label>

        {loadingInfo && (
          <span style={{ fontSize: 13, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <div className="spinner" style={{ width: 14, height: 14 }} /> Читаю видео...
          </span>
        )}
      </div>

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 16 }}>
          ⚠️ {error}
        </div>
      )}

      {/* Карточка видео */}
      {info && (
        <div style={{
          display: 'flex', gap: 16, padding: 16,
          background: 'var(--bg-surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)', alignItems: 'center', flexWrap: 'wrap',
        }}>
          {info.thumbnail && (
            <img
              src={info.thumbnail}
              alt="preview"
              style={{ width: 120, height: 68, objectFit: 'cover', borderRadius: 8 }}
            />
          )}
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{
              fontWeight: 700, fontSize: 15, marginBottom: 6,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {info.title}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <span>⏱ {Math.floor(info.duration / 60)} мин {info.duration % 60} сек</span>
              {info.uploader && <span>👤 {info.uploader}</span>}
              {info.has_chapters && (
                <span className="badge badge-purple">📑 {info.chapters.length} глав</span>
              )}
              {tInfo.has_transcript ? (
                <span className="badge badge-green">
                  💬 Субтитры {tInfo.is_auto ? '(авто)' : '(встроенные)'} · {tInfo.segment_count} реплик
                </span>
              ) : (
                <span className="badge">🎙 Субтитров нет — распознаю речь</span>
              )}
              {clipCount > 0 && <span className="badge">✂️ ~{clipCount} фрагментов</span>}
            </div>
          </div>

          <button
            className="btn btn-secondary"
            onClick={handleDownloadOnly}
            disabled={busy}
            title="Только скачать видео, нарезать вручную"
          >
            {downloading ? <><div className="spinner" /> Загрузка...</> : <>⬇ Только скачать</>}
          </button>
        </div>
      )}
    </div>
  );
}
