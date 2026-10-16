const SESSION_KEY = 'videopril.session.v1';


export function clearSession(storage) {
  try {
    storage?.removeItem(SESSION_KEY);
  } catch {
    // Session persistence is optional.
  }
}

export function loadSession(storage) {
  try {
    const raw = storage?.getItem(SESSION_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return {};
    if (parsed.video && typeof parsed.video !== 'object') {
      clearSession(storage);
      return {};
    }
    return parsed;
  } catch {
    clearSession(storage);
    return {};
  }
}

export function saveSession(storage, session) {
  try {
    storage?.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // The active editor continues even when browser storage is unavailable.
  }
}
