import { useEffect, useMemo, useRef, useState } from 'react';
import { updateCompositionLayer } from '../lib/clipComposition.js';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const round = (value) => Math.round(value * 10000) / 10000;

function mediaStyle(rect, fit) {
  return {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.width * 100}%`,
    height: `${rect.height * 100}%`,
    objectFit: fit === 'stretch' ? 'fill' : fit,
  };
}

function croppedSourceStyle(crop, fit, sourceAspect, canvasAspect, destination) {
  const destinationAspect = canvasAspect * destination.width / destination.height;
  const cropAspect = sourceAspect * crop.width / crop.height;
  const cover = fit === 'cover';
  const useFullWidth = cover ? cropAspect <= destinationAspect : cropAspect >= destinationAspect;
  const renderedWidth = useFullWidth ? 100 : (cropAspect / destinationAspect) * 100;
  const renderedHeight = useFullWidth ? (destinationAspect / cropAspect) * 100 : 100;
  const renderedLeft = (100 - renderedWidth) / 2;
  const renderedTop = (100 - renderedHeight) / 2;
  return {
    position: 'absolute',
    left: `${renderedLeft - (crop.x / crop.width) * renderedWidth}%`,
    top: `${renderedTop - (crop.y / crop.height) * renderedHeight}%`,
    width: `${renderedWidth / crop.width}%`,
    height: `${renderedHeight / crop.height}%`,
    maxWidth: 'none',
    objectFit: 'fill',
  };
}

export default function CompositionCanvas({
  sourceUrl,
  previewTime = 0,
  composition,
  selectedLayer,
  bannerMedia,
  adMedia,
  sourceWidth = 0,
  sourceHeight = 0,
  onCompositionChange,
}) {
  const canvasRef = useRef(null);
  const sourceRef = useRef(null);
  const bannerRef = useRef(null);
  const adRef = useRef(null);
  const dragRef = useRef(null);
  const [intrinsicSize, setIntrinsicSize] = useState({ width: 0, height: 0 });
  const effectiveSourceWidth = Number(sourceWidth) || intrinsicSize.width || 16;
  const effectiveSourceHeight = Number(sourceHeight) || intrinsicSize.height || 9;
  const sourceAspect = effectiveSourceWidth / effectiveSourceHeight;
  const canvasAspect = composition.output_aspect_ratio === 'original' ? sourceAspect : 9 / 16;

  const layer = useMemo(() => {
    if (selectedLayer === 'main_layer') return composition.main_layer;
    return composition[selectedLayer];
  }, [composition, selectedLayer]);

  useEffect(() => {
    const relative = Math.max(0, previewTime - composition.source_start);
    if (sourceRef.current && Math.abs(sourceRef.current.currentTime - previewTime) > 0.2) {
      sourceRef.current.currentTime = previewTime;
    }
    for (const media of [bannerRef.current, adRef.current]) {
      if (media && Number.isFinite(media.duration) && media.duration > 0) media.currentTime = relative % media.duration;
    }
  }, [composition.source_start, previewTime]);

  const beginDrag = (event, mode) => {
    if (!layer?.destination || !canvasRef.current) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      mode,
      startX: event.clientX,
      startY: event.clientY,
      rect: { ...layer.destination },
      bounds: canvasRef.current.getBoundingClientRect(),
    };
  };

  const moveDrag = (event) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = (event.clientX - drag.startX) / Math.max(1, drag.bounds.width);
    const dy = (event.clientY - drag.startY) / Math.max(1, drag.bounds.height);
    const rect = { ...drag.rect };
    if (drag.mode === 'move') {
      rect.x = round(clamp(drag.rect.x + dx, 0, 1 - rect.width));
      rect.y = round(clamp(drag.rect.y + dy, 0, 1 - rect.height));
    } else {
      rect.width = round(clamp(drag.rect.width + dx, 0.03, 1 - rect.x));
      rect.height = round(clamp(drag.rect.height + dy, 0.03, 1 - rect.y));
    }
    onCompositionChange(updateCompositionLayer(composition, selectedLayer, { destination: rect }));
  };

  const endDrag = () => { dragRef.current = null; };
  const selectedClass = (name) => selectedLayer === name ? ' selected' : '';

  return (
    <div
      ref={canvasRef}
      className={`composition-canvas ${composition.output_aspect_ratio === 'original' ? 'is-landscape' : ''}`}
      style={{ aspectRatio: composition.output_aspect_ratio === 'original' ? `${effectiveSourceWidth} / ${effectiveSourceHeight}` : '9 / 16' }}
      onPointerMove={moveDrag}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {sourceUrl && (
        <div
          className={`composition-layer main-layer${selectedClass('main_layer')}`}
          style={mediaStyle(composition.main_layer.destination, composition.main_layer.fit)}
          onPointerDown={(event) => selectedLayer === 'main_layer' && beginDrag(event, 'move')}
        >
          <video
            ref={sourceRef}
            src={sourceUrl}
            muted
            playsInline
            preload="metadata"
            onLoadedMetadata={(event) => setIntrinsicSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })}
            style={croppedSourceStyle(composition.main_layer.crop, composition.main_layer.fit, sourceAspect, canvasAspect, composition.main_layer.destination)}
          />
        </div>
      )}
      {composition.banner_layer?.enabled && bannerMedia?.url && (
        <video
          ref={bannerRef}
          className={`composition-layer${selectedClass('banner_layer')}`}
          src={bannerMedia.url}
          muted loop playsInline preload="metadata"
          style={mediaStyle(composition.banner_layer.destination, composition.banner_layer.fit)}
          onPointerDown={(event) => selectedLayer === 'banner_layer' && beginDrag(event, 'move')}
        />
      )}
      {composition.ad_layer?.enabled && composition.ad_layer.mode === 'overlay' && adMedia?.url && (
        <video
          ref={adRef}
          className={`composition-layer${selectedClass('ad_layer')}`}
          src={adMedia.url}
          muted loop playsInline preload="metadata"
          style={mediaStyle(composition.ad_layer.destination, composition.ad_layer.fit)}
          onPointerDown={(event) => selectedLayer === 'ad_layer' && beginDrag(event, 'move')}
        />
      )}
      {layer?.destination && (
        <button
          type="button"
          className="composition-resize-handle"
          style={{
            left: `${(layer.destination.x + layer.destination.width) * 100}%`,
            top: `${(layer.destination.y + layer.destination.height) * 100}%`,
          }}
          aria-label="Изменить размер выбранного слоя"
          onPointerDown={(event) => beginDrag(event, 'resize')}
        />
      )}
      {!sourceUrl && <div className="composition-canvas-empty">Предпросмотр исходника недоступен</div>}
    </div>
  );
}
