const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const rounded = (value) => Math.round(value * 10000) / 10000;

export function normalizeCropRegion(value) {
  if (!value || typeof value !== 'object') return null;
  const region = {
    x: Number(value.x),
    y: Number(value.y),
    width: Number(value.width),
    height: Number(value.height),
  };
  if (!Object.values(region).every(Number.isFinite)) return null;
  if (region.x < 0 || region.y < 0 || region.width < 0.03 || region.height < 0.03) return null;
  if (region.x + region.width > 1 || region.y + region.height > 1) return null;
  return region;
}

export function normalizeWebcamHeightRatio(value, fallback = 0.35) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0.2 && value <= 0.5
    ? value
    : fallback;
}

export function normalizedRectFromDrag(start, end, bounds) {
  if (!bounds?.width || !bounds?.height) return null;
  const startX = clamp(start.clientX - bounds.left, 0, bounds.width);
  const startY = clamp(start.clientY - bounds.top, 0, bounds.height);
  const endX = clamp(end.clientX - bounds.left, 0, bounds.width);
  const endY = clamp(end.clientY - bounds.top, 0, bounds.height);
  const x = Math.min(startX, endX) / bounds.width;
  const y = Math.min(startY, endY) / bounds.height;
  const width = Math.abs(endX - startX) / bounds.width;
  const height = Math.abs(endY - startY) / bounds.height;
  if (width < 0.03 || height < 0.03) return null;
  return { x: rounded(x), y: rounded(y), width: rounded(width), height: rounded(height) };
}

export function previewCoverWindow(region, sourceAspectRatio, paneAspectRatio) {
  if (!region || sourceAspectRatio <= 0 || paneAspectRatio <= 0) return null;
  const selectedAspectRatio = sourceAspectRatio * region.width / region.height;
  if (selectedAspectRatio >= paneAspectRatio) {
    const width = selectedAspectRatio / paneAspectRatio * 100;
    return { width, height: 100, left: -(width - 100) / 2, top: 0 };
  }
  const height = paneAspectRatio / selectedAspectRatio * 100;
  return { width: 100, height, left: 0, top: -(height - 100) / 2 };
}
