import { Component } from 'react';
import type { ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          minHeight: '50vh', padding: 40, textAlign: 'center',
        }}>
          <h2 style={{ fontSize: '1.4rem', marginBottom: 8 }}>页面出错了</h2>
          <p style={{ color: 'var(--text2)', marginBottom: 20, maxWidth: 480 }}>
            {this.state.error?.message || '未知错误'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: undefined })}
            style={{
              padding: '8px 24px', borderRadius: 8, border: '1px solid var(--border)',
              background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: '0.9rem',
            }}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
