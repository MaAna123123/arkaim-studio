import { useState, useEffect, useRef } from 'react';
import { toEditorMedia } from '../lib/mediaAsset.js';

function AdSection({ apiBase, onAdStatusChange }) {
  const [adInfo, setAdInfo] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  useEffect(() => { loadAdInfo(); }, []);

  const loadAdInfo = async () => {
    try {
      const r = await fetch(`${apiBase}/api/ad/current`);
      const data = await r.json();
      setAdInfo(data);
      onAdStatusChange?.(toEditorMedia(apiBase, data, 'ad'));
    } catch {
      setAdInfo({ has_ad: false });
      onAdStatusChange?.(toEditorMedia(apiBase, { has_ad: false }, 'ad'));
    }
  };

  const handleUpload = async (file) => {
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await fetch(`${apiBase}/api/ad/upload`, { method: 'POST', body: form });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `Ошибка ${r.status}`);
      }
      const data = await r.json();
      setAdInfo({ has_ad: true, ...data });
      onAdStatusChange?.(toEditorMedia(apiBase, { has_ad: true, ...data }, 'ad'));
    } catch (err) {
      alert(`Ошибка загрузки рекламы: ${err.message}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleRemove = async () => {
    try {
      await fetch(`${apiBase}/api/ad/remove`, { method: 'DELETE' });
      setAdInfo({ has_ad: false });
      onAdStatusChange?.(toEditorMedia(apiBase, { has_ad: false }, 'ad'));
    } catch {
      alert('Не удалось удалить рекламный ролик');
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) handleUpload(file);
  };

  const fmtDuration = (sec) => {
    if (!sec) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  };
  const fmtSize = (bytes) => {
    if (!bytes) return '0 B';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  };

  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        📺 Видео-реклама (вставляется в середину)
      </div>
      {adInfo?.has_ad ? (
        <div className="ad-manager-loaded">
          <div className="ad-manager-preview">
            <video
              src={`${apiBase}${adInfo.url}`}
              className="ad-manager-video"
              muted preload="metadata"
              onMouseEnter={(e) => e.target.play()}
              onMouseLeave={(e) => { e.target.pause(); e.target.currentTime = 0; }}
            />
          </div>
          <div className="ad-manager-meta">
            <div className="ad-manager-filename">🎬 {adInfo.filename || 'Рекламный ролик'}</div>
            <div className="ad-manager-details">⏱ {fmtDuration(adInfo.duration)} · 📦 {fmtSize(adInfo.size_bytes)}</div>
            <div className="ad-manager-hint">Реклама будет вставлена в середину каждого клипа</div>
          </div>
          <div className="ad-manager-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                if (fileRef.current) fileRef.current.value = '';
                fileRef.current?.click();
              }}
            >
              🔄 Заменить
            </button>
            <button type="button" className="btn btn-danger btn-sm" onClick={handleRemove}>🗑 Удалить</button>
          </div>
        </div>
      ) : (
        <div
          className={`ad-manager-dropzone ${dragOver ? 'dragging' : ''}`}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {uploading ? (
            <div className="ad-manager-uploading"><div className="spinner" /><span>Загрузка...</span></div>
          ) : (
            <>
              <div className="ad-manager-dropzone-icon">📺</div>
              <div className="ad-manager-dropzone-text">Перетащите рекламный ролик или нажмите для выбора</div>
              <div className="ad-manager-dropzone-hint">MP4, MOV, MKV · Рекомендуется до 30 сек</div>
            </>
          )}
        </div>
      )}
      <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/x-matroska,.mp4,.mov,.mkv,.webm" style={{ display: 'none' }} onChange={(e) => handleUpload(e.target.files?.[0])} />
    </div>
  );
}

function BannerSection({ apiBase, onBannerStatusChange }) {
  const [bannerInfo, setBannerInfo] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  useEffect(() => { loadBannerInfo(); }, []);

  const loadBannerInfo = async () => {
    try {
      const r = await fetch(`${apiBase}/api/banner/current`);
      const data = await r.json();
      setBannerInfo(data);
      onBannerStatusChange?.(toEditorMedia(apiBase, data, 'banner'), 'loaded');
    } catch {
      setBannerInfo({ has_banner: false });
      onBannerStatusChange?.(toEditorMedia(apiBase, { has_banner: false }, 'banner'), 'loaded');
    }
  };

  const handleUpload = async (file) => {
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await fetch(`${apiBase}/api/banner/upload`, { method: 'POST', body: form });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `Ошибка ${r.status}`);
      }
      const data = await r.json();
      setBannerInfo({ has_banner: true, ...data });
      onBannerStatusChange?.(toEditorMedia(apiBase, { has_banner: true, ...data }, 'banner'), 'uploaded');
    } catch (err) {
      alert(`Ошибка загрузки баннера: ${err.message}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleRemove = async () => {
    try {
      await fetch(`${apiBase}/api/banner/remove`, { method: 'DELETE' });
      setBannerInfo({ has_banner: false });
      onBannerStatusChange?.(toEditorMedia(apiBase, { has_banner: false }, 'banner'), 'removed');
    } catch {
      alert('Не удалось удалить баннер');
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) handleUpload(file);
  };

  const fmtDuration = (sec) => {
    if (!sec) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  };
  const fmtSize = (bytes) => {
    if (!bytes) return '0 B';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  };

  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        🎥 Видео-баннер (зацикленный, сверху клипа)
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10, lineHeight: 1.5 }}>
        Мини-видео наложится поверх каждого клипа как рекламный баннер. Видео зацикливается автоматически.
      </div>
      {bannerInfo?.has_banner ? (
        <div className="ad-manager-loaded">
          <div className="ad-manager-preview">
            <video
              src={`${apiBase}${bannerInfo.url}`}
              className="ad-manager-video"
              muted loop preload="metadata"
              onMouseEnter={(e) => e.target.play()}
              onMouseLeave={(e) => { e.target.pause(); e.target.currentTime = 0; }}
            />
          </div>
          <div className="ad-manager-meta">
            <div className="ad-manager-filename">🎥 {bannerInfo.filename || 'Видео-баннер'}</div>
            <div className="ad-manager-details">⏱ {fmtDuration(bannerInfo.duration)} · 📦 {fmtSize(bannerInfo.size_bytes)}</div>
            <div className="ad-manager-hint">
              Баннер накладывается в верхней части каждого клипа. Размер (высота от 150 до 500 px) настраивается в блоке экспорта ниже.
            </div>
          </div>
          <div className="ad-manager-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                if (fileRef.current) fileRef.current.value = '';
                fileRef.current?.click();
              }}
            >
              🔄 Заменить
            </button>
            <button type="button" className="btn btn-danger btn-sm" onClick={handleRemove}>🗑 Удалить</button>
          </div>
        </div>
      ) : (
        <div
          className={`ad-manager-dropzone ${dragOver ? 'dragging' : ''}`}
          style={{ borderColor: 'rgba(14, 165, 233, 0.4)' }}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {uploading ? (
            <div className="ad-manager-uploading"><div className="spinner" /><span>Загрузка баннера...</span></div>
          ) : (
            <>
              <div className="ad-manager-dropzone-icon">🎥</div>
              <div className="ad-manager-dropzone-text">Перетащите видео-баннер или нажмите для выбора</div>
              <div className="ad-manager-dropzone-hint">MP4, MOV · Рекомендуется короткий зацикленный клип (2-10 сек)</div>
            </>
          )}
        </div>
      )}
      <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/x-matroska,.mp4,.mov,.mkv,.webm" style={{ display: 'none' }} onChange={(e) => handleUpload(e.target.files?.[0])} />
    </div>
  );
}

export default function AdManager({ apiBase, onAdStatusChange, onBannerStatusChange }) {
  return (
    <div className="ad-manager">
      <div className="ad-manager-title">
        <span className="ad-manager-icon">🎬</span>
        Реклама
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <AdSection apiBase={apiBase} onAdStatusChange={onAdStatusChange} />
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 20 }}>
          <BannerSection apiBase={apiBase} onBannerStatusChange={onBannerStatusChange} />
        </div>
      </div>
    </div>
  );
}
