'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import VtkDoseViewer, { type VtkMaterial } from '../../components/results/VtkDoseViewer';
import { boundedPreview } from '../../components/results/doseSurface';
import { readDoseImage } from '../../components/results/doseViewFrame';
import type { RouteDoseField } from '../../components/results/routeDoseSurface';
import { API_URL } from '../../lib/api';
import type { GammaSource } from '../../lib/types';
import type { RoutePosition } from './RouteConfigurationView';

const PREVIEW_CELL_BUDGET = 1_500_000;
const PARALLEL_DOWNLOADS = 3;

export default function StudyDoseScene({ positions, studyPath, sources, bottomCenter, requestedCount, active = true }: {
  positions: (RoutePosition & { materials?: VtkMaterial[]; simulated_particles?: number; summary?: string })[];
  studyPath: string;
  sources: GammaSource[];
  bottomCenter: boolean;
  requestedCount: number;
  active?: boolean;
}) {
  const cache = useRef(new Map<string, RouteDoseField>());
  const [result, setResult] = useState<{ studyPath: string; version: string; fields: RouteDoseField[]; failed: string[] }>();
  const [retry, setRetry] = useState(0);
  const cellsPerField = Math.max(1, Math.floor(PREVIEW_CELL_BUDGET / requestedCount));
  const keys = positions.map((position, index) => `${studyPath}/${cellsPerField}/${index}/${position.summary ?? ''}/${position.simulated_particles ?? ''}`);
  const version = JSON.stringify(keys);
  const materials = useMemo(() => [...new Map(positions.flatMap((position) =>
    (position.materials ?? []).map((material) => [material.id, material] as const))).values()], [positions]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const { signal } = controller;
    async function load() {
      const failed: string[] = [];
      const keys: string[] = JSON.parse(version);
      for (const key of cache.current.keys()) {
        if (!keys.includes(key)) cache.current.delete(key);
      }
      const missing = positions.flatMap((position, index) =>
        cache.current.has(keys[index]) ? [] : [{ position, index }]);
      for (let start = 0; start < missing.length && !signal.aborted; start += PARALLEL_DOWNLOADS) {
        await Promise.all(missing.slice(start, start + PARALLEL_DOWNLOADS).map(async ({ position, index }) => {
          try {
            const response = await fetch(`${API_URL}${studyPath}/maps/${index}`, { signal, cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const buffer = await response.arrayBuffer();
            if (signal.aborted) return;
            const image = boundedPreview(await readDoseImage(buffer), cellsPerField);
            if (!signal.aborted) {
              cache.current.set(keys[index], { name: position.name, centerMm: position.center_mm, yaw: position.yaw_degrees, image });
            }
          } catch {
            if (!signal.aborted) failed.push(position.name);
          }
        }));
      }
      if (!signal.aborted) {
        const fields = positions.flatMap((_, index) => {
          const entry = cache.current.get(keys[index]);
          return entry ? [entry] : [];
        });
        setResult({ studyPath, version, fields, failed });
      }
    }
    void load();
    return () => controller.abort();
  }, [active, cellsPerField, positions, retry, studyPath, version]);

  // Keep the mounted viewer while this study publishes another position.
  // Removing it for one fetch resets its level, clipping and material filters.
  // A different study must never display the previous study's maps.
  const currentResult = result?.studyPath === studyPath ? result : undefined;
  const route = useMemo(() => currentResult && ({ fields: currentResult.fields, sources, bottomCenter }), [bottomCenter, currentResult, sources]);
  const loading = currentResult?.version !== version;
  return <section className="study-dose-scene" aria-label="Tasa de dosis 3D del recorrido">
    {loading && <p className="inline-notice" role="status">{currentResult ? 'Actualizando' : 'Cargando'} las distribuciones de {positions.length} posiciones…</p>}
    {currentResult && currentResult.failed.length > 0 && <p className="inline-notice" role="status">
      No se pudieron cargar {currentResult.failed.join(', ')}. <button type="button" className="button secondary" onClick={() => setRetry((attempt) => attempt + 1)}>Reintentar</button>
    </p>}
    {active && route && route.fields.length > 0 && <VtkDoseViewer route={route} materials={materials} label="Mapas por posición en el irradiador"/>}
  </section>;
}
