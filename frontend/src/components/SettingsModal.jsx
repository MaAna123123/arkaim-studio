import { useState, useEffect } from 'react';

const PROVIDERS = [
  {
    id: 'gemini',
    name: '🌌 Google Gemini',
    desc: 'Бесплатный тариф Gemini 2.0 Flash (очень быстрый)',
    defaultModel: 'gemini-2.0-flash',
    requiresKey: true,
    keyLink: 'https://aistudio.google.com/apikey',
  },
  {
    id: 'deepseek',
    name: '🐋 DeepSeek API',
    desc: 'DeepSeek-V3 и DeepSeek-R1 (топовый анализ сюжета)',
    defaultModel: 'deepseek-chat',
    baseUrl: 'https://api.deepseek.com/v1',
    requiresKey: true,
    keyLink: 'https://platform.deepseek.com/api_keys',
  },
  {
    id: 'openrouter',
    name: '🆓 OpenRouter (Бесплатный Qwen & DeepSeek)',
    desc: '100% Бесплатные модели Qwen 2.5 72B и DeepSeek R1',
    defaultModel: 'qwen/qwen-2.5-72b-instruct:free',
    baseUrl: 'https://openrouter.ai/api/v1',
    requiresKey: true,
    keyLink: 'https://openrouter.ai/keys',
  },
  {
    id: 'ollama',
    name: '💻 Ollama (Локальный ИИ на ПК)',
    desc: '100% Бесплатный ИИ прямо на вашей видеокарте/ПК (без интернета)',
    defaultModel: 'qwen2.5:7b',
    baseUrl: 'http://localhost:11434/v1',
    requiresKey: false,
  },
];

export default function SettingsModal({ apiBase, onClose }) {
  const [provider, setProvider] = useState('gemini');
  const [apiKey, setApiKey] = useState('');
  const [maskedKey, setMaskedKey] = useState('');
  const [hasKey, setHasKey] = useState(false);
  const [modelName, setModelName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [whisperModel, setWhisperModel] = useState('small');

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetch(`${apiBase}/api/settings`)
      .then((r) => r.json())
      .then((d) => {
        if (d.provider) setProvider(d.provider);
        setHasKey(d.api_key_set);
        setMaskedKey(d.api_key_masked || '');
        if (d.model_name) setModelName(d.model_name);
        if (d.base_url) setBaseUrl(d.base_url);
        if (d.whisper_model) setWhisperModel(d.whisper_model);
      })
      .catch(() => {});
  }, [apiBase]);

  const activeProviderObj = PROVIDERS.find((p) => p.id === provider) || PROVIDERS[0];

  // При смене провайдера модель и адрес обязательно сбрасываются на его собственные.
  // Иначе, например, DeepSeek получал модель "gemini-2.0-flash" и отвечал ошибкой.
  const handleProviderChange = (p) => {
    setProvider(p.id);
    setModelName(p.defaultModel || '');
    setBaseUrl(p.baseUrl || '');
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const r = await fetch(`${apiBase}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider,
          api_key: apiKey,
          model_name: modelName || activeProviderObj.defaultModel,
          base_url: baseUrl || activeProviderObj.baseUrl || '',
          whisper_model: whisperModel,
        }),
      });
      if (!r.ok) throw new Error('save failed');
      setSaved(true);
      if (apiKey) setHasKey(true);
      setApiKey('');
      setTimeout(() => setSaved(false), 3000);
    } catch {
      alert('Ошибка сохранения настроек');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-box" style={{ maxWidth: 580 }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <h2 style={{ fontSize: 20, fontWeight: 800 }}>⚙️ Настройки ИИ-нейросети</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
              Выберите провайдера для смыслового поиска историй
            </p>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        {/* Provider selector */}
        <div className="settings-field">
          <label className="settings-label">Провайдер Нейросети</label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 16 }}>
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                id={`btn-provider-${p.id}`}
                className={`prompt-chip ${provider === p.id ? 'active' : ''}`}
                style={{ textAlign: 'left', padding: 12 }}
                onClick={() => handleProviderChange(p)}
              >
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>{p.name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{p.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* API Key field if provider requires key */}
        {activeProviderObj.requiresKey && (
          <div className="settings-field">
            <label className="settings-label">API Key для {activeProviderObj.name}</label>
            <div className="settings-key-row">
              <input
                id="settings-api-key-input"
                type="password"
                className="input settings-key-input"
                placeholder={hasKey ? maskedKey || '••••••••••••' : 'Вставьте ваш API ключ...'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
            {activeProviderObj.keyLink && (
              <div className="settings-hint">
                <a href={activeProviderObj.keyLink} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-secondary)' }}>
                  Получить ключ для {activeProviderObj.name} →
                </a>
              </div>
            )}
          </div>
        )}

        {/* Model name override */}
        <div className="settings-field" style={{ marginTop: 16 }}>
          <label className="settings-label">Модель ИИ</label>
          <input
            id="settings-model-input"
            type="text"
            className="input"
            placeholder={activeProviderObj.defaultModel}
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
          />
          <div className="settings-hint">
            Например: <code>qwen/qwen-2.5-72b-instruct:free</code>, <code>deepseek-chat</code>, <code>deepseek-r1:free</code>
          </div>
        </div>

        <hr className="divider" />

        {/* Whisper — запасное распознавание речи */}
        <div className="settings-field">
          <label className="settings-label">Распознавание речи (запасной вариант)</label>
          <select
            id="settings-whisper-select"
            className="input"
            value={whisperModel}
            onChange={(e) => setWhisperModel(e.target.value)}
          >
            <option value="tiny">tiny — самый быстрый, качество низкое</option>
            <option value="base">base — быстрый, среднее качество</option>
            <option value="small">small — рекомендуется для русского</option>
            <option value="medium">medium — точнее, но заметно медленнее</option>
          </select>
          <div className="settings-hint">
            Используется только если у видео нет субтитров. При наличии субтитров YouTube
            они берутся напрямую — это занимает пару секунд вместо десятков минут.
          </div>
        </div>

        <hr className="divider" />

        {/* Save button */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {saved ? '✅ Настройки сохранены!' : ''}
          </span>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
            <button id="btn-settings-save" className={`btn ${saved ? 'btn-success' : 'btn-primary'}`} onClick={handleSave} disabled={saving}>
              {saved ? '✓ Сохранено' : saving ? 'Сохраняю...' : 'Сохранить'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
