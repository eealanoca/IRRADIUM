'use client';

import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileBox, Pencil, Plus, Trash2, UploadCloud } from 'lucide-react';
import StepViewer, { type StepPreviewPart } from '../../components/cad/StepViewer';
import { API_URL } from '../../lib/api';
import type { Material, StepMaterial } from '../../lib/types';
import { NOMINAL_LOAD_DIMENSIONS_MM, normalizePreview, uploadCadPreview, type PreviewResponse } from './cadPreview';

type LoadExample = { id: string; name: string; description: string; materials: StepMaterial[] };

export default function LoadView({
  routeWorkflow = false,
  visible = true,
  stepFile,
  stlFile,
  stepMaterials = [],
  onStepChange,
  onStepSizeChange,
  onStlChange,
  onStepMaterialsChange,
  onMaterialTagsChange,
  onContinue,
}: {
  routeWorkflow?: boolean;
  visible?: boolean;
  stepFile: File | null;
  stlFile: File | null;
  stepMaterials?: StepMaterial[];
  onStepChange: (file: File | null) => void;
  onStepSizeChange: (size?: [number, number, number]) => void;
  onStlChange: (file: File | null) => void;
  onStepMaterialsChange: (materials: StepMaterial[]) => void;
  onMaterialTagsChange: (tags: string[]) => void;
  onContinue: () => void;
}) {
  const [parts, setParts] = useState<StepPreviewPart[]>([]);
  const [materialTags, setMaterialTags] = useState<string[]>([]);
  const [airMaterial, setAirMaterial] = useState<StepMaterial | null>(null);
  const [editingName, setEditingName] = useState('');
  const [density, setDensity] = useState('');
  const [elements, setElements] = useState('');
  const [fractionType, setFractionType] = useState<'atomic' | 'weight'>('weight');
  const [materialError, setMaterialError] = useState('');
  const [state, setState] = useState<'default' | 'loading' | 'ready' | 'error'>('default');
  const stepInput = useRef<HTMLInputElement>(null);
  const [examples, setExamples] = useState<LoadExample[]>([]);
  const [example, setExample] = useState('eps');
  const [dimensions, setDimensions] = useState<number[]>(NOMINAL_LOAD_DIMENSIONS_MM);
  const safeMaterials = Array.isArray(stepMaterials) ? stepMaterials : [];
  const safeMaterialTags = Array.isArray(materialTags) ? materialTags : [];

  const applyPreview = (response: PreviewResponse, resetMaterials: boolean) => {
    const preview = normalizePreview(response);
    setParts(preview.parts);
    setMaterialTags(preview.tags);
    onMaterialTagsChange(preview.tags);
    if (resetMaterials) {
      onStepMaterialsChange(
        airMaterial && preview.tags.includes('air') ? [airMaterial] : [],
      );
      setEditingName('');
      setDensity('');
      setElements('');
      setFractionType('weight');
    }
  };

  const loadDefault = async () => {
    const response = await fetch(`${API_URL}/api/assets/load/preview/components`);
    if (!response.ok) throw new Error();
    applyPreview(await response.json() as PreviewResponse, true);
  };

  useEffect(() => {
    if (routeWorkflow) {
      fetch(`${API_URL}/api/route/examples`).then(async (response) => {
        if (!response.ok) throw new Error();
        setExamples((await response.json() as { examples: LoadExample[] }).examples.filter((item) => item.id !== 'airload'));
      }).catch(() => setState('error'));
      return;
    }
    Promise.all([
      fetch(`${API_URL}/api/assets/load/preview/components`),
      fetch(`${API_URL}/api/config/materials`),
    ])
      .then(async ([previewResponse, materialsResponse]) => {
        if (!previewResponse.ok || !materialsResponse.ok) throw new Error();
        const preview = await previewResponse.json() as PreviewResponse;
        const configured = await materialsResponse.json() as { materials: Material[] };
        const air = (configured.materials ?? []).find((item) => item.name.toLowerCase() === 'air');
        const normalizedAir = air
          ? { name: 'air', density: air.density, elements: air.elements, fraction_type: air.fraction_type ?? 'weight' as const }
          : null;
        setAirMaterial(normalizedAir);
        const normalizedPreview = normalizePreview(preview);
        setParts(normalizedPreview.parts);
        setMaterialTags(normalizedPreview.tags);
        onMaterialTagsChange(normalizedPreview.tags);
        onStepMaterialsChange(normalizedAir && normalizedPreview.tags.includes('air') ? [normalizedAir] : []);
        setEditingName('');
      })
      .catch(() => setState('error'));
  // La carga inicial debe ejecutarse una sola vez; los setters pertenecen al estado raíz.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectFile = async (file: File | null, presetMaterials?: StepMaterial[]) => {
    onStepChange(file);
    onStepSizeChange();
    if (routeWorkflow) {
      onStepMaterialsChange([]);
      onMaterialTagsChange([]);
    }
    if (!file) {
      setState('default');
      if (routeWorkflow) { setParts([]); setMaterialTags([]); }
      else await loadDefault();
      return;
    }
    setState('loading');
    try {
      const preview = await uploadCadPreview(file);
      applyPreview(preview, true);
      onStepSizeChange(preview.dimensions_mm ?? undefined);
      if (presetMaterials) onStepMaterialsChange(presetMaterials);
      setState('ready');
    } catch { setState('error'); }
  };

  async function loadExample() {
    const selected = examples.find((item) => item.id === example);
    if (!selected) return;
    setState('loading');
    const [x, y, z] = dimensions;
    try {
      const response = await fetch(`${API_URL}/api/route/examples/${selected.id}?x=${x}&y=${y}&z=${z}`);
      if (!response.ok) throw new Error();
      await selectFile(new File([await response.blob()], `${selected.id}-${x}-${y}-${z}.step`), selected.materials);
    } catch { setState('error'); }
  }

  const editMaterial = (material: StepMaterial) => {
    setEditingName(material.name);
    setDensity(String(material.density));
    setElements(JSON.stringify(material.elements, null, 2));
    setFractionType(material.fraction_type ?? 'weight');
    setMaterialError('');
  };

  const saveMaterial = () => {
    try {
      if (!editingName || editingName === 'air') throw new Error('Selecciona un material editable.');
      const parsedDensity = Number(density);
      const parsedElements = JSON.parse(elements) as Record<string, number>;
      if (!Number.isFinite(parsedDensity) || parsedDensity <= 0) throw new Error('La densidad debe ser mayor que cero.');
      if (!parsedElements || Array.isArray(parsedElements) || Object.keys(parsedElements).length === 0) throw new Error('Añade al menos un elemento.');
      if (Object.entries(parsedElements).some(([symbol, fraction]) => !symbol.trim() || !Number.isFinite(fraction) || fraction <= 0)) throw new Error('Las fracciones elementales deben ser positivas.');
      const material = { name: editingName, density: parsedDensity, elements: parsedElements, fraction_type: fractionType };
      onStepMaterialsChange([...safeMaterials.filter((item) => item.name !== editingName), material]);
      setEditingName('');
      setDensity('');
      setElements('');
      setMaterialError('');
    } catch (error) {
      setMaterialError(error instanceof Error ? error.message : 'Material inválido.');
    }
  };

  const removeMaterial = (name: string) => {
    if (name === 'air') return;
    onStepMaterialsChange(safeMaterials.filter((item) => item.name !== name));
    setEditingName('');
    setDensity('');
    setElements('');
    setFractionType('weight');
    setMaterialError('');
  };

  const configuredNames = new Set(safeMaterials.map((item) => item.name));
  const missingMaterials = safeMaterialTags.filter((tag) => !configuredNames.has(tag));
  const readyForCase = (!routeWorkflow || (!!stepFile && safeMaterialTags.some((tag) => tag !== 'air'))) && state !== 'loading' && state !== 'error' && safeMaterialTags.length > 0 && missingMaterials.length === 0;

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div><span className="section-label">Entradas geométricas</span><h2>Modelo tridimensional de la carga</h2><p>{routeWorkflow ? 'Selecciona el STEP del contenido de la litera y configura sus materiales.' : 'El STEP se utiliza en Monte Carlo y el STL independiente en el método determinista.'}</p></div>
        <button className="button primary" disabled={!readyForCase} onClick={onContinue}>Usar en un caso</button>
      </section>
      <div className="load-layout">
        <section className="surface cad-surface">
          <header className="surface-header"><div><span className="section-label">Visor interactivo</span><h3>{stepFile?.name ?? 'Sin geometría seleccionada'}</h3></div><span className={`status-chip${state === 'ready' ? ' success' : ''}`}>{state === 'ready' && <CheckCircle2 size={14}/>}{stepFile ? 'STEP' : 'Pendiente'}</span></header>
          {visible && (parts.length ? <StepViewer parts={parts}/> : <div className="cad-empty-state"><FileBox size={42}/><h3>{state === 'loading' ? 'Preparando la geometría…' : 'Empieza con la geometría de la carga'}</h3><p>Carga un archivo STEP para inspeccionar sus cuerpos y asignar sus materiales.</p><button type="button" className="button primary" disabled={state === 'loading'} onClick={() => stepInput.current?.click()}>Seleccionar STEP</button><small>También puedes generar una carga de ejemplo desde el panel lateral.</small></div>)}
        </section>
        <aside className="surface load-sidebar">
          <header><div className="load-file-icon"><FileBox size={22} /></div><div><span className="section-label">Archivos de carga</span><h3>Geometrías de simulación</h3></div></header>
          <label className="step-dropzone">
            <input ref={stepInput} aria-label="Archivo STEP" type="file" accept=".step,.stp" onChange={(event) => void selectFile(event.target.files?.[0] ?? null)} />
            <UploadCloud size={27} />
            <strong>{state === 'loading' ? 'Procesando geometría…' : stepFile?.name ?? 'Seleccionar archivo STEP'}</strong>
            <span>Monte Carlo · STEP o STP · hasta 500 MB</span>
          </label>
          {state === 'error' && <p className="form-error">No se pudo triangular el archivo STEP seleccionado.</p>}
          {routeWorkflow && examples.length > 0 && <div className="form-stack">
            <label className="field"><span>Cargas de ejemplo</span><select value={example} onChange={(event) => setExample(event.target.value)}>{examples.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><small>{examples.find((item) => item.id === example)?.description}</small></label>
            <div className="simulation-form-grid">{['X', 'Y', 'Z'].map((axis, index) => <label className="field" key={axis}>
              <span>{axis} local [mm]</span><input type="number" min={1} max={5000} step="any" value={dimensions[index]} onChange={(event) => setDimensions((current) => current.map((value, i) => i === index ? Number(event.target.value) : value))}/>
            </label>)}</div>
            <button className="button secondary" type="button" disabled={state === 'loading' || dimensions.some((size) => size < 1 || size > 5000)} onClick={() => void loadExample()}>Generar y cargar STEP</button>
          </div>}
          {!routeWorkflow && <label className="step-dropzone stl-dropzone">
            <input type="file" accept=".stl" onChange={(event) => onStlChange(event.target.files?.[0] ?? null)} />
            <UploadCloud size={27} />
            <strong>{stlFile?.name ?? 'Seleccionar archivo STL'}</strong>
            <span>Método determinista · STL · sin previsualización</span>
          </label>}
          <div className="load-facts"><div><span>Monte Carlo</span><strong>STEP</strong></div>{!routeWorkflow && <div><span>Método determinista</span><strong>STL · aire</strong></div>}</div>
          <div className="info-note"><strong>{routeWorkflow ? 'Contenido de la litera' : 'Separación geométrica'}</strong><p>{routeWorkflow ? 'El recorrido ubica la carga por el centro de su base o de su caja envolvente, según la referencia elegida en Parámetros.' : 'El visor muestra únicamente el STEP. El STL determinista se conserva como una entrada independiente del caso.'}</p></div>
        </aside>
      </div>
      <section className="surface step-materials-panel">
        <header className="surface-header"><div><span className="section-label">Materiales vinculados al STEP</span><h3>Composición física de la carga</h3><p>Los materiales se detectan por el prefijo de cada cuerpo del STEP. Revisa sus propiedades antes de crear el caso.</p></div><span className={`status-chip ${safeMaterialTags.length > 0 && missingMaterials.length === 0 ? 'success' : ''}`}>{safeMaterialTags.length ? `${safeMaterials.length}/${safeMaterialTags.length} configurados` : 'Pendiente de geometría'}</span></header>
        {!safeMaterialTags.length && <p className="materials-empty-note">Al cargar la geometría aparecerán aquí los materiales que debes revisar.</p>}
        <div className="step-materials-layout" hidden={!safeMaterialTags.length}>
          <div className="data-table-wrap step-material-table">
            <table>
              <thead><tr><th>Nombre exacto</th><th>Densidad</th><th>Base</th><th>Elementos / fracciones</th><th className="material-status-column">Estado</th><th>Acciones</th></tr></thead>
              <tbody>
                {safeMaterials.map((material) => {
                  const tag = material.name;
                  return <tr key={tag}><td className="mono">{tag}</td><td>{material.density} g/cm³</td><td>{(material.fraction_type ?? 'weight') === 'atomic' ? 'Atómica' : 'En peso'}</td><td className="material-composition">{Object.entries(material.elements).map(([symbol, fraction]) => `${symbol}: ${fraction}`).join(' · ')}</td><td className="material-status-column"><span className="status-chip success material-status-chip">{tag === 'air' ? 'Calibración · bloqueado' : 'Listo'}</span></td><td><div className="material-row-actions">{tag !== 'air' && <button className="table-action" onClick={() => editMaterial(material)} aria-label={`Editar ${tag}`}><Pencil size={14} /></button>}{tag !== 'air' && <button className="table-action danger" onClick={() => removeMaterial(tag)} aria-label={`Eliminar ${tag}`}><Trash2 size={14} /></button>}</div></td></tr>;
                })}
              </tbody>
            </table>
          </div>
          <div className="step-material-editor">
            <div><span className="section-label">Añadir o editar</span><h4>Definición del material</h4></div>
            <label className="field"><span>Nombre detectado</span><select value={editingName} onChange={(event) => { const selectedName = event.target.value; setEditingName(selectedName); const current = safeMaterials.find((item) => item.name === selectedName); if (current) editMaterial(current); else { setDensity(''); setElements(''); setFractionType('weight'); setMaterialError(''); } }}><option value="">Seleccionar material…</option>{safeMaterialTags.filter((tag) => tag !== 'air' && (!configuredNames.has(tag) || tag === editingName)).map((tag) => <option key={tag} value={tag}>{tag}</option>)}</select><small>La lista comienza en blanco. Selecciona un nombre detectado en el STEP y define sus propiedades.</small></label>
            <label className="field"><span>Densidad (<i className="unit-lowercase">g/cm³</i>)</span><input type="number" min="0" step="any" value={density} onChange={(event) => setDensity(event.target.value)} placeholder="Ej. 1.0" /></label>
            <label className="field"><span>Base de las fracciones</span><select value={fractionType} onChange={(event) => setFractionType(event.target.value as 'atomic' | 'weight')}><option value="weight">Fracción en peso</option><option value="atomic">Fracción atómica</option></select><small>Debe coincidir con la base usada para definir la composición química.</small></label>
            <label className="field"><span>Elementos y fracciones</span><textarea rows={6} value={elements} onChange={(event) => setElements(event.target.value)} placeholder={'{\n  "H": 0.111894,\n  "O": 0.888106\n}'} /><small>Objeto JSON: símbolo químico y fracción positiva para cada elemento.</small></label>
            {materialError && <p className="form-error compact">{materialError}</p>}
            <div className="step-material-editor-actions"><button className="button secondary" disabled={!editingName && !density && !elements} onClick={() => { setEditingName(''); setDensity(''); setElements(''); setFractionType('weight'); setMaterialError(''); }}>Limpiar</button><button className="button primary" disabled={!editingName} onClick={saveMaterial}><Plus size={15} /> {safeMaterials.some((item) => item.name === editingName) ? 'Actualizar material' : 'Añadir material'}</button></div>
          </div>
        </div>
        {missingMaterials.length > 0 && <p className="step-material-warning">Falta configurar: <strong>{missingMaterials.join(', ')}</strong>. Completa la tabla antes de usar el STEP en un caso.</p>}
      </section>
    </div>
  );
}
