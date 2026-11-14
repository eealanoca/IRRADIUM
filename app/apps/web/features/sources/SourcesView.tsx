'use client';
import { uiLocale } from '../../lib/i18n/number';

import DialogFrame from '../../components/ui/DialogFrame';

import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Activity, Atom, CalendarDays, Edit3, Minus, Plus, Search, Trash2, X } from 'lucide-react';
import { apiGet, apiJson } from '../../lib/api';
import type { GammaSource, SourcesResponse } from '../../lib/types';

const EMPTY_SOURCE: GammaSource = {
  id: '', index: -1, radionuclide: 'Co-60', initial_activity_bq: 3.7e13,
  current_activity_bq: 3.7e13, initial_activity_kci: 1, current_activity_kci: 1,
  remaining_fraction: 1, initial_reading_date: new Date().toISOString().slice(0, 10),
  lower_point_mm: [0, 0, 0], upper_point_mm: [0, 0, 451.75],
  center_point_mm: [0, 0, 225.875], active_length_mm: 451.75, status: 'configured',
};

function formatKCi(value: number) {
  return `${value.toLocaleString(uiLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kCi`;
}

function SourceForm({ source, onClose, onSaved }: { source: GammaSource; onClose: () => void; onSaved: () => void }) {
  const isNew = source.index < 0;
  const [identifier, setIdentifier] = useState(source.id);
  const [lower, setLower] = useState(source.lower_point_mm.join(', '));
  const [upper, setUpper] = useState(source.upper_point_mm.join(', '));
  const [activity, setActivity] = useState(String(source.initial_activity_kci));
  const [date, setDate] = useState(source.initial_reading_date);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const vector = (value: string) => value.split(',').map((item) => Number(item.trim()));

  const save = async () => {
    const lowerPoint = vector(lower);
    const upperPoint = vector(upper);
    if (lowerPoint.length !== 3 || upperPoint.length !== 3 || [...lowerPoint, ...upperPoint].some((value) => !Number.isFinite(value))
      || [...lower.split(','), ...upper.split(',')].some((value) => !value.trim())) {
      setError('Las coordenadas deben contener exactamente tres valores numéricos.');
      return;
    }
    if (!identifier.trim()) {
      setError('El identificador de la fuente es obligatorio.');
      return;
    }
    if (lowerPoint.every((value, index) => value === upperPoint[index])) {
      setError('Los puntos de la fuente deben ser distintos; la longitud activa no puede ser cero.');
      return;
    }
    if (!Number.isFinite(Number(activity)) || Number(activity) <= 0) {
      setError('La actividad inicial debe ser un número positivo.');
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      setError('Introduce una fecha de lectura válida.');
      return;
    }
    setSaving(true); setError('');
    try {
      await apiJson(isNew ? '/api/config/sources' : `/api/config/sources/${source.index}`, isNew ? 'POST' : 'PUT', {
        identifier: identifier.trim(),
        lower_point: lowerPoint,
        upper_point: upperPoint,
        initial_activity_kci: Number(activity),
        initial_reading_date: date,
      });
      onSaved();
    } catch (failure) {
      let message = 'No fue posible guardar la fuente. Revisa los valores.';
      if (failure instanceof Error) {
        try {
          const { detail } = JSON.parse(failure.message) as { detail?: string | { msg: string }[] };
          if (typeof detail === 'string') message = detail;
          else if (Array.isArray(detail)) message = detail.map(item => item.msg.replace(/^Value error, /, '')).join(' ');
        } catch { /* Keep the readable fallback for a connection failure. */ }
      }
      setError(message); setSaving(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`¿Retirar ${source.id} de la configuración activa?`)) return;
    setSaving(true);
    try { await apiJson(`/api/config/sources/${source.index}`, 'DELETE'); onSaved(); }
    catch { setError('No fue posible retirar la fuente.'); setSaving(false); }
  };

  return (
    <DialogFrame onClose={onClose} label={isNew ? 'Añadir fuente' : `Editar ${source.id}`}>
      <section className="editor-modal">
        <header><div><span className="section-label">Inventario versionado</span><h2>{isNew ? 'Añadir fuente' : `Editar ${source.id}`}</h2></div><button className="icon-button" onClick={onClose} aria-label="Cerrar"><X size={18} /></button></header>
        <div className="editor-grid">
          <label className="field full"><span>Identificador de la fuente</span><input value={identifier} maxLength={80} onChange={(event) => setIdentifier(event.target.value)} placeholder="Ej. SRC-113" autoFocus /></label>
          <label className="field full"><span>Punto inferior [x, y, z] mm</span><input value={lower} onChange={(event) => setLower(event.target.value)} /></label>
          <label className="field full"><span>Punto superior [x, y, z] mm</span><input value={upper} onChange={(event) => setUpper(event.target.value)} /></label>
          <label className="field"><span>Actividad inicial</span><div className="input-suffix"><input type="number" min="0.0001" step="0.01" value={activity} onChange={(event) => setActivity(event.target.value)} /><i>kCi</i></div></label>
          <label className="field"><span>Fecha de lectura</span><input type="date" value={date} onInput={(event) => setDate(event.currentTarget.value)} onChange={(event) => setDate(event.target.value)} /></label>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <footer>{!isNew && <button className="danger-button" disabled={saving} onClick={remove}><Trash2 size={15} /> Retirar</button>}<span /><button className="button ghost" onClick={onClose}>Cancelar</button><button className="button primary" disabled={saving} onClick={save}>{saving ? 'Guardando…' : 'Guardar cambios'}</button></footer>
      </section>
    </DialogFrame>
  );
}

export default function SourcesView({ configurationRevision = 0 }: { configurationRevision?: number }) {
  const [data, setData] = useState<SourcesResponse | null>(null);
  const [selectedId, setSelectedId] = useState('SRC-001');
  const [query, setQuery] = useState('');
  const [editor, setEditor] = useState<GammaSource | null>(null);
  const [error, setError] = useState('');
  const [rackZoom, setRackZoom] = useState(1);
  const [rackBaseWidth, setRackBaseWidth] = useState<number | null>(null);
  const rackCanvasRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    try { setData(await apiGet<SourcesResponse>('/api/config/sources')); setError(''); }
    catch { setError('No fue posible cargar el inventario desde la API.'); }
  };
  useEffect(() => {
    apiGet<SourcesResponse>('/api/config/sources')
      .then((response) => { setData(response); setError(''); })
      .catch(() => setError('No fue posible cargar el inventario desde la API.'));
  }, [configurationRevision]);

  const sources = useMemo(() => data?.sources ?? [], [data]);
  const nextIdentifier = useMemo(() => {
    const configured = new Set(sources.map((source) => source.id.toLocaleLowerCase()));
    let sequence = sources.length + 1;
    while (configured.has(`src-${String(sequence).padStart(3, '0')}`)) sequence += 1;
    return `SRC-${String(sequence).padStart(3, '0')}`;
  }, [sources]);
  const selected = sources.find((source) => source.id === selectedId) ?? sources[0];
  const filtered = sources.filter((source) => source.id.toLowerCase().includes(query.toLowerCase()));
  const xs = sources.flatMap((source) => [source.lower_point_mm[0], source.upper_point_mm[0]]);
  const zs = sources.flatMap((source) => [source.lower_point_mm[2], source.upper_point_mm[2]]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const zMin = Math.min(...zs), zMax = Math.max(...zs);
  const xRange = Math.max(1, xMax - xMin);
  const zRange = Math.max(1, zMax - zMin);
  const plotPadding = 5;
  const usablePlot = 100 - plotPadding * 2;
  const position = (source: GammaSource) => ({
    left: `${plotPadding + ((source.center_point_mm[0] - xMin) / xRange) * usablePlot}%`,
    bottom: `${plotPadding + ((source.lower_point_mm[2] - zMin) / zRange) * usablePlot}%`,
    height: `${(source.active_length_mm / zRange) * usablePlot}%`,
  });
  useEffect(() => {
    const canvas = rackCanvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => {
      const styles = window.getComputedStyle(canvas);
      const availableWidth = canvas.clientWidth - parseFloat(styles.paddingLeft) - parseFloat(styles.paddingRight);
      const availableHeight = canvas.clientHeight - parseFloat(styles.paddingTop) - parseFloat(styles.paddingBottom);
      const fittedWidth = Math.min(availableWidth, availableHeight * (xRange / zRange));
      setRackBaseWidth(Math.max(1, fittedWidth));
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [xRange, zRange]);
  const rackStyle = {
    width: rackBaseWidth === null ? '100%' : `${rackBaseWidth * rackZoom}px`,
    aspectRatio: `${xRange} / ${zRange}`,
  } as CSSProperties;

  if (error) return <div className="empty-state"><Activity size={28} /><h2>Inventario no disponible</h2><p>{error}</p><button className="button primary" onClick={load}>Reintentar</button></div>;
  if (!data) return <div className="loading-panel">Cargando inventario de fuentes…</div>;
  if (!selected) return <div className="view-stack">
    <section className="surface empty-state"><Atom size={28}/><h2>No hay fuentes configuradas</h2>
      <p>Añade una fuente cilíndrica para definir el inventario del irradiador. Los casos guardados conservan su propio inventario.</p>
      <button className="button primary" onClick={() => setEditor({ ...EMPTY_SOURCE, id: nextIdentifier })}><Plus size={16}/> Añadir fuente</button>
    </section>
    {editor && <SourceForm source={editor} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); void load(); }}/>}</div>;

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div><span className="section-label">Inventario de referencia · {data.reference_date}</span><h2>Inventario de fuentes</h2><p>Selecciona una cápsula para consultar su geometría y actividad a la fecha indicada.</p></div>
        <button className="button primary" onClick={() => setEditor({ ...EMPTY_SOURCE, id: nextIdentifier })}><Plus size={16} /> Añadir fuente</button>
      </section>
      <section className="metric-grid source-metrics">
        <article><span>Fuentes configuradas</span><strong>{data.count}</strong><small>Co-60</small></article>
        <article><span>Actividad total actual</span><strong>{formatKCi(data.total_current_activity_kci)}</strong><small>Calculada a la fecha</small></article>
        <article><span>Vida media</span><strong>{data.half_life_days.toLocaleString(uiLocale())} días</strong><small>Parámetro configurado</small></article>
      </section>
      <div className="source-layout">
        <section className="surface rack-surface">
          <header className="surface-header"><div><span className="section-label">Plano X–Z · escala física</span><h3>Rack completo</h3></div><div className="rack-toolbar"><div className="rack-legend"><i /> Fuente activa <b /> Seleccionada</div><div className="zoom-control"><button disabled={rackZoom <= 1} onClick={() => setRackZoom((zoom) => Math.max(1, zoom - .25))} aria-label="Reducir ampliación del rack"><Minus size={13}/></button><input aria-label="Ampliación del rack" type="range" min="1" max="5" step=".25" value={rackZoom} onChange={(event) => setRackZoom(Number(event.target.value))}/><button disabled={rackZoom >= 5} onClick={() => setRackZoom((zoom) => Math.min(5, zoom + .25))} aria-label="Ampliar rack"><Plus size={13}/></button><span>{rackZoom.toFixed(2).replace(/\.00$/, '')}×</span></div></div></header>
          <div className="rack-canvas" ref={rackCanvasRef}>
            <div className="rack-structure" style={rackStyle}><span className="axis-label axis-x">Eje X · {xRange.toFixed(1)} mm</span><span className="axis-label axis-z">Eje Z · {zRange.toFixed(1)} mm</span>
              {sources.map((source) => <button key={source.id} className={source.id === selected.id ? 'rack-source selected' : 'rack-source'} style={position(source)} onClick={() => setSelectedId(source.id)} title={`${source.id} · ${formatKCi(source.current_activity_kci)}`} aria-label={`${source.id}, ${formatKCi(source.current_activity_kci)}`}><i /><span>{source.id}</span></button>)}
            </div>
          </div>
        </section>
        <aside className="surface source-detail">
          <header><div className="source-id-badge" aria-hidden="true">{sources.findIndex((item) => item.id === selected.id) + 1}</div><div><span className="section-label">Fuente seleccionada</span><h3>{selected.id}</h3></div><button className="icon-button" onClick={() => setEditor(selected)} aria-label="Editar fuente"><Edit3 size={16} /></button></header>
          <div className="activity-focus"><span>Actividad actual</span><strong>{formatKCi(selected.current_activity_kci)}</strong><div className="activity-track"><i style={{ width: `${selected.remaining_fraction * 100}%` }} /></div><small>{(selected.remaining_fraction * 100).toFixed(1)}% de {formatKCi(selected.initial_activity_kci)}</small></div>
          <dl className="detail-list"><div><dt><CalendarDays size={14} /> Lectura inicial</dt><dd>{selected.initial_reading_date}</dd></div>{selected.diameter_mm !== undefined && <div><dt>Diámetro de la fuente cilíndrica</dt><dd>{selected.diameter_mm} mm</dd></div>}<div><dt>Longitud activa</dt><dd>{selected.active_length_mm.toFixed(2)} mm</dd></div><div className="stacked"><dt>Punto inferior</dt><dd>[{selected.lower_point_mm.map((value) => value.toFixed(2)).join(', ')}] mm</dd></div><div className="stacked"><dt>Punto superior</dt><dd>[{selected.upper_point_mm.map((value) => value.toFixed(2)).join(', ')}] mm</dd></div></dl>
          <button className="button secondary full" onClick={() => setEditor(selected)}><Edit3 size={15} /> Editar parámetros</button>
        </aside>
      </div>
      <section className="surface table-surface"><header className="surface-header"><div><span className="section-label">Inventario</span><h3>Todas las fuentes</h3></div><label className="search-control"><Search size={15} /><input placeholder="Buscar por identificador" value={query} onChange={(event) => setQuery(event.target.value)} /></label></header><div className="data-table-wrap"><table><thead><tr><th>Fuente</th><th>Actividad inicial</th><th>Actividad actual</th><th>Lectura</th><th>Centro [x, y, z]</th><th /></tr></thead><tbody>{filtered.map((source) => <tr key={source.id} className={source.id === selected.id ? 'active' : ''} onClick={() => setSelectedId(source.id)}><td><strong>{source.id}</strong></td><td>{formatKCi(source.initial_activity_kci)}</td><td><strong>{formatKCi(source.current_activity_kci)}</strong></td><td>{source.initial_reading_date}</td><td className="mono">[{source.center_point_mm.map((value) => value.toFixed(1)).join(', ')}]</td><td><button className="table-action" aria-label={`Editar fuente ${source.id}`} onClick={(event) => { event.stopPropagation(); setEditor(source); }}><Edit3 size={14} /></button></td></tr>)}</tbody></table></div></section>
      {editor && <SourceForm source={editor} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); setSelectedId('SRC-001'); void load(); }} />}
    </div>
  );
}
