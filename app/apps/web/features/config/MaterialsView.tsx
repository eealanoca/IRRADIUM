'use client';
import { uiLocale, numberFormatter } from '../../lib/i18n/number';

import DialogFrame from '../../components/ui/DialogFrame';
import AirCalibrationResults from './AirCalibrationResults';

import { useEffect, useState } from 'react';
import { Atom, BadgeCheck, Edit3, ExternalLink, ShieldCheck, X } from 'lucide-react';
import { apiGet, apiJson } from '../../lib/api';
import type { Material } from '../../lib/types';
import StepViewer from '../../components/cad/StepViewer';
import { normalizePreview, type PreviewResponse } from '../load/cadPreview';
import { readableError, type RouteConfiguration } from '../route/RouteConfigurationView';

const NIST_ALANINE_URL = 'https://physics.nist.gov/PhysRefData/XrayMassCoef/ComTab/alanine.html';

function currentLocalDate() {
  const today = new Date();
  return new Date(today.getTime() - today.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

type AirReference = {
  irradiation_date: string; radionuclide: string; source_count: number;
  photon_fluence_cm2_s: number; dose_rate_alanine_kgy_h: number;
};

function AirRouteCalibrationForm({ onResults, visible }: { onResults: (caseId: string) => void; visible: boolean }) {
  const [dimensions, setDimensions] = useState(['500', '400', '1000']);
  const [cellSize, setCellSize] = useState('1');
  const [referenceDate, setReferenceDate] = useState(currentLocalDate);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const [preview, setPreview] = useState<{ query: string; parts: ReturnType<typeof normalizePreview>['parts'] }>();
  const [previewError, setPreviewError] = useState('');
  const [x, y, z] = dimensions;
  const query = dimensions.every((value) => Number(value) >= 1 && Number(value) <= 5000)
    ? new URLSearchParams({ x, y, z }).toString() : '';

  useEffect(() => {
    if (!visible || !query) return;
    let active = true;
    const timer = setTimeout(() => {
      setPreviewError('');
      apiGet<PreviewResponse>(`/api/route/calibration/air-load?${query}`)
        .then((response) => { if (active) setPreview({ query, parts: normalizePreview(response).parts }); })
        .catch((failure) => { if (active) setPreviewError(readableError(failure)); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [query, visible]);

  async function calculate(event: React.FormEvent) {
    event.preventDefault();
    setError(''); setBusy(true);
    try {
      const study = await apiJson<{ case_id: string }>('/api/route/calibration/air-study', 'POST', {
        dimensions_mm: dimensions.map(Number), cell_size_cm: Number(cellSize),
        irradiation_date: referenceDate || undefined,
      });
      onResults(study.case_id);
    } catch (failure) { setError(readableError(failure)); }
    finally { setBusy(false); }
  }

  return <section className="surface air-reference">
    <header className="surface-header"><div><h3>Calibración determinista · carga llena de aire</h3>
      <p>Calcula el mapa de cada posición y la suma del recorrido completo. La referencia representa la respuesta de alanina en campo libre, sin atenuación ni dispersión.</p></div></header>
    <form className="form-stack" onSubmit={calculate} aria-busy={busy}>
      <div className="simulation-form-grid">
        {['X', 'Y', 'Z'].map((axis, index) => <label className="field" key={axis}><span>{axis} de la carga [mm]</span>
          <input required type="number" min={1} max={5000} step="any" value={dimensions[index]} disabled={busy} onChange={(event) => {
            const value = event.target.value;
            setDimensions((current) => current.map((coordinate, i) => i === index ? value : coordinate));
          }}/></label>)}
        <label className="field"><span>Tamaño máximo de celda [cm]</span><input required type="number" min={.01} max={100} step="any" value={cellSize} disabled={busy} onChange={(event) => setCellSize(event.target.value)}/></label>
        <label className="field"><span>Fecha de irradiación</span><input type="date" value={referenceDate} disabled={busy} onInput={(event) => setReferenceDate(event.currentTarget.value)} onChange={(event) => setReferenceDate(event.target.value)}/><small>Si queda vacía, se usa la fecha actual.</small></label>
      </div>
      <button className="button primary" disabled={busy}>{busy ? 'Calculando mapas del recorrido…' : 'Calcular recorrido y ver mapas'}</button>
    </form>
    {visible && <div className="air-calibration-preview">
      <div className="saved-load-facts"><strong>Carga de referencia · Aire seco</strong><span>{x} × {y} × {z} mm</span>
        {preview?.query === query && preview?.parts[0] && <a className="button secondary" href={preview.parts[0].url}>Descargar STL</a>}
      </div>
      {preview?.query === query ? <StepViewer parts={preview.parts}/> : <p className="inline-notice" role="status">{previewError || (query ? 'Preparando la vista de la carga…' : 'Introduce las tres dimensiones de la carga.')}</p>}
    </div>}
    {error && <p role="alert" className="form-error">{error}</p>}
  </section>;
}

function AirReferenceForm() {
  const [point, setPoint] = useState(['', '', '']);
  const [referenceDate, setReferenceDate] = useState(currentLocalDate);
  const [positions, setPositions] = useState<RouteConfiguration['positions']>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AirReference>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    apiGet<RouteConfiguration>('/api/route/configuration').then((data) => {
      if (active) setPositions(data.positions);
    }).catch(() => { /* Manual coordinates remain available. */ });
    return () => { active = false; };
  }, []);
  async function calculate(event: React.FormEvent) {
    event.preventDefault();
    setResult(undefined); setError(''); setBusy(true);
    try {
      setResult(await apiJson<AirReference>('/api/route/calibration/air-reference', 'POST', {
        point_mm: point.map(Number), irradiation_date: referenceDate || undefined,
      }));
    } catch (failure) { setError(readableError(failure)); }
    finally { setBusy(false); }
  }
  const number = numberFormatter({ maximumSignificantDigits: 5 });
  return <details className="surface air-reference">
    <summary className="route-study-summary">Referencia en un punto</summary>
    <form className="form-stack" onSubmit={calculate}>
      <div className="simulation-form-grid">
        <label className="field"><span>Tomar coordenadas de una posición</span><select disabled={busy} onChange={(event) => {
          const position = positions.find((item) => item.name === event.target.value);
          if (position) { setPoint(position.center_mm.map(String)); setResult(undefined); }
        }}><option value="">Coordenadas manuales</option>{positions.map((position) => <option key={position.name}>{position.name}</option>)}</select></label>
        <label className="field"><span>Fecha de calibración</span><input type="date" value={referenceDate} disabled={busy} onInput={(event) => { setReferenceDate(event.currentTarget.value); setResult(undefined); }} onChange={(event) => { setReferenceDate(event.target.value); setResult(undefined); }}/><small>Si queda vacía, se usa la fecha actual.</small></label>
        {['X', 'Y', 'Z'].map((axis, index) => <label className="field" key={axis}><span>{axis} del punto [mm]</span><input required type="number" min={-1e6} max={1e6} step="any" value={point[index]} disabled={busy} onChange={(event) => {
          const value = event.target.value;
          setPoint((current) => current.map((coordinate, i) => i === index ? value : coordinate));
          setResult(undefined);
        }}/></label>)}
      </div>
      <button className="button primary" disabled={busy}>{busy ? 'Calculando…' : 'Calcular referencia en aire'}</button>
    </form>
    {error && <p role="alert" className="form-error">{error}</p>}
    {result && <div className="inline-notice" role="status">
      <p><strong>{number.format(result.dose_rate_alanine_kgy_h)} kGy/h</strong> en alanina, en aire · <strong>{number.format(result.photon_fluence_cm2_s)}</strong> fotones/cm²/s</p>
      <small>{result.source_count} fuentes · {result.radionuclide} · {result.irradiation_date}. Referencia puntual para comparar con Monte Carlo.</small>
    </div>}
  </details>;
}

function MaterialForm({ material, onClose, onSaved }: { material: Material; onClose: () => void; onSaved: () => void }) {
  const [density, setDensity] = useState(String(material.density));
  const [elements, setElements] = useState(JSON.stringify(material.elements, null, 2));
  const [coefficients, setCoefficients] = useState(JSON.stringify(material.mu_en_over_rho, null, 2));
  const [error, setError] = useState('');

  const save = async () => {
    try {
      await apiJson(`/api/config/materials/${encodeURIComponent(material.name)}`, 'PUT', {
        name: 'air',
        transport_medium: 'air',
        dosimeter_medium: 'alanine',
        coefficient_quantity: 'mass_energy_absorption',
        coefficient_units: 'cm2/g',
        coefficient_source: 'NIST X-Ray Mass Attenuation Coefficients, Table 4: Alanine',
        fraction_type: 'weight',
        density: Number(density),
        elements: JSON.parse(elements),
        mu_en_over_rho: JSON.parse(coefficients),
      });
      onSaved();
    } catch {
      setError('Revisa la densidad y la sintaxis JSON de la composición y los coeficientes.');
    }
  };

  return (
    <DialogFrame onClose={onClose} label="Editar calibración del simulador">
      <section className="editor-modal wide">
        <header><div><span className="section-label">Referencia de verificación</span><h2>Editar calibración del simulador</h2></div><button className="icon-button" aria-label="Cerrar" onClick={onClose}><X size={18} /></button></header>
        <div className="editor-grid">
          <label className="field"><span>Medio de transporte</span><input value="Aire" disabled /></label>
          <label className="field"><span>Respuesta dosimétrica</span><input value="Alanina" disabled /></label>
          <label className="field"><span>Densidad del aire [<i className="unit-lowercase">g/cm³</i>]</span><input type="number" step="0.000001" value={density} onChange={(event) => setDensity(event.target.value)} /></label>
          <div />
          <label className="field full"><span>Composición elemental del aire · fracción en peso NIST · JSON</span><textarea rows={7} value={elements} onChange={(event) => setElements(event.target.value)} /></label>
          <label className="field full"><span>μen/ρ de alanina por energía [cm²/g] · JSON</span><textarea rows={7} value={coefficients} onChange={(event) => setCoefficients(event.target.value)} /></label>
        </div>
        {error && <p className="form-error">{error}</p>}
        <footer><span /><span /><button className="button ghost" onClick={onClose}>Cancelar</button><button className="button primary" onClick={save}>Guardar calibración</button></footer>
      </section>
    </DialogFrame>
  );
}

export default function MaterialsView({ routeWorkflow = false, visible = true, calibrationRequest }: {
  routeWorkflow?: boolean; visible?: boolean; calibrationRequest?: { caseId: string };
}) {
  const [results, setResults] = useState(Boolean(calibrationRequest));
  const [selectedCaseId, setSelectedCaseId] = useState(calibrationRequest?.caseId ?? '');
  const [openedRequest, setOpenedRequest] = useState(calibrationRequest);
  if (calibrationRequest && calibrationRequest !== openedRequest) {
    setOpenedRequest(calibrationRequest);
    setSelectedCaseId(calibrationRequest.caseId);
    setResults(true);
  }
  const showResults = routeWorkflow && results;
  const [materials, setMaterials] = useState<Material[]>([]);
  const [editor, setEditor] = useState<Material | null>(null);
  const [error, setError] = useState('');
  const load = async () => {
    try {
      const data = await apiGet<{ materials: Material[] }>('/api/config/materials');
      setMaterials(data.materials);
      setError('');
    } catch {
      setError('No fue posible cargar la referencia de calibración.');
    }
  };

  useEffect(() => {
    apiGet<{ materials: Material[] }>('/api/config/materials')
      .then((data) => { setMaterials(data.materials); setError(''); })
      .catch(() => setError('No fue posible cargar la referencia de calibración.'));
  }, []);

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div><span className="section-label">Verificación cruzada</span><h2>Calibración del simulador</h2><p>Compara el método determinista y Monte Carlo bajo una condición de referencia reproducible en aire.</p></div>
        <span className="status-chip success"><BadgeCheck size={15}/> Calibración en aire</span>
      </section>
      {routeWorkflow && <div className="result-analysis-tabs cad-mode-tabs" role="tablist" aria-label="Vistas de calibración">
        <button type="button" role="tab" id="calibration-setup-tab" aria-controls="calibration-setup" aria-selected={!showResults} onClick={() => setResults(false)}>Configurar calibración</button>
        <button type="button" role="tab" id="calibration-results-tab" aria-controls="calibration-results" aria-selected={showResults} onClick={() => setResults(true)}>Resultados de calibración</button>
      </div>}
      <div hidden={showResults} className="view-stack" id="calibration-setup" role={routeWorkflow ? 'tabpanel' : undefined} aria-labelledby={routeWorkflow ? 'calibration-setup-tab' : undefined}>
        <div className="info-note calibration-note"><strong>Dos medios con funciones diferentes</strong><p>El aire es el medio donde se transportan los fotones durante la calibración. La alanina representa la respuesta del dosímetro utilizado para convertir fluencia de energía en dosis absorbida.</p></div>
        {routeWorkflow && <AirRouteCalibrationForm visible={visible && !showResults} onResults={(caseId) => { setSelectedCaseId(caseId); setResults(true); }}/>}
        {routeWorkflow && <AirReferenceForm/>}
        {error ? <div className="empty-state"><p>{error}</p><button className="button primary" onClick={load}>Reintentar</button></div> : (
          <div className="material-grid calibration-grid">
            {materials.filter((material) => material.name === 'air').map((material) => (
              <article className="surface material-card calibration-card" key={material.name}>
                <header><div className="material-icon"><Atom size={20} /></div><div><span className="section-label">Medio de transporte</span><h3>Aire</h3></div><button className="icon-button" aria-label="Editar calibración" onClick={() => setEditor(material)}><Edit3 size={16} /></button></header>
                <div className="material-density"><span>Densidad del aire</span><strong>{material.density.toLocaleString(uiLocale(), { maximumFractionDigits: 6 })}</strong><small>g/cm³</small></div>
                <div className="element-chips" aria-label="Composición elemental del aire">{Object.entries(material.elements).map(([element, fraction]) => <span key={element}><strong>{element}</strong>{(fraction * 100).toFixed(2)}%</span>)}</div>
                <div className="dosimetry-callout"><ShieldCheck size={20}/><div><span>Respuesta dosimétrica</span><strong>Alanina</strong><p>Coeficiente másico de absorción de energía, μen/ρ.</p></div></div>
                <div className="coefficient-list alanine-coefficients"><span>μen/ρ de alanina <small>[cm²/g]</small></span>{Object.entries(material.mu_en_over_rho).map(([energy, value]) => <div key={energy}><b>{energy} MeV</b><strong>{value.toFixed(5)}</strong></div>)}</div>
                <div className="coefficient-verification"><BadgeCheck size={15}/><p><strong>Valores verificados</strong>Coinciden con la tabla NIST de alanina dentro del redondeo de interpolación.</p></div>
                <a className="nist-link" href={NIST_ALANINE_URL} target="_blank" rel="noreferrer">Consultar tabla NIST de alanina <ExternalLink size={13}/></a>
                <button className="button secondary full" onClick={() => setEditor(material)}><Edit3 size={15} /> Editar referencia</button>
              </article>
            ))}
          </div>
        )}
      </div>
      {routeWorkflow && <div id="calibration-results" className="view-stack" role="tabpanel" aria-labelledby="calibration-results-tab" hidden={!showResults}>
        {visible && showResults && <AirCalibrationResults preferredCaseId={selectedCaseId} preferOpenMc={Boolean(calibrationRequest && selectedCaseId === calibrationRequest.caseId)} onSelect={setSelectedCaseId}/>}
      </div>}
      {editor && <MaterialForm material={editor} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); void load(); }} />}
    </div>
  );
}
