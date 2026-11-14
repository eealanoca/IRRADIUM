'use client';

import { useEffect, useId, useState } from 'react';
import { DoseCut } from '../../components/results/PositionDoseCuts';
import { HIDDEN_CELL, orientDoseImage, readDoseImage, type DoseImage } from '../../components/results/doseViewFrame';
import { API_URL } from '../../lib/api';
import type { RoutePosition } from './RouteConfigurationView';

export type RouteAlignment = {
  mesh_frame: 'load-local';
  reference_position: string;
  reference_yaw_degrees: number;
  turns: { from_position: string; to_position: string; yaw_degrees: number }[];
};

type MeshCache = Map<string, DoseImage>;

type CutSet = { title: string; image: DoseImage }[];

function TurnCuts({ before, after, referenceYaw, reference, sumUrl, interval, cache }: {
  before: { name: string; url: string };
  after: { name: string; url: string; yaw: number };
  referenceYaw: number;
  reference: string;
  sumUrl: string;
  interval: string;
  cache: MeshCache;
}) {
  const [result, setResult] = useState<{ cuts: CutSet; scale: [number, number] }>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const { name: beforeName, url: beforeUrl } = before;
  const { name: afterName, url: afterUrl, yaw } = after;
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
    async function load(url: string) {
      const cached = cache.get(url);
      if (cached) {
        cache.delete(url);
        cache.set(url, cached);
        return cached;
      }
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error('No se pudo cargar una malla de la superposición.');
      const image = await readDoseImage(await response.arrayBuffer());
      if (!controller.signal.aborted) {
        cache.set(url, image);
        if (cache.size > 3) {
          const oldest = cache.keys().next().value;
          if (oldest) cache.delete(oldest);
        }
      }
      return image;
    }
    async function compare() {
      const first = await load(beforeUrl);
      const second = await load(afterUrl);
      const sum = await load(sumUrl);
      if (controller.signal.aborted) return;
      const cuts = [
        { title: `${beforeName} · alineada con ${reference}`, image: orientDoseImage(first, referenceYaw) },
        { title: `${afterName} · vista en el irradiador`, image: orientDoseImage(second, yaw) },
        { title: `${afterName} · alineada con ${reference}`, image: orientDoseImage(second, referenceYaw) },
        { title: `Suma ${interval}`, image: orientDoseImage(sum, referenceYaw) },
      ];
      let maximum = 0;
      for (const { image } of cuts) {
        const [nx, ny, nz] = image.dimensions;
        const start = nx * ny * Math.floor(nz / 2);
        for (let i = start; i < start + nx * ny; i++) {
          const value = image.values[i];
          if ((!image.ghosts || !(image.ghosts[i] & HIDDEN_CELL)) && Number.isFinite(value)) maximum = Math.max(maximum, value);
        }
      }
      setResult({ cuts, scale: [0, maximum] });
    }
    void compare().catch((cause: Error) => {
      if (active) setError(timedOut ? 'La carga de las mallas tardó demasiado. Puedes volver a intentarlo.' : cause.message);
    }).finally(() => clearTimeout(timeout));
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [afterName, afterUrl, attempt, beforeName, beforeUrl, cache, interval, reference, referenceYaw, sumUrl, yaw]);

  if (!result) return <div className="inline-notice" role="status">
    <p>{error || 'Cargando los aportes y la suma…'}</p>
    {error && <button type="button" className="button secondary" onClick={() => { setError(''); setAttempt((value) => value + 1); }}>Reintentar carga</button>}
  </div>;
  return <div className="cut-grid superposition-cuts">
    {result.cuts.map(({ image, title }) => <DoseCut key={title} image={image} title={title} plane="XY" unit="kGy/h" scale={result.scale}/>)}
  </div>;
}

export default function RouteSuperpositionCheck({ alignment, positions, interval, studyPath, sumPath }: {
  alignment: RouteAlignment;
  positions: RoutePosition[];
  interval: RoutePosition[];
  studyPath: string;
  sumPath: string;
}) {
  const [expanded, setExpanded] = useState(interval.length === 2);
  const [selected, setSelected] = useState('');
  const [cache] = useState<MeshCache>(() => new Map());
  const contentId = useId();
  const turn = alignment.turns.find((item) => item.from_position === selected) ?? alignment.turns[0];
  if (!turn || !interval.length) return null;
  const before = positions.findIndex((point) => point.name === turn.from_position);
  const after = positions.findIndex((point) => point.name === turn.to_position);

  return <div className="route-superposition-check">
    <button type="button" className="superposition-toggle" aria-expanded={expanded} aria-controls={contentId}
      onClick={() => setExpanded((current) => !current)}>
      <span aria-hidden="true">{expanded ? '▾' : '▸'}</span> Comprobar la superposición en el giro
    </button>
    {expanded && <div id={contentId} role="region" aria-label="Comprobación de la superposición">
      {alignment.turns.length > 1 && <label className="field"><span>Giro del recorrido</span>
        <select value={turn.from_position} onChange={(event) => setSelected(event.target.value)}>
          {alignment.turns.map((item) =>
            <option key={item.from_position} value={item.from_position}>{item.from_position} → {item.to_position}</option>)}
        </select>
      </label>}
      <p>Corte XY a media altura con la misma escala. Los mapas alineados siguen los mismos puntos físicos de la carga. La suma incluye todas las posiciones del intervalo.</p>
      {before >= 0 && after >= 0 ? <TurnCuts key={turn.from_position} cache={cache}
        before={{ name: turn.from_position, url: `${API_URL}${studyPath}/maps/${before}` }}
        after={{ name: turn.to_position, yaw: positions[after].yaw_degrees, url: `${API_URL}${studyPath}/maps/${after}` }}
        reference={alignment.reference_position} referenceYaw={alignment.reference_yaw_degrees}
        interval={`${interval[0].name}–${interval[interval.length - 1].name}`} sumUrl={`${API_URL}${sumPath}`}/>
        : <p className="inline-notice" role="status">No están disponibles las dos posiciones de este giro en el estudio seleccionado.</p>}
    </div>}
  </div>;
}
