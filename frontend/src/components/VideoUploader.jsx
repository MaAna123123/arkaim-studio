import { useRef, useState } from 'react';

const FORMATS = ['MP4', 'MKV', 'AVI', 'MOV', 'WEBM', 'TS'];

function fmtBytes(b) {
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
  if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
  return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function fmtDuration(sec) {
  if (!sec) return '--';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

export default function VideoUploader({ onVideoSelect, onResetVideo, currentVideo }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);

  const handleFile = async (file) => {
    if (!file) return;
    setUploading(true);
    await onVideoSelect(file);
    setUploading(false);
  };

  const openFilePicker = (e) => {
    e?.stopPropagation();
    if (inputRef.current) {
      inputRef.current.value = '';
    }
    inputRef.current?.click();
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  // If we have a video loaded, show info bar instead
  if (currentVideo) {
    const meta = currentVideo.metadata || {};
    return (
      <div
        className="video-info-bar glass-card"
        id="video-info-bar"
      >
        <div className="video-info-thumb" aria-hidden="true" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="video-info-name" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {currentVideo.filename}
          </div>
          <div className="video-info-meta">
            <span>{fmtDuration(meta.duration)}</span>
            <span>{meta.width}×{meta.height}</span>
            <span>{meta.fps} fps</span>
            <span>{fmtBytes(meta.size_bytes)}</span>
          </div>
        </div>
        <div className="badge badge-green">Загружено</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            id="btn-replace-local-file"
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={openFilePicker}
            title="Загрузить другой файл с компьютера"
          >
            Заменить файл
          </button>
          {onResetVideo && (
            <button
              id="btn-reset-video"
              type="button"
              className="btn btn-danger btn-sm"
              onClick={onResetVideo}
              title="Сбросить текущее видео и ввести новую ссылку YouTube или выбрать другой файл"
            >
              Сбросить / Другое видео
            </button>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          style={{ display: 'none' }}
          onClick={(e) => { e.target.value = ''; }}
          onChange={(e) => handleFile(e.target.files[0])}
        />
      </div>
    );
  }

  return (
    <div
      id="video-uploader"
      className={`uploader glass-card ${dragging ? 'dragging' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef} type="file" accept="video/*" style={{ display: 'none' }}
        onChange={(e) => handleFile(e.target.files[0])}
      />

      {uploading ? (
        <>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>
            <div className="spinner spinner-lg" />
          </div>
          <div className="uploader-title">Загрузка видео...</div>
          <p className="uploader-subtitle">Извлекаю метаданные и генерирую превью</p>
        </>
      ) : (
        <>
          <span className="uploader-icon" aria-hidden="true" />
          <div className="uploader-title">Перетащите видео сюда</div>
          <p className="uploader-subtitle">или нажмите для выбора файла с компьютера</p>
          <button className="btn btn-primary" onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}>
            Выбрать видео
          </button>
          <div className="uploader-formats">
            {FORMATS.map(f => (
              <span key={f} className="badge badge-purple">{f}</span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
