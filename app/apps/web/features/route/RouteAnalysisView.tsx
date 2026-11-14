'use client';

import { useEffect, useState } from 'react';
import { MethodPanel, type MethodResult, type ResultTool } from '../results/ResultsView';
import { API_URL, apiGet } from '../../lib/api';
import type { VtkMaterial } from '../../components/results/VtkDoseViewer';
import { readableError } from './RouteConfigurationView';

export default function RouteAnalysisView({ path, caseId, title, materials, positionYaw, tool, onToolChange, accumulated = false, deterministicAir = false }: {
  path: string; caseId: string; title: string; materials?: VtkMaterial[]; positionYaw?: number; tool?: ResultTool; onToolChange?: (tool: ResultTool) => void; accumulated?: boolean; deterministicAir?: boolean;
}) {
  const [response, setResponse] = useState<{ path: string; result?: MethodResult; error?: string }>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    apiGet<MethodResult>(path).then((result) => { if (active) setResponse({ path, result }); })
      .catch((error) => { if (active) setResponse({ path, error: readableError(error) }); });
    return () => { active = false; };
  }, [path, attempt]);
  const { result, error } = response?.path === path ? response : {};
  if (error) return <div className="inline-notice" role="status">{error} <button type="button" className="button secondary" onClick={() => { setResponse(undefined); setAttempt((value) => value + 1); }}>Reintentar análisis</button></div>;
  if (!result) return <p className="inline-notice" role="status">Preparando herramientas de análisis…</p>;
  return <MethodPanel caseId={caseId} method={result} title={`${deterministicAir ? "Determinista" : "Monte Carlo"} · ${title}`} deterministicAir={deterministicAir} subtitle="Análisis del estudio" order="" materials={materials}
    hideHeader positionYaw={positionYaw} tool={tool} onToolChange={onToolChange} artifactBase={`${API_URL}${path}`} quantityUnit={accumulated ? 'kGy' : 'kGy/h'}/>;
}
