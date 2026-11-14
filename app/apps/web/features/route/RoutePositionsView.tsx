'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useEffect, useState } from 'react';
import RouteScene from './RouteScene';
import { apiGet, apiJson } from '../../lib/api';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import type { SourcesResponse } from '../../lib/types';
import { NOMINAL_LOAD_DIMENSIONS_MM } from '../load/cadPreview';
import { readableError, type RouteConfiguration } from './RouteConfigurationView';

type PositionDraft = { id: string; name: string; coordinates: string; yaw: string };

export default function RoutePositionsView({ revision, size, onSaved }: { revision: number; size?: [number, number, number]; onSaved: () => void }) {
  const [route, setRoute] = useState<RouteConfiguration>();
  const [sources, setSources] = useState<SourcesResponse>();
  const [selected, setSelected] = useState('');
  const [message, setMessage] = useState('');
  const [draft, setDraft] = useState<PositionDraft[]>();
  const [reference, setReference] = useState<RouteConfiguration['position_reference']>('bottom_center');
  const [offset, setOffset] = useState('0');
  const [saving, setSaving] = useState(false);
  const dimensions = size ?? NOMINAL_LOAD_DIMENSIONS_MM;
  useEffect(() => {
    let active = true;
    Promise.all([apiGet<RouteConfiguration>('/api/route/configuration'), apiGet<SourcesResponse>('/api/config/sources')])
      .then(([configuration, inventory]) => {
        if (!active) return;
        setRoute(configuration);
        setSources(inventory);
        setSelected((current) => configuration.positions.some((item) => item.name === current) ? current : configuration.positions[0]?.name ?? '');
      }).catch((error) => { if (active) setMessage(readableError(error)); });
    return () => { active = false; };
  }, [revision]);
  function edit() {
    if (!route) return;
    setMessage('');
    setReference(route.position_reference);
    setOffset(String(route.cad_offset_degrees));
    setDraft(route.positions.map((position) => ({ id: position.name, name: position.name, coordinates: position.center_mm.join(', '), yaw: String(position.yaw_degrees) })));
  }
  function update(index: number, changes: Partial<PositionDraft>) {
    setDraft((items) => items?.map((point, i) => i === index ? { ...point, ...changes } : point));
  }
  function move(index: number, direction: number) {
    setDraft((items) => {
      if (!items) return items;
      const next = [...items];
      [next[index], next[index + direction]] = [next[index + direction], next[index]];
      return next;
    });
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft?.length) return;
    setSaving(true); setMessage('');
    try {
      const positions = draft.map((point, index) => {
        const parts = point.coordinates.split(',').map((value) => value.trim());
        if (parts.length !== 3 || parts.some((value) => !value || !Number.isFinite(Number(value)))) {
          throw new Error(`Posición ${index + 1}. Introduce X, Y y Z en milímetros, separados por comas.`);
        }
        return { name: point.name.trim(), center_mm: parts.map(Number) as [number, number, number], yaw_degrees: Number(point.yaw) };
      });
      const current = await apiGet<RouteConfiguration>('/api/route/configuration');
      const previousSelection = current.calculation_positions ?? [];
      const selectedNames = draft.flatMap((point, index) => previousSelection.includes(point.id) ? [positions[index].name] : []);
      const updated = await apiJson<RouteConfiguration>('/api/route/configuration', 'PUT', {
        ...current, positions, position_reference: reference, cad_offset_degrees: Number(offset),
        calculation_positions: selectedNames.length ? selectedNames : null,
      });
      setRoute(updated); setDraft(undefined); onSaved();
      setMessage(previousSelection.length && !selectedNames.length
        ? 'Recorrido guardado. Al retirar las posiciones seleccionadas, el alcance vuelve a Recorrido completo.'
        : 'Recorrido guardado para los casos nuevos.');
    } catch (error) { setMessage(readableError(error)); }
    finally { setSaving(false); }
  }
  const rotationChanges = route?.positions.flatMap((position, index) => {
    const previous = route.positions[index - 1];
    const delta = previous ? ((position.yaw_degrees - previous.yaw_degrees) % 360 + 360) % 360 : 0;
    return delta !== 0 ? [`${previous.name} → ${position.name}`] : [];
  }) ?? [];
  const format = (value: number) => value.toLocaleString(uiLocale(), { maximumFractionDigits: 1 });
  return <div className="view-stack route-positions-page">
    <section className="hero-row"><div><span className="section-label">Geometría del recorrido</span><h2>Posiciones, distancias y fuentes</h2><p>Selecciona una posición para resaltarla y consulta las distancias entre paradas.</p></div>
      <button type="button" className="button secondary" disabled={!route || !!draft} onClick={edit}>Editar recorrido</button>
      <label className="field"><span>Resaltar posición</span><select value={selected} onChange={(event) => setSelected(event.target.value)}>{route?.positions.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
    </section>
    <div className="position-geometry-summary"><span><strong>{route?.positions.length ?? 0}</strong> posiciones</span><span><strong>{sources?.count ?? 0}</strong> fuentes</span><span><strong>{dimensions.join(' × ')}</strong> mm · X × Y × Z</span><span>Envolvente {size ? 'del STEP' : 'de referencia'}</span></div>
    {message && <p className="inline-notice" role="status">{message}</p>}
    {draft && <form className="surface route-position-edit" onSubmit={save}>
      <header className="surface-header"><div><h3>Editar geometría y giros</h3><p>Cada parada requiere un giro absoluto alrededor de Z. Introduce 0° explícitamente si conserva la orientación de referencia.</p></div></header>
      <div className="simulation-form-grid">
        <label className="field"><span>Referencia para X, Y, Z</span><select value={reference} onChange={(event) => setReference(event.target.value as typeof reference)}><option value="bottom_center">Centro de la base</option><option value="center">Centro de la caja envolvente</option></select></label>
        <label className="field"><span>Desfase global del CAD [°]</span><input type="number" required min={-360} max={360} step="any" value={offset} onChange={(event) => setOffset(event.target.value)}/></label>
      </div>
      <div className="data-table-wrap"><table><thead><tr><th>Orden</th><th>Posición</th><th>X, Y, Z [mm]</th><th>Giro Z [°]</th><th>Acciones</th></tr></thead>
        <tbody>{draft.map((point, index) => <tr key={point.id}>
          <td>{index + 1}</td>
          <td><input aria-label={`Nombre de la posición ${index + 1}`} required maxLength={80} value={point.name} onChange={(event) => update(index, { name: event.target.value })}/></td>
          <td><input aria-label={`Coordenadas de la posición ${index + 1}`} required value={point.coordinates} onChange={(event) => update(index, { coordinates: event.target.value })}/></td>
          <td><input aria-label={`Giro de la posición ${index + 1}`} type="number" required min={-360} max={360} step="any" value={point.yaw} onChange={(event) => update(index, { yaw: event.target.value })}/></td>
          <td><div className="route-row-controls">
            <button type="button" className="table-action" aria-label={`Subir posición ${index + 1}`} disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp size={13}/></button>
            <button type="button" className="table-action" aria-label={`Bajar posición ${index + 1}`} disabled={saving || index === draft.length - 1} onClick={() => move(index, 1)}><ArrowDown size={13}/></button>
            <button type="button" className="table-action" aria-label={`Quitar posición ${index + 1}`} disabled={saving || draft.length < 2} onClick={() => setDraft(draft.filter((_, i) => i !== index))}><Trash2 size={13}/></button>
          </div></td>
        </tr>)}</tbody></table></div>
      <div className="route-position-edit-actions">
        <button type="button" className="button secondary" disabled={saving || draft.length >= 100} onClick={() => setDraft([...draft, { id: crypto.randomUUID(), name: '', coordinates: '', yaw: '' }])}><Plus size={14}/>Añadir parada al recorrido</button>
        <button type="button" className="button secondary" disabled={saving} onClick={() => setDraft(undefined)}>Cancelar edición</button>
        <button className="button primary" disabled={saving}>Guardar recorrido</button>
      </div>
    </form>}
    <section className="surface cad-surface"><RouteScene positions={route?.positions ?? []} sources={sources?.sources ?? []} size={dimensions} selected={selected} reference={route?.position_reference ?? 'center'} yawOffset={route?.cad_offset_degrees}/></section>
    <section className="surface"><header className="surface-header"><div><h3>Recorrido guardado</h3><p>La tabla muestra el giro absoluto efectivo, incluido el desfase global. Las sumas registran estas orientaciones para seguir el mismo punto físico de la carga.</p><p>Las distancias son rectas entre referencias.</p><p><strong>Cambios de orientación: </strong>{rotationChanges.length ? rotationChanges.join(' · ') : 'Sin cambios entre paradas consecutivas.'}</p></div></header>
      <div className="data-table-wrap"><table>
        <thead><tr><th>Orden</th><th>Posición</th><th>X, Y, Z [mm]</th><th>Giro Z [°]</th><th>Distancia a la anterior [mm]</th></tr></thead>
        <tbody>{route?.positions.map((item, index) => {
          const previous = route.positions[index - 1];
          const distance = previous ? Math.hypot(...item.center_mm.map((coordinate, axis) => coordinate - previous.center_mm[axis])) : null;
          return <tr key={item.name} className={item.name === selected ? 'selected-position-row' : ''}><td>{index + 1}</td><td><button className="table-action" aria-pressed={item.name === selected} onClick={() => setSelected(item.name)}>{item.name}</button></td><td>{item.center_mm.map(format).join(' · ')}</td><td>{format(item.yaw_degrees + route.cad_offset_degrees)}</td><td>{distance === null ? '—' : format(distance)}</td></tr>;
        })}</tbody>
      </table></div>
    </section>
  </div>;
}
