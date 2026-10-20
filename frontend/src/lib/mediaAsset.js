export function toEditorMedia(apiBase, data = {}, kind) {
  const available = kind === 'ad' ? data.has_ad : data.has_banner;
  const rawUrl = typeof data.url === 'string' ? data.url : '';
  return {
    has_media: Boolean(available),
    url: available && rawUrl ? `${apiBase}${rawUrl}` : '',
    raw_url: rawUrl,
    duration: Number(data.duration) || 0,
    width: Number(data.width) || 0,
    height: Number(data.height) || 0,
    filename: typeof data.filename === 'string' ? data.filename : '',
  };
}
