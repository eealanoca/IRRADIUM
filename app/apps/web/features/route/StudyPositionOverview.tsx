'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useEffect, useId, useState } from 'react';
import type { GammaSource, SourcesResponse } from '../../lib/types';
import { apiGet } from '../../lib/api';
import { isOblique, type Vector3 } from '../../components/results/doseViewFrame';
import type { MethodResult } from '../results/ResultsView';
import type { RouteConfiguration, RoutePosition } from './RouteConfigurationView';
import StudyDoseScene from './StudyDoseScene';
import type { VtkMaterial } from '../../components/results/VtkDoseViewer';

type PositionRate = RoutePosition & { mean_kgy_h: number; materials?: VtkMaterial[]; simulated_particles?: number; summary?: string };
type Layout = { sources: GammaSource[]; position_reference: RouteConfiguration['position_reference']; archived: boolean };
const format = (value: number) => value.toLocaleString(uiLocale(), { maximumSignificantDigits: 4 });

function StudyPlan({ points, allPositions, sources, size, selected, onSelect }: {
  points: { point: PositionRate; index: number }[];
  allPositions: PositionRate[];
  sources: GammaSource[];
  size: Vector3;
  selected?: number;
  onSelect: (index: number) => void;
}) {
  const arrow = useId();
  const extent = allPositions.flatMap((point) => {
    const [x, y] = point.center_mm;
    const angle = point.yaw_degrees * Math.PI / 180;
    const width = Math.abs(Math.cos(angle)) * size[0] + Math.abs(Math.sin(angle)) * size[1];
    const height = Math.abs(Math.sin(angle)) * size[0] + Math.abs(Math.cos(angle)) * size[1];
    return [[x - width / 2, -y - height / 2], [x + width / 2, -y + height / 2]];
  });
  const coordinates = extent.concat(sources.flatMap(({ lower_point_mm, upper_point_mm }) =>
    [lower_point_mm, upper_point_mm].map(([x, y]) => [x, -y])));
  const left = Math.min(...coordinates.map(([x]) => x));
  const right = Math.max(...coordinates.map(([x]) => x));
  const top = Math.min(...coordinates.map(([, y]) => y));
  const bottom = Math.max(...coordinates.map(([, y]) => y));
  const pad = Math.max((right - left) * .045, 65);
  const font = Math.max((right - left) / 47, Math.min(...size) * .22);
  const minWidth = Math.max(right - left + 2 * pad, (bottom - top + 2 * pad) * 1.5);
  const vx = (left + right - minWidth) / 2;

  return <svg className="study-position-plan" role="group" aria-label="Planta interactiva del recorrido" viewBox={`${vx} ${top - pad} ${minWidth} ${bottom - top + 2 * pad}`}>
    <defs><marker id={arrow} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-soft)"/>
    </marker></defs>
    {points.slice(1).map(({ point, index }, i) => {
      const previous = points[i];
      if (index !== previous.index + 1) return null;
      const [x0, y0] = previous.point.center_mm;
      const [x1, y1] = point.center_mm;
      const distance = Math.hypot(x1 - x0, y1 - y0);
      if (!distance) return null;
      const ux = (x1 - x0) / distance;
      const uy = (y1 - y0) / distance;
      const edge = (yaw: number) => {
        const angle = yaw * Math.PI / 180;
        const x = ux * Math.cos(angle) + uy * Math.sin(angle);
        const y = -ux * Math.sin(angle) + uy * Math.cos(angle);
        return Math.min(size[0] / (2 * Math.abs(x)), size[1] / (2 * Math.abs(y)));
      };
      const start = edge(previous.point.yaw_degrees) + font * .08;
      const end = distance - edge(point.yaw_degrees) - font * .11;
      if (end <= start) return null;
      return <line key={index} x1={x0 + ux * start} y1={-(y0 + uy * start)} x2={x0 + ux * end} y2={-(y0 + uy * end)}
        stroke="var(--text-soft)" strokeWidth={font * .065} markerEnd={`url(#${arrow})`}/>;
    })}
    {points.map(({ point, index }) => {
      const [x, y] = point.center_mm;
      return <g key={point.name} role="button" tabIndex={0} aria-pressed={index === selected} aria-label={`Mostrar ${point.name} en el visor`}
        onClick={() => onSelect(index)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(index); } }}>
        <title>{point.name} · {format(point.mean_kgy_h)} kGy/h</title>
        <rect x={x - size[0] / 2} y={-y - size[1] / 2} width={size[0]} height={size[1]} rx={font * .17}
          transform={`rotate(${-point.yaw_degrees} ${x} ${-y})`} strokeWidth={font * .035}/>
        <text x={x} y={-y - font * .16} textAnchor="middle" fontSize={font} fontWeight="650">{point.name}</text>
        <text x={x} y={-y + font * .80} textAnchor="middle" fontSize={font * .73}>{format(point.mean_kgy_h)}</text>
      </g>;
    })}
    {sources.map(({ id, lower_point_mm: [x0, y0], upper_point_mm: [x1, y1] }) =>
      <g key={id} className="study-position-source"><title>{id}</title>
        <line x1={x0} y1={-y0} x2={x1} y2={-y1} strokeWidth={font * .24}/>
        <circle cx={x0} cy={-y0} r={font * .16}/>
      </g>)}
  </svg>;
}

export default function StudyPositionOverview({ positions, studyPath, layout, selected, onSelect, active, requestedCount, occupancy, deterministicAir = false }: {
  positions: PositionRate[];
  studyPath: string;
  active: boolean;
  requestedCount: number;
  deterministicAir?: boolean;
  occupancy?: RouteConfiguration['occupancy'];
  layout?: { sources?: GammaSource[]; position_reference?: RouteConfiguration['position_reference'] };
  selected?: number;
  onSelect: (index: number) => void;
}) {
  const [view, setView] = useState<'plan' | '3d'>('3d');
  const [size, setSize] = useState<Vector3>();
  const [fallbackLayout, setFallbackLayout] = useState<Layout>();
  const [layoutError, setLayoutError] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    apiGet<MethodResult>(`${studyPath}/positions/0/analysis`).then(({ summary }) => {
      const mesh = summary?.mesh as { lower_left: Vector3; upper_right: Vector3 } | undefined;
      const dimensions = mesh?.lower_left?.map((low, axis) => 10 * (mesh.upper_right[axis] - low)) as Vector3 | undefined;
      if (!dimensions?.every((value) => Number.isFinite(value) && value > 0)) throw new Error('El estudio no contiene las dimensiones de la malla.');
      if (active) setSize(dimensions);
    }).catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [studyPath]);
  useEffect(() => {
    if (layout?.sources && layout.position_reference) return;
    let active = true;
    Promise.all([apiGet<SourcesResponse>('/api/config/sources'), apiGet<RouteConfiguration>('/api/route/configuration')])
      .then(([inventory, route]) => {
        if (active) setFallbackLayout({ sources: inventory.sources, position_reference: route.position_reference, archived: false });
      }).catch(() => { if (active) setLayoutError('No se pudo cargar la geometría del irradiador. Puedes abrir la planta para consultar las posiciones.'); });
    return () => { active = false; };
  }, [layout?.position_reference, layout?.sources]);

  const geometry: Layout | undefined = layout?.sources && layout.position_reference
    ? { sources: layout.sources, position_reference: layout.position_reference, archived: true } : fallbackLayout;
  const indexed = positions.map((point, index) => ({ point, index }));
  const lower = indexed.filter(({ point }) => /^I\d+$/i.test(point.name));
  const upper = indexed.filter(({ point }) => /^S\d+$/i.test(point.name));
  const levels = lower.length && upper.length && lower.length + upper.length === indexed.length
    ? [{ label: 'Nivel inferior', points: lower }, { label: 'Nivel superior', points: upper }]
    : [{ label: lower.length === indexed.length ? 'Nivel inferior' : upper.length === indexed.length ? 'Nivel superior' : 'Posiciones del estudio', points: indexed }];
  const current = selected === undefined ? undefined : positions[selected];

  return <div className="study-position-overview">
    <header><div><h4>{deterministicAir ? 'Recorrido de calibración' : 'Recorrido simulado'}</h4><p>{positions.length} posiciones con resultados{view === 'plan' && ' · Cada número indica la tasa media de la carga en kGy/h.'}</p>
      {deterministicAir ? <div className="study-occupancy"><strong>Carga llena de aire</strong><span>Campo libre</span></div> : <div className="study-occupancy" title={occupancy === 'isolated' ? 'Sin sombras de cargas vecinas. Incluye la atenuación dentro de la propia carga.' : 'Incluye la atenuación y la dispersión de las cargas vecinas.'}>
        <strong>{occupancy === 'isolated' ? 'Carga aislada' : occupancy === 'occupied' ? 'Ocupación completa' : 'Ocupación no registrada'}</strong>
        {occupancy === 'isolated' && <span>Sin sombras de cargas vecinas.</span>}
        {occupancy === 'occupied' && <span>Con atenuación y dispersión de las cargas vecinas.</span>}
      </div>}
    </div>
      <div className="result-analysis-tabs" role="tablist" aria-label="Vista del recorrido">
        <button type="button" role="tab" aria-selected={view === '3d'} onClick={() => setView('3d')}>Tasa de dosis 3D</button>
        <button type="button" role="tab" aria-selected={view === 'plan'} onClick={() => setView('plan')}>Planta +Z</button>
      </div>
    </header>
    {view === 'plan' && (size ? <div className="study-position-levels">
      {levels.map(({ label, points }) => <section key={label} aria-label={label}><h5>{label}</h5>
        <StudyPlan points={points} allPositions={positions} sources={geometry?.sources ?? []} size={size} selected={selected} onSelect={onSelect}/>
      </section>)}
    </div> : <p className="inline-notice" role="status">{error || 'Cargando dimensiones del estudio…'}</p>)}
    <div hidden={view !== '3d'}>{geometry ? <StudyDoseScene key={studyPath} positions={positions} studyPath={studyPath}
      active={active && view === '3d'} sources={geometry.sources} bottomCenter={geometry.position_reference === 'bottom_center'} requestedCount={requestedCount}/>
      : <p className="inline-notice" role="status">{layoutError || 'Cargando la geometría del irradiador…'}</p>}</div>
    <footer>{view === 'plan' && <span>+X → · +Y ↑</span>}
      {geometry && <span className="study-source-key">{geometry.archived ? 'Fuentes guardadas del caso' : 'Fuentes de la configuración actual · referencia visual'}</span>}
      {view === 'plan' && current && isOblique(current.yaw_degrees) && <span>Vista de tasa remuestreada para el ángulo oblicuo.</span>}
    </footer>
  </div>;
}
