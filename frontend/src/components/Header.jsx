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
          <div className="header-logo-icon" aria-hidden="true">A</div>
          <span className="header-brand-copy">
            <span className="header-logo-text">АРКАИМ <b>STUDIO</b></span>
            <span className="header-logo-subtitle">SECURE VIDEO WORKSPACE</span>
          </span>
        </div>

        <div className="header-status">
          <div className={`status-dot ${online ? 'online' : 'offline'}`} />
          <span>{online ? 'Локальный контур защищён' : 'Backend недоступен'}</span>
        </div>

        <div className="header-actions">
          <button
            className="btn btn-secondary btn-sm"
            id="btn-settings"
            onClick={onSettings}
          >
            <span aria-hidden="true">⚙</span> Настройки
          </button>
        </div>
      </div>
    </header>
  );
}
