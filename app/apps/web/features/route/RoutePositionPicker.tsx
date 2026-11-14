'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useState } from 'react';
import { Plus, X } from 'lucide-react';
import type { RoutePosition } from './RouteConfigurationView';

export default function RoutePositionPicker({ positions, selected, onChange, disabled = false, yawOffset = 0 }: {
  positions: RoutePosition[]; selected: string[]; onChange: (names: string[]) => void; disabled?: boolean; yawOffset?: number;
}) {
  const [candidate, setCandidate] = useState('');
  const available = positions.filter((point) => !selected.includes(point.name));
  const choice = available.find((point) => point.name === candidate);
  const ordered = positions.filter((point) => selected.includes(point.name));
  const angle = (point: RoutePosition) => (point.yaw_degrees + yawOffset).toLocaleString(uiLocale(), { maximumFractionDigits: 3 });
  return <div className="route-position-picker">
    <div className="route-position-add">
      <label className="field"><span>Posición del recorrido</span><select value={choice?.name ?? ''} disabled={disabled || !available.length} onChange={(event) => setCandidate(event.target.value)}>
        <option value="">{available.length ? 'Selecciona una posición…' : 'Todas las posiciones están añadidas'}</option>
        {available.map((point) => <option key={point.name} value={point.name}>{point.name} · giro Z {angle(point)}°</option>)}
      </select></label>
      <button type="button" className="button secondary" disabled={disabled || !choice} onClick={() => {
        if (!choice) return;
        onChange(positions.filter((point) => point.name === choice.name || selected.includes(point.name)).map((point) => point.name));
        setCandidate('');
      }}><Plus size={14}/>Añadir posición</button>
    </div>
    {ordered.length ? <ul aria-label="Posiciones seleccionadas" className="route-position-chips">
      {ordered.map((point) => <li key={point.name}><strong>{point.name}</strong><span>{angle(point)}°</span><button type="button" className="table-action" aria-label={`Quitar ${point.name}`} disabled={disabled} onClick={() => onChange(selected.filter((name) => name !== point.name))}><X size={13}/></button></li>)}
    </ul> : <small>Añade al menos una posición. Se calcularán en el orden del recorrido.</small>}
  </div>;
}
