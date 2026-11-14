'use client';

import { useEffect, useRef, useState } from 'react';
import { ChevronRight, FilePlus2, RotateCcw, Search, Trash2 } from 'lucide-react';
import { API_URL, apiGet, apiJson } from '../../lib/api';
import type { IrradiationCase, StepMaterial } from '../../lib/types';
import { SavedLoadGeometry } from '../load/SavedLoadGeometry';
import AirDoseMethodNote, { isAirReference } from './AirDoseMethodNote';
import { methodLabels, readableError, VarianceOptions, type RouteConfiguration } from './RouteConfigurationView';

export default function RouteCasesView({ stepFile, materials, materialTags, revision, visible, selectedId, onSelect, onLoad, onConfigure, onResults, onInspect }: {
  stepFile: File | null;
  materials: StepMaterial[];
  materialTags: string[];
  revision: number;
  visible: boolean;
  selectedId: string;
  onSelect: (caseId: string) => void;
  onLoad: () => void;
  onConfigure: () => void;
  onResults: (caseId: string) => void;
  onInspect: (caseId: string) => void;
}) {
  const [configuration, setConfiguration] = useState<RouteConfiguration>();
  const [cases, setCases] = useState<IrradiationCase[]>([]);
  const [trash, setTrash] = useState<Pick<IrradiationCase, 'case_id' | 'name' | 'workflow'>[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const createRef = useRef<HTMLDetailsElement>(null);
  const [method, setMethod] = useState<RouteConfiguration['dose_tally']>();
  const [occupancy, setOccupancy] = useState<RouteConfiguration['occupancy']>();
  const [variance, setVariance] = useState<RouteConfiguration['variance_reduction']>();
  const configuredMethod = configuration?.dose_tally ?? 'heating';
  const chosenMethod = method ?? (materialTags.includes('airload') && configuredMethod === 'heating' ? 'flux' : configuredMethod);
  const chosenOccupancy = occupancy ?? configuration?.occupancy ?? 'isolated';
  const chosenVariance = variance ?? configuration?.variance_reduction ?? 'source_mixture';

  useEffect(() => {
    if (!visible) return;
    let active = true;
    Promise.all([
      apiGet<RouteConfiguration>('/api/route/configuration'),
      apiGet<{ cases: IrradiationCase[] }>('/api/cases'),
      apiGet<{ cases: IrradiationCase[] }>('/api/cases-trash'),
    ]).then(([nextConfiguration, nextCases, nextTrash]) => {
      if (!active) return;
      setConfiguration(nextConfiguration);
      setCases(nextCases.cases.filter((item) => item.workflow === 'suspended_litters' && !item.analysis_method));
      setTrash(nextTrash.cases.filter((item) => item.workflow === 'suspended_litters'));
    }).catch((error) => { if (active) setMessage(readableError(error)); });
    return () => { active = false; };
  }, [revision, visible]);

  async function moveCase(item: Pick<IrradiationCase, 'case_id' | 'name' | 'workflow'>, restore = false) {
    setBusy(true); setMessage('');
    try {
      if (restore) {
        const restored = await apiJson<IrradiationCase>(`/api/cases-trash/${item.case_id}/restore`, 'POST');
        setCases(current => [restored, ...current]);
        setTrash(current => current.filter(saved => saved.case_id !== item.case_id));
        onSelect(item.case_id);
        setMessage(`Caso restaurado: ${item.name}`);
      } else {
        const response = await apiJson<{ cases: IrradiationCase[] }>(`/api/cases/${item.case_id}/trash`, 'POST');
        const remaining = response.cases.filter(saved => saved.workflow === 'suspended_litters' && !saved.analysis_method);
        setCases(remaining);
        setTrash(current => [item, ...current]);
        onSelect(remaining[0]?.case_id ?? '');
        setMessage(`Caso enviado a la papelera: ${item.name}. Puedes recuperarlo con Restaurar.`);
      }
    } catch (error) { setMessage(readableError(error)); }
    finally { setBusy(false); }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!stepFile || !configuration?.positions.length) return;
    setBusy(true);
    setMessage('');
    try {
      const form = new FormData();
      form.append('step', stepFile);
      form.append('name', name.trim());
      form.append('description', description.trim());
      form.append('materials_json', JSON.stringify(materials));
      form.append('dose_tally', chosenMethod);
      form.append('occupancy', chosenOccupancy);
      form.append('variance_reduction', chosenVariance);
      const response = await fetch(`${API_URL}/api/route/cases`, { method: 'POST', body: form });
      if (!response.ok) throw new Error(await response.text());
      const saved = await response.json() as IrradiationCase;
      setCases((current) => [saved, ...current.filter((item) => item.case_id !== saved.case_id)]);
      setName('');
      setDescription('');
      onSelect(saved.case_id);
      onResults(saved.case_id);
    } catch (error) {
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  const selected = cases.find((item) => item.case_id === selectedId) ?? cases[0];
  const materialsReady = materialTags.some((tag) => tag !== 'air')
    && materialTags.every((tag) => materials.some((item) => item.name === tag));
  const canCreate = !!stepFile && materialsReady && !!configuration?.positions.length;
  const matchingCases = cases.filter((item) => `${item.name} ${item.description ?? ''}`.toLocaleLowerCase('es').includes(query.trim().toLocaleLowerCase('es')));
  return (
    <div className="view-stack route-cases-page">
      <section className="hero-row"><div><span className="section-label">Operación · Casos</span><h2>Casos de irradiación</h2><p>Selecciona un caso para verificar su geometría o abrir sus resultados.</p></div><button type="button" className="button primary" onClick={() => { if (createRef.current) { createRef.current.open = true; createRef.current.querySelector('input')?.focus({ preventScroll: true }); createRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }}><FilePlus2 size={15}/>Nuevo caso</button></section>
      {message && <p className="inline-notice" role="status">{message}</p>}
      <div className="cases-layout">
        <details className="surface new-case-panel" ref={createRef} open={stepFile ? true : undefined}>
          <summary><FilePlus2 size={16}/><div><strong>Crear un caso de irradiación</strong><span>{stepFile ? stepFile.name : 'Carga primero un STEP y asigna sus materiales en Carga CAD.'}</span></div><ChevronRight size={15}/></summary>
          <form className="form-stack" onSubmit={create}>
            <label className="field"><span>Nombre del caso</span><input required maxLength={120} value={name} onChange={(event) => setName(event.target.value)}/></label>
            <label className="field"><span>Descripción</span><textarea rows={3} className="prose-input" maxLength={2000} value={description} onChange={(event) => setDescription(event.target.value)}/></label>
            <div className="attached-file"><div><span>Geometría de la carga</span><strong>{stepFile?.name ?? 'Selecciona un STEP y configura sus materiales'}</strong></div><button type="button" onClick={onLoad}>Carga CAD</button></div>
            <div className="attached-file"><div><span>Recorrido</span><strong>{configuration?.positions.length ?? 0} posiciones configuradas</strong></div><button type="button" onClick={onConfigure}>Parámetros</button></div>
            <label className="field"><span>Método</span><select value={chosenMethod} onChange={(event) => { setMethod(event.target.value as RouteConfiguration['dose_tally']); setVariance(event.target.value === 'phits' ? 'analog' : 'source_mixture'); }}>{Object.entries(methodLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            {isAirReference(materialTags) && <AirDoseMethodNote/>}
            <label className="field"><span>Ocupación</span><select value={chosenOccupancy} onChange={(event) => setOccupancy(event.target.value as RouteConfiguration['occupancy'])}><option value="isolated">Una sola carga</option><option value="occupied">Ocupación completa · atenuación por cargas vecinas</option></select></label>
            <label className="field"><span>Reducción de varianza</span><select value={chosenVariance} onChange={(event) => setVariance(event.target.value as RouteConfiguration['variance_reduction'])}><VarianceOptions method={chosenMethod}/></select></label>
            <button className="button primary" disabled={busy || !canCreate || !name.trim()}>Crear caso e ir a Resultados</button>
          </form>
        </details>
        <section className="surface">
          <header className="surface-header"><div><h3>Casos de recorrido guardados</h3><p>{matchingCases.length} de {cases.length} casos</p></div><label className="case-search"><Search size={15}/><input type="search" aria-label="Buscar casos guardados" placeholder="Buscar por nombre o descripción" value={query} onChange={(event) => setQuery(event.target.value)}/></label></header>
          {!cases.length && <div className="empty-list">Todavía no hay casos de recorrido.</div>}
          {cases.length > 0 && !matchingCases.length && <div className="empty-list">No hay coincidencias. <button type="button" className="button ghost" onClick={() => setQuery('')}>Limpiar búsqueda</button></div>}
          <div className="case-rows">{matchingCases.map((item) => (
            <div className={item.case_id === selected?.case_id ? 'case-row active' : 'case-row'} key={item.case_id}>
              <button className="case-row-select" aria-pressed={item.case_id === selected?.case_id} disabled={busy} onClick={() => onSelect(item.case_id)}>
                <div><strong>{item.name}</strong><small>{item.step_input?.original_name ?? 'Sin STEP asociado'}{item.step_material_tags?.length ? ` · ${item.step_material_tags.join(', ')}` : ''}</small></div><ChevronRight size={16}/>
              </button>
            </div>
          ))}</div>
        </section>
      </div>
      {visible && selected && <SavedLoadGeometry key={selected.case_id} item={selected} actions={<>
        <button className="button secondary" disabled={busy} onClick={() => void moveCase(selected)}><Trash2 size={15}/>Enviar a la papelera</button>
        <button className="button secondary" disabled={busy || !selected.step_input} onClick={() => onInspect(selected.case_id)}>Abrir en Carga CAD</button>
        <button className="button primary" disabled={busy || !selected.step_input} onClick={() => onResults(selected.case_id)}>Abrir en Resultados</button>
      </>}/>}
      <details className="surface case-trash">
        <summary><Trash2 size={15}/>Papelera · {trash.length} casos</summary>
        <p className="muted">Los casos retirados no aparecen en Carga CAD ni Resultados. Se conservan sus archivos y ocupan espacio hasta su eliminación definitiva.</p>
        {!trash.length && <p className="muted">La papelera está vacía.</p>}
        {trash.map(item => <div className="attached-file" key={item.case_id}><strong>{item.name}</strong><button className="button secondary" disabled={busy} onClick={() => void moveCase(item, true)} aria-label={`Restaurar ${item.name}`}><RotateCcw size={14}/>Restaurar</button></div>)}
      </details>
    </div>
  );
}
