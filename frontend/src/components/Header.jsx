export default function Header({ online, onSettings }) {
  return (
    <header className="header">
      <div className="header-inner">
        <div
          className="header-logo"
          style={{ cursor: 'pointer' }}
          title="Нажми для обновления страницы"
          onClick={() => window.location.reload()}
        >
          <div className="header-logo-icon">🎬</div>
          <span className="header-logo-text">videopril</span>
        </div>

        <div className="header-status">
          <div className={`status-dot ${online ? 'online' : 'offline'}`} />
          <span>{online ? 'Сервер онлайн' : 'Сервер офлайн'}</span>
        </div>

        <div className="header-actions">
          <button
            className="btn btn-secondary btn-sm"
            id="btn-settings"
            onClick={onSettings}
          >
            ⚙️ Настройки
          </button>
        </div>
      </div>
    </header>
  );
}
