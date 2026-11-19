'use client';

import { useEffect } from 'react';
import { SERVER_URL } from '../../lib/api';

export default function DesktopSession() {
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (disposed || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
      const url = new URL('/api/desktop/session', SERVER_URL);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url);
      socket.onclose = () => {
        if (!disposed) retry = setTimeout(connect, 1000);
      };
    };
    const leave = () => {
      disposed = true;
      clearTimeout(retry);
      socket?.close();
    };
    const enter = () => {
      disposed = false;
      connect();
    };

    let enabled = false;
    fetch(`${SERVER_URL}/api/health`, { cache: 'no-store' })
      .then(response => response.json())
      .then(status => {
        if (disposed || !status.desktop_session) return;
        enabled = true;
        connect();
        window.addEventListener('pagehide', leave);
        window.addEventListener('pageshow', enter);
      })
      .catch(() => {});

    return () => {
      leave();
      if (enabled) {
        window.removeEventListener('pagehide', leave);
        window.removeEventListener('pageshow', enter);
      }
    };
  }, []);

  return null;
}
