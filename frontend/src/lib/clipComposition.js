const MIN_RECT_SIZE = 0.03;
const MIN_CLIP_DURATION = 0.5;
const MAIN_FITS = new Set(['contain', 'cover']);
const OVERLAY_FITS = new Set(['contain', 'cover', 'stretch']);
const AD_MODES = new Set(['insert', 'overlay']);

const clone = (value) => {
  if (Array.isArray(value)) return value.map(clone);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)]));
  }
  return value;
};

const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

function normalizeRect(value) {
  if (!value || typeof value !== 'object') return null;
  const rect = {
    x: Number(value.x),
    y: Number(value.y),
    width: Number(value.width),
    height: Number(value.height),
  };
  if (!Object.values(rect).every(Number.isFinite)) return null;
  if (rect.x < 0 || rect.y < 0 || rect.width < MIN_RECT_SIZE || rect.height < MIN_RECT_SIZE) return null;
  if (rect.x + rect.width > 1 || rect.y + rect.height > 1) return null;
  return rect;
}

function normalizeOverlay(value, duration, { ad = false } = {}) {
  if (value == null) return null;
  if (!value || typeof value !== 'object') return null;
  const destination = normalizeRect(value.destination);
  const start = Number(value.start);
  const end = Number(value.end);
  if (!destination || !OVERLAY_FITS.has(value.fit)) return null;
  if (![start, end].every(Number.isFinite) || start < 0 || end <= start || end > duration) return null;
  const normalized = {
    enabled: value.enabled !== false,
    destination,
    fit: value.fit,
    start,
    end,
  };
  if (ad) {
    if (!AD_MODES.has(value.mode)) return null;
    normalized.mode = value.mode;
  }
  return normalized;
}

export function createDefaultComposition(segment = {}, outputAspectRatio = '9:16') {
  const rawStart = Number(segment.start);
  const rawEnd = Number(segment.end);
  const sourceStart = Number.isFinite(rawStart) ? Math.max(0, rawStart) : 0;
  const sourceEnd = Number.isFinite(rawEnd) && rawEnd >= sourceStart + MIN_CLIP_DURATION
    ? rawEnd
    : sourceStart + MIN_CLIP_DURATION;
  return {
    source_start: sourceStart,
    source_end: sourceEnd,
    output_aspect_ratio: outputAspectRatio === 'original' ? 'original' : '9:16',
    main_layer: {
      crop: { x: 0, y: 0, width: 1, height: 1 },
      destination: { x: 0, y: 0, width: 1, height: 1 },
      fit: 'cover',
    },
    banner_layer: null,
    ad_layer: null,
  };
}

export function compositionDuration(composition) {
  const start = Number(composition?.source_start);
  const end = Number(composition?.source_end);
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0;
}

export function normalizeComposition(value, segment = {}, sourceDuration = 0) {
  if (!value || typeof value !== 'object') return null;
  const fallback = createDefaultComposition(segment, value.output_aspect_ratio);
  const durationLimit = finite(sourceDuration) && sourceDuration > 0
    ? sourceDuration
    : Math.max(fallback.source_end, Number(value.source_end) || 0);
  const sourceStartValue = Number(value.source_start);
  const sourceEndValue = Number(value.source_end);
  if (![sourceStartValue, sourceEndValue].every(Number.isFinite)) return null;
  const sourceStart = clamp(sourceStartValue, 0, durationLimit);
  const sourceEnd = clamp(sourceEndValue, 0, durationLimit);
  if (sourceEnd - sourceStart < MIN_CLIP_DURATION) return null;
  if (!['9:16', 'original'].includes(value.output_aspect_ratio)) return null;

  const crop = normalizeRect(value.main_layer?.crop);
  const destination = normalizeRect(value.main_layer?.destination);
  if (!crop || !destination || !MAIN_FITS.has(value.main_layer?.fit)) return null;

  const normalized = {
    source_start: sourceStart,
    source_end: sourceEnd,
    output_aspect_ratio: value.output_aspect_ratio,
    main_layer: { crop, destination, fit: value.main_layer.fit },
    banner_layer: null,
    ad_layer: null,
  };
  const editedDuration = compositionDuration(normalized);
  if (value.banner_layer != null) {
    normalized.banner_layer = normalizeOverlay(value.banner_layer, editedDuration);
    if (!normalized.banner_layer) return null;
  }
  if (value.ad_layer != null) {
    normalized.ad_layer = normalizeOverlay(value.ad_layer, editedDuration, { ad: true });
    if (!normalized.ad_layer) return null;
  }
  return normalized;
}

export function updateCompositionLayer(composition, layerName, patch) {
  if (!['main_layer', 'banner_layer', 'ad_layer'].includes(layerName)) return clone(composition);
  const next = clone(composition);
  if (patch == null) {
    next[layerName] = null;
    return next;
  }
  next[layerName] = {
    ...(next[layerName] || {}),
    ...clone(patch),
  };
  return next;
}

export function serializeExportSegment(segment, sourceIndex = null) {
  const serialized = {
    start: Number(segment.start),
    end: Number(segment.end),
    label: typeof segment.label === 'string' ? segment.label : '',
  };
  if (typeof segment.transcript_text === 'string') {
    serialized.transcript_text = segment.transcript_text;
  }
  if (segment.composition && typeof segment.composition === 'object') {
    serialized.composition = clone(segment.composition);
  }
  if (Number.isInteger(sourceIndex) && sourceIndex >= 0) {
    serialized.segment_index = sourceIndex;
  }
  return serialized;
}

export { MIN_CLIP_DURATION, MIN_RECT_SIZE };
