'use client';

import { useEffect, useState } from 'react';
import { ASSET_RELOAD_MESSAGE, isAssetLoadError } from '../../lib/assetLoadError';

export default function InterfaceUpdateNotice() {
  const [needed, setNeeded] = useState(false);

  useEffect(() => {
    const preloadFailed = () => setNeeded(true);
    const rejected = (event: PromiseRejectionEvent) => {
      if (isAssetLoadError(event.reason)) setNeeded(true);
    };
    const failed = (event: ErrorEvent) => {
      if (isAssetLoadError(event.error ?? event.message)) setNeeded(true);
    };
    window.addEventListener('vite:preloadError', preloadFailed);
    window.addEventListener('unhandledrejection', rejected);
    window.addEventListener('error', failed);
    return () => {
      window.removeEventListener('vite:preloadError', preloadFailed);
      window.removeEventListener('unhandledrejection', rejected);
      window.removeEventListener('error', failed);
    };
  }, []);

  if (!needed) return null;
  return <aside className="interface-update-notice" role="alert">
    <div><strong>Recargar la interfaz</strong><p>{ASSET_RELOAD_MESSAGE}</p></div>
    <button type="button" className="button secondary" onClick={() => window.location.reload()}>Recargar página</button>
  </aside>;
}
