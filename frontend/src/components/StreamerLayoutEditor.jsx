import { useEffect, useRef, useState } from 'react';
import { normalizedRectFromDrag, previewCoverWindow } from '../lib/streamerLayout';

function SelectionBox({ region, kind }) {
  if (!region) return null;
  return (
    <div
      className={`streamer-selection streamer-selection-${kind}`}
      style={{
        left: `${region.x * 100}%`,
        top: `${region.y * 100}%`,
        width: `${region.width * 100}%`,
        height: `${region.height * 100}%`,
      }}
    >
      <span>{kind === 'webcam' ? 'Вебка' : 'Геймплей'}</span>
    </div>
  );
}

function CroppedPreview({ src, region, label, currentTime, sourceAspectRatio, paneAspectRatio }) {
  const previewRef = useRef(null);
  useEffect(() => {
    const player = previewRef.current;
    if (!player || !Number.isFinite(currentTime)) return;
    if (Math.abs(player.currentTime - currentTime) > 0.35) {
      try { player.currentTime = currentTime; } catch { /* metadata may not be ready */ }
    }
  }, [currentTime, src]);

  const coverWindow = previewCoverWindow(region, sourceAspectRatio, paneAspectRatio);
  return (
    <div className="streamer-preview-pane">
      {src && region && coverWindow ? (
        <div
          className="streamer-crop-window"
          style={{
            width: `${coverWindow.width}%`,
            height: `${coverWindow.height}%`,
            left: `${coverWindow.left}%`,
            top: `${coverWindow.top}%`,
          }}
        >
          <video
            ref={previewRef}
            src={src}
            muted
            playsInline
            aria-label={label}
            onLoadedMetadata={(event) => {
              if (Number.isFinite(currentTime)) event.currentTarget.currentTime = currentTime;
            }}
            style={{
              width: `${100 / region.width}%`,
              height: `${100 / region.height}%`,
              left: `${(-region.x / region.width) * 100}%`,
              top: `${(-region.y / region.height) * 100}%`,
            }}
          />
        </div>
      ) : <span>{label}</span>}
    </div>
  );
}

export default function StreamerLayoutEditor({
  src,
  currentTime = 0,
  enabled,
  onEnabledChange,
  webcamRegion,
  onWebcamRegionChange,
  gameplayRegion,
  onGameplayRegionChange,
  webcamHeightRatio,
  onWebcamHeightRatioChange,
}) {
  const videoRef = useRef(null);
  const stageRef = useRef(null);
  const dragStartRef = useRef(null);
  const [selectionMode, setSelectionMode] = useState('webcam');
  const [draftRegion, setDraftRegion] = useState(null);
  const [sourceAspectRatio, setSourceAspectRatio] = useState(16 / 9);

  useEffect(() => {
    const player = videoRef.current;
    if (!player || !Number.isFinite(currentTime)) return;
    if (Math.abs(player.currentTime - currentTime) > 0.35) {
      try { player.currentTime = currentTime; } catch { /* metadata may not be ready */ }
    }
  }, [currentTime, src]);

  const updateDraft = (event) => {
    if (!dragStartRef.current || !stageRef.current) return;
    setDraftRegion(normalizedRectFromDrag(
      dragStartRef.current,
      event,
      stageRef.current.getBoundingClientRect(),
    ));
  };

  const finishDrag = (event) => {
    if (!dragStartRef.current || !stageRef.current) return;
    const region = normalizedRectFromDrag(
      dragStartRef.current,
      event,
      stageRef.current.getBoundingClientRect(),
    );
    dragStartRef.current = null;
    setDraftRegion(null);
    if (!region) return;
    if (selectionMode === 'webcam') onWebcamRegionChange(region);
    else onGameplayRegionChange(region);
  };

  return (
    <section className="glass-card streamer-layout-card">
      <div className="streamer-layout-heading">
        <div>
          <div className="arkaim-eyebrow">Компоновка стрима</div>
          <h3>Вебка сверху, геймплей снизу</h3>
          <p>Выделите две области прямо на исходном кадре. Экспорт станет вертикальным 9:16.</p>
        </div>
        <button
          id="btn-toggle-streamer-layout"
          type="button"
          className={`btn ${enabled ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => onEnabledChange(!enabled)}
        >
          {enabled ? 'Режим включён' : 'Включить режим'}
        </button>
      </div>

      {enabled && (
        <div className="streamer-layout-workspace">
          <div>
            <div className="streamer-selection-controls">
              <button
                id="btn-select-webcam"
                type="button"
                className={`btn btn-sm ${selectionMode === 'webcam' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setSelectionMode('webcam')}
              >
                Выделить вебку
              </button>
              <button
                id="btn-select-gameplay"
                type="button"
                className={`btn btn-sm ${selectionMode === 'gameplay' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setSelectionMode('gameplay')}
              >
                Выделить геймплей
              </button>
              <span>Зажмите мышь и обведите область</span>
            </div>
            <div
              ref={stageRef}
              className={`streamer-source-stage streamer-mode-${selectionMode}`}
              onPointerDown={(event) => {
                dragStartRef.current = { clientX: event.clientX, clientY: event.clientY };
                event.currentTarget.setPointerCapture?.(event.pointerId);
              }}
              onPointerMove={updateDraft}
              onPointerUp={finishDrag}
              onPointerCancel={() => {
                dragStartRef.current = null;
                setDraftRegion(null);
              }}
              style={{ aspectRatio: sourceAspectRatio }}
            >
              {src ? (
                <video
                  ref={videoRef}
                  src={src}
                  muted
                  playsInline
                  onLoadedMetadata={(event) => {
                    const { videoWidth, videoHeight } = event.currentTarget;
                    if (videoWidth > 0 && videoHeight > 0) setSourceAspectRatio(videoWidth / videoHeight);
                    if (Number.isFinite(currentTime)) event.currentTarget.currentTime = currentTime;
                  }}
                />
              ) : <div>Видео недоступно</div>}
              <SelectionBox region={webcamRegion} kind="webcam" />
              <SelectionBox region={gameplayRegion} kind="gameplay" />
              <SelectionBox region={draftRegion} kind={selectionMode} />
            </div>
          </div>

          <div className="streamer-result-column">
            <div className="streamer-result-label">Предпросмотр 9:16</div>
            <div className="streamer-result-preview">
              <div style={{ height: `${webcamHeightRatio * 100}%` }}>
                <CroppedPreview
                  src={src}
                  region={webcamRegion}
                  label="Вебка"
                  currentTime={currentTime}
                  sourceAspectRatio={sourceAspectRatio}
                  paneAspectRatio={(9 / 16) / webcamHeightRatio}
                />
              </div>
              <div style={{ height: `${(1 - webcamHeightRatio) * 100}%` }}>
                <CroppedPreview
                  src={src}
                  region={gameplayRegion}
                  label="Геймплей"
                  currentTime={currentTime}
                  sourceAspectRatio={sourceAspectRatio}
                  paneAspectRatio={(9 / 16) / (1 - webcamHeightRatio)}
                />
              </div>
            </div>
            <label className="streamer-ratio-control" htmlFor="streamer-webcam-height">
              Высота вебки: {Math.round(webcamHeightRatio * 100)}%
              <input
                id="streamer-webcam-height"
                type="range"
                min="0.2"
                max="0.5"
                step="0.01"
                value={webcamHeightRatio}
                onChange={(event) => onWebcamHeightRatioChange(Number(event.target.value))}
              />
            </label>
          </div>
        </div>
      )}
    </section>
  );
}
