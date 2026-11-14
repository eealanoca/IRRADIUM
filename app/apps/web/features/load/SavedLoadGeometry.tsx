'use client';
import { numberFormatter } from '../../lib/i18n/number';

import { useEffect, useState, type ReactNode } from 'react';
import { Download, FileBox } from 'lucide-react';
import StepViewer from '../../components/cad/StepViewer';
import { API_URL, apiGet } from '../../lib/api';
import type { IrradiationCase } from '../../lib/types';
import { readableError } from '../route/RouteConfigurationView';
import { normalizePreview, uploadCadPreview, type PreviewResponse } from './cadPreview';

const previews = new Map<string, PreviewResponse>();
const dimensionsFormat = numberFormatter({ maximumFractionDigits: 1 });

function stepUrl(item: IrradiationCase) {
  return `${API_URL}/api/cases/${encodeURIComponent(item.case_id)}/inputs/step`;
}

async function previewSavedLoad(item: IrradiationCase, signal: AbortSignal): Promise<PreviewResponse> {
  const url = `${API_URL}/api/route/cases/${encodeURIComponent(item.case_id)}/cad-preview`;
  const cached = previews.get(url);
  if (cached) return cached;

  const response = await fetch(url, { signal, cache: 'no-store' });
  let preview: PreviewResponse;
  if (response.ok) {
    preview = await response.json();
  } else {
    const body = await response.text();
    // An already running API can serve the exact case input through its existing routes.
    if (response.status !== 404 || !body.includes('"detail":"Not Found"')) throw new Error(body);
    const step = await fetch(stepUrl(item), { signal, cache: 'no-store' });
    if (!step.ok) throw new Error(await step.text());
    const file = new File([await step.blob()], item.step_input?.original_name ?? 'load.step');
    preview = await uploadCadPreview(file, signal);
  }

  if (!preview.parts?.length) throw new Error('El STEP guardado no contiene componentes visibles.');
  if (previews.size >= 12) {
    const oldest = previews.keys().next().value;
    if (oldest) previews.delete(oldest);
  }
  previews.set(url, preview);
  return preview;
}

export function SavedLoadGeometry({ item, actions }: { item: IrradiationCase; actions?: ReactNode }) {
  const [geometry, setGeometry] = useState<ReturnType<typeof normalizePreview>>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!item.step_input) return;
    const controller = new AbortController();
    setGeometry(undefined);
    setError('');
    previewSavedLoad(item, controller.signal)
      .then((preview) => { if (!controller.signal.aborted) setGeometry(normalizePreview(preview)); })
      .catch((cause) => { if (!controller.signal.aborted) setError(readableError(cause)); });
    return () => controller.abort();
  }, [item, attempt]);

  return <section className="surface saved-load-geometry" aria-label="Geometría guardada del caso">
    <header className="surface-header">
      <div><span className="section-label">STEP guardado en el caso</span><h3>{item.name}</h3><p>{item.step_input?.original_name ?? 'Sin archivo STEP'}</p></div>
      <div className="saved-load-actions">
        {item.step_input && <a className="button secondary" href={stepUrl(item)}><Download size={15}/> Descargar STEP</a>}
        {actions}
      </div>
    </header>
    {geometry ? <>
      <div className="saved-load-facts">
        {geometry.dimensions && <span><strong>Dimensiones X × Y × Z</strong> {geometry.dimensions.map((value) => dimensionsFormat.format(value)).join(' × ')} mm</span>}
        <span><strong>Componentes</strong> {geometry.parts.length}</span>
        <span><strong>Materiales</strong> {geometry.tags.join(', ')}</span>
      </div>
      <StepViewer parts={geometry.parts}/>
    </> : <div className="saved-load-placeholder" role="status">
      <FileBox size={22}/>
      <p>{error || (item.step_input ? 'Preparando la geometría guardada…' : 'Este caso todavía no tiene un STEP asociado.')}</p>
      {error && <button type="button" className="button secondary" onClick={() => setAttempt((value) => value + 1)}>Reintentar</button>}
    </div>}
  </section>;
}

export default function SavedLoadsView({ selectedId, onSelect, onResults }: {
  selectedId: string;
  onSelect: (caseId: string) => void;
  onResults: (caseId: string) => void;
}) {
  const [cases, setCases] = useState<IrradiationCase[]>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    apiGet<{ cases: IrradiationCase[] }>('/api/cases')
      .then(({ cases }) => { if (active) setCases(cases.filter((item) => item.workflow === 'suspended_litters' && !item.analysis_method && item.step_input)); })
      .catch((cause) => { if (active) setError(readableError(cause)); });
    return () => { active = false; };
  }, []);
  const selected = cases?.find((item) => item.case_id === selectedId) ?? cases?.[0];

  return <div className="view-stack">
    <section className="hero-row">
      <div><span className="section-label">Geometrías de casos</span><h2>Cargas guardadas</h2><p>Inspecciona el archivo STEP que usará el cálculo del caso seleccionado.</p></div>
      {selected && <label className="field saved-load-select" htmlFor="saved-load-case"><span>Carga guardada</span><select id="saved-load-case" aria-label="Carga guardada" value={selected.case_id} onChange={(event) => onSelect(event.target.value)}>
        {cases?.map((item) => <option key={item.case_id} value={item.case_id}>{item.name}</option>)}
      </select></label>}
    </section>
    {selected ? <SavedLoadGeometry key={selected.case_id} item={selected} actions={<button className="button primary" onClick={() => onResults(selected.case_id)}>Abrir en Resultados</button>}/>
      : <p className="inline-notice" role="status">{error || (cases ? 'Todavía no hay casos con una carga STEP guardada.' : 'Cargando casos de irradiación…')}</p>}
  </div>;
}
