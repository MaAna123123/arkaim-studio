import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Unhandled React Error:', error, errorInfo);
  }

  handleReset = () => {
    try {
      localStorage.clear();
    } catch {
      // ignore
    }
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#050505',
          color: '#f5f5f2',
          fontFamily: '"Manrope", "Segoe UI", system-ui, sans-serif',
          padding: 24,
          textAlign: 'center',
        }}>
          <div style={{ width: 44, height: 3, background: '#f2c66e', marginBottom: 24 }} />
          <h2 style={{ fontSize: 24, fontWeight: 800, marginBottom: 12 }}>
            Произошла ошибка при отображении интерфейса
          </h2>
          <p style={{ color: '#b7b7b1', maxWidth: 480, marginBottom: 24, fontSize: 14, lineHeight: 1.6 }}>
            Возможно, накопились устаревшие данные в кэше браузера. Нажмите кнопку ниже, чтобы сбросить кэш и перезапустить приложение.
          </p>
          <div style={{
            background: 'rgba(255, 68, 68, 0.1)',
            border: '1px solid rgba(255, 68, 68, 0.3)',
            borderRadius: 8,
            padding: 12,
            marginBottom: 24,
            maxWidth: 600,
            wordBreak: 'break-word',
            fontFamily: 'monospace',
            fontSize: 12,
            color: '#ff8888',
          }}>
            {this.state.error?.toString() || 'Неизвестная ошибка'}
          </div>
          <button
            onClick={this.handleReset}
            style={{
              background: '#f3f3ef',
              color: '#080808',
              border: 'none',
              borderRadius: 8,
              padding: '12px 28px',
              fontSize: 15,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            Сбросить кэш и перезапустить
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
