import { useState, useEffect, useCallback } from 'react';

const PLATFORMS = ['youtube', 'instagram', 'tiktok'];

const ICONS = { youtube: 'YT', instagram: 'IG', tiktok: 'TT' };
const NAMES = { youtube: 'YouTube', instagram: 'Instagram', tiktok: 'TikTok' };
const COLORS = {
  youtube: { bg: 'rgba(255, 0, 0, 0.08)', border: 'rgba(255, 0, 0, 0.25)', text: '#ff4444' },
  instagram: { bg: 'rgba(225, 48, 108, 0.08)', border: 'rgba(225, 48, 108, 0.25)', text: '#e1306c' },
  tiktok: { bg: 'rgba(0, 242, 234, 0.08)', border: 'rgba(0, 242, 234, 0.25)', text: '#00f2ea' },
};

export default function SocialSharePanel({ apiBase, clipResults }) {
  const [socialStatus, setSocialStatus] = useState({});
  const [preparing, setPreparing] = useState(null);      // { clipUrl, platform }
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [modal, setModal] = useState(null);              // { clip, platform, mode, data }
  const [copied, setCopied] = useState('');
  const [form, setForm] = useState({ title: '', description: '', tags: '', privacy: 'private' });

  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/api/social/status`);
      setSocialStatus(await r.json());
    } catch {
      setSocialStatus({});
    }
  }, [apiBase]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  // ── Копирование в буфер ────────────────────────────────────────────────────
  const copyText = async (text, key) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Резервный путь для окружений без Clipboard API
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* не критично */ }
      document.body.removeChild(ta);
    }
    setCopied(key);
    setTimeout(() => setCopied(''), 1800);
  };

  // ── YouTube OAuth ──────────────────────────────────────────────────────────
  const handleYouTubeAuth = async () => {
    try {
      const r = await fetch(`${apiBase}/api/social/youtube/auth`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || 'YouTube API не настроен');
      if (!data.auth_url) return;

      window.open(data.auth_url, '_blank', 'width=600,height=700');
      const poll = setInterval(async () => {
        const updated = await fetch(`${apiBase}/api/social/status`).then((r2) => r2.json()).catch(() => null);
        if (updated?.youtube?.authenticated) {
          clearInterval(poll);
          setSocialStatus(updated);
        }
      }, 3000);
      setTimeout(() => clearInterval(poll), 120000);
    } catch (err) {
      alert(`Ошибка YouTube авторизации: ${err.message}`);
    }
  };

  const handleYouTubeDisconnect = async () => {
    try {
      await fetch(`${apiBase}/api/social/youtube/disconnect`, { method: 'DELETE' });
      await loadStatus();
    } catch {
      alert('Не удалось отключить YouTube');
    }
  };

  // ── Подготовка публикации ──────────────────────────────────────────────────
  const openFor = async (clip, platform) => {
    const isAutoYouTube = platform === 'youtube' && socialStatus.youtube?.authenticated;

    setPreparing({ clipUrl: clip.url, platform });
    setUploadResult(null);
    setCopied('');

    try {
      const r = await fetch(`${apiBase}/api/social/prepare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          platform,
          clip_url: clip.url,
          label: clip.label || '',
          transcript_text: clip.transcript_text || '',
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || 'Не удалось подготовить публикацию');

      setForm({
        title: data.title || clip.label || '',
        description: data.description || '',
        tags: (data.hashtags || []).join(', '),
        privacy: 'private',
      });
      setModal({ clip, platform, mode: isAutoYouTube ? 'auto' : 'semi', data });
    } catch (err) {
      alert(`Ошибка подготовки: ${err.message}`);
    } finally {
      setPreparing(null);
    }
  };

  // Полуавтомат: копируем описание, качаем файл, открываем страницу загрузки
  const handleSemiPublish = async () => {
    if (!modal) return;
    const { data, platform } = modal;

    await copyText(`${form.title}\n\n${form.description}`.trim(), 'publish');

    const link = document.createElement('a');
    link.href = `${apiBase}${data.download_url}`;
    link.download = data.file_name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    setTimeout(() => window.open(data.upload_url, '_blank', 'noopener,noreferrer'), 600);
    setUploadResult({ semi: true, platform });
  };

  // Автомат: только YouTube с подключённым Data API
  const handleAutoPublish = async () => {
    if (!modal) return;
    setUploading(true);
    setUploadResult(null);
    try {
      const r = await fetch(`${apiBase}/api/social/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          platform: modal.platform,
          clip_url: modal.clip.url,
          title: form.title,
          description: form.description,
          tags: form.tags.split(',').map((t) => t.trim().replace(/^#/, '')).filter(Boolean),
          privacy: form.privacy,
        }),
      });
      const result = await r.json();
      if (!r.ok) throw new Error(result.detail || `Ошибка ${r.status}`);
      setUploadResult(result);
    } catch (err) {
      setUploadResult({ error: err.message });
    } finally {
      setUploading(false);
    }
  };

  if (!clipResults || clipResults.length === 0) return null;

  return (
    <div className="social-share-panel">
      <div className="social-share-title">
        <span className="social-share-icon" aria-hidden="true" />
        Публикация в соцсети
      </div>

      <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 14px' }}>
        Программа сама составит заголовок, описание и хэштеги, скачает клип и откроет страницу
        загрузки. Останется только нажать «Опубликовать» на самой площадке.
      </p>

      {/* Статус площадок */}
      <div className="social-platforms-row">
        {PLATFORMS.map((platform) => {
          const status = socialStatus[platform] || {};
          const colors = COLORS[platform];
          return (
            <div
              key={platform}
              className="social-platform-badge"
              style={{ background: colors.bg, borderColor: colors.border }}
            >
              <span className="social-platform-icon">{ICONS[platform]}</span>
              <span className="social-platform-name">{NAMES[platform]}</span>
              {platform === 'youtube' ? (
                status.authenticated ? (
                  <button
                    className="btn btn-sm social-connect-btn"
                    style={{ color: colors.text, borderColor: colors.border }}
                    onClick={handleYouTubeDisconnect}
                    title="Отключить аккаунт"
                  >
                    Авто · отключить
                  </button>
                ) : (
                  <button
                    className="btn btn-sm social-connect-btn"
                    style={{ color: colors.text, borderColor: colors.border }}
                    onClick={handleYouTubeAuth}
                    title="Подключить для полностью автоматической загрузки"
                  >
                    Подключить авто
                  </button>
                )
              ) : (
                <span className="social-status-connected" style={{ color: colors.text }}>
                  Полуавтомат
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Кнопки по каждому клипу */}
      <div className="social-clips-list">
        {clipResults.map((clip, idx) => (
          <div key={`social-${clip.url}-${idx}`} className="social-clip-row">
            <div className="social-clip-name">{clip.label}</div>
            <div className="social-clip-buttons">
              {PLATFORMS.map((platform) => {
                const colors = COLORS[platform];
                const isBusy = preparing?.clipUrl === clip.url && preparing?.platform === platform;
                return (
                  <button
                    key={platform}
                    className="btn btn-sm social-upload-btn"
                    style={{ background: colors.bg, borderColor: colors.border, color: colors.text }}
                    disabled={!!preparing}
                    onClick={() => openFor(clip, platform)}
                  >
                    {isBusy ? (
                      <><div className="spinner" style={{ width: 14, height: 14 }} /> Готовлю...</>
                    ) : (
                      <>{ICONS[platform]} {NAMES[platform]}</>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Модальное окно публикации */}
      {modal && (
        <div
          className="modal-overlay"
          role="presentation"
          onMouseDown={(e) => { if (e.target === e.currentTarget) setModal(null); }}
        >
          <div className="social-publish-modal" role="dialog" aria-modal="true">
            <div className="social-publish-header">
              <strong>
                {ICONS[modal.platform]} Публикация в {NAMES[modal.platform]}
                {modal.mode === 'semi' && (
                  <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text-secondary)', marginLeft: 8 }}>
                    полуавтомат
                  </span>
                )}
              </strong>
              <button className="clip-preview-close" aria-label="Закрыть" onClick={() => setModal(null)}>×</button>
            </div>

            <div className="social-publish-body">
              {uploadResult?.error ? (
                <div className="social-publish-error">
                  <div className="publish-state-mark publish-state-mark-error" aria-hidden="true">!</div>
                  <div style={{ fontWeight: 700, marginBottom: 8 }}>Ошибка публикации</div>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: 'var(--text-secondary)' }}>
                    {uploadResult.error}
                  </pre>
                </div>
              ) : uploadResult?.url ? (
                <div className="social-publish-success">
                  <div className="publish-state-mark" aria-hidden="true">OK</div>
                  <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>Видео опубликовано!</div>
                  <a href={uploadResult.url} target="_blank" rel="noopener noreferrer" className="social-publish-link">
                    {uploadResult.url}
                  </a>
                </div>
              ) : uploadResult?.semi ? (
                <div className="social-publish-success">
                  <div className="publish-state-mark" aria-hidden="true">i</div>
                  <div style={{ fontWeight: 700, fontSize: 17, marginBottom: 10 }}>
                    Клип скачан, описание скопировано
                  </div>
                  <ol style={{ textAlign: 'left', fontSize: 13, lineHeight: 1.7, paddingLeft: 20, margin: 0 }}>
                    {(modal.data.instructions || []).map((step, i) => <li key={i}>{step}</li>)}
                  </ol>
                  <button
                    className="btn btn-secondary"
                    style={{ marginTop: 14 }}
                    onClick={() => window.open(modal.data.upload_url, '_blank', 'noopener,noreferrer')}
                  >
                    ↗ Открыть {NAMES[modal.platform]} ещё раз
                  </button>
                </div>
              ) : (
                <>
                  <div className="social-publish-clip-name">
                    {modal.clip.label}
                    <span style={{ color: 'var(--text-secondary)', fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                      {modal.data.file_size_mb} МБ
                    </span>
                  </div>

                  <label className="social-publish-label">
                    Заголовок
                    <input
                      type="text"
                      className="input"
                      value={form.title}
                      onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                      maxLength={100}
                    />
                  </label>

                  <label className="social-publish-label">
                    Описание с хэштегами
                    <textarea
                      className="input social-publish-textarea"
                      value={form.description}
                      onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                      maxLength={5000}
                      rows={6}
                    />
                  </label>

                  <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                    <button className="btn btn-sm btn-secondary" onClick={() => copyText(form.title, 'title')}>
                      {copied === 'title' ? 'Скопировано' : 'Копировать заголовок'}
                    </button>
                    <button className="btn btn-sm btn-secondary" onClick={() => copyText(form.description, 'desc')}>
                      {copied === 'desc' ? 'Скопировано' : 'Копировать описание'}
                    </button>
                  </div>

                  {modal.platform === 'youtube' && modal.mode === 'auto' && (
                    <label className="social-publish-label">
                      Приватность
                      <select
                        className="input"
                        value={form.privacy}
                        onChange={(e) => setForm((f) => ({ ...f, privacy: e.target.value }))}
                      >
                        <option value="private">Приватное</option>
                        <option value="unlisted">По ссылке</option>
                        <option value="public">Публичное</option>
                      </select>
                    </label>
                  )}

                  {modal.mode === 'semi' && (
                    <div style={{
                      fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.6,
                      background: 'var(--bg-surface)', padding: '10px 12px',
                      borderRadius: 8, border: '1px solid var(--border)',
                    }}>
                      {modal.platform === 'youtube'
                        ? 'Подключите YouTube выше, чтобы публиковать полностью автоматически.'
                        : `${NAMES[modal.platform]} не разрешает загрузку через API без бизнес-аккаунта, поэтому финальную кнопку нажимаете вы.`}
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="social-publish-footer">
              {!uploadResult && (
                modal.mode === 'auto' ? (
                  <button
                    className="btn btn-primary"
                    disabled={uploading || !form.title.trim()}
                    onClick={handleAutoPublish}
                  >
                    {uploading ? <><div className="spinner" /> Загружаю...</> : <>Опубликовать</>}
                  </button>
                ) : (
                  <button
                    className="btn btn-primary"
                    disabled={!form.title.trim()}
                    onClick={handleSemiPublish}
                  >
                    Скачать клип и открыть {NAMES[modal.platform]}
                  </button>
                )
              )}
              <button className="btn btn-secondary" onClick={() => setModal(null)}>
                {uploadResult ? 'Закрыть' : 'Отмена'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
