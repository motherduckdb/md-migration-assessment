/**
 * Local-mode entry point. Mounts the Dive exactly as MotherDuck would, with a
 * thin bar above it that states the mode and which file is being read. The
 * Dive source itself knows nothing about modes.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import Dive from '../src/index';

type Info = { mode: string; database: string; alias: string; tool_version?: string };

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, color: '#bc1200', fontFamily: 'system-ui, sans-serif' }}>
          <strong>The dashboard failed to render.</strong>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{String(this.state.error.stack ?? this.state.error)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

function LocalBar() {
  const [info, setInfo] = React.useState<Info | null>(null);
  React.useEffect(() => {
    fetch(new URL('api/info', document.baseURI).toString())
      .then((r) => r.json())
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);
  return (
    <div
      style={{
        fontFamily: 'system-ui, -apple-system, sans-serif',
        fontSize: 12,
        color: '#6a6a6a',
        background: '#fff',
        borderBottom: '1px solid #e5e5e5',
        padding: '6px 16px',
        display: 'flex',
        gap: 12,
        alignItems: 'baseline',
      }}
    >
      <strong style={{ color: '#231f20' }}>Local mode</strong>
      <span>
        {info ? (
          <>
            reading <code>{info.database}</code> read-only over loopback; nothing leaves this machine.
          </>
        ) : (
          'served from this machine; nothing leaves it.'
        )}
      </span>
      <span style={{ marginLeft: 'auto' }}>
        To share inside a MotherDuck organization: <code>md-assess publish</code>
      </span>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LocalBar />
    <ErrorBoundary>
      <Dive />
    </ErrorBoundary>
  </React.StrictMode>,
);
