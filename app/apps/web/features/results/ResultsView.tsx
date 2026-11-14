/* eslint-disable @next/next/no-img-element */
'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useEffect, useState } from 'react';
import {
  Activity, BarChart3, CheckCircle2, Download, FileJson2,
  FlaskConical, LockKeyhole, MoveVertical, RotateCw, ScanLine,
} from 'lucide-react';
import VtkDoseViewer, { type VtkMaterial } from '../../components/results/VtkDoseViewer';
import Isodose3DViewer from '../../components/results/Isodose3DViewer';
import PositionDoseCuts from '../../components/results/PositionDoseCuts';
import { API_URL, apiGet } from '../../lib/api';
import type { IrradiatorType } from '../../lib/types';
import type { IrradiationCase } from '../../lib/types';

type Movement = {
  step_angle_degrees_best_real: number[];
  step_length: Record<string, number>;
  work_section: string;
  work_section_position: number[];
  average_dose: number;
  description: string;
};

export type MethodResult = {
  state: string;
  progress: number;
  stage?: string;
  error?: string;
  artifact_revision?: string;
  summary: Record<string, unknown> | null;
  artifacts: { vtk: boolean; vtk_format?: 'vtk' | 'vti' | null; cut_xy: boolean; cut_yz: boolean };
};

type ResultCatalog = {
  case_id: string;
  name: string;
  description: string;
  monte_carlo_dose_tally: 'flux' | 'heating' | 'alanine' | 'phits';
  load_materials: VtkMaterial[];
  methods: { deterministic: MethodResult; monte_carlo: MethodResult };
};

function numeric(summary: Record<string, unknown> | null, ...keys: string[]) {
  for (const key of keys) {
    const value = summary?.[key];
    if (typeof value === 'number') return value;
  }
  return null;
}

export type ResultTool = 'volume' | 'isodoses' | 'cuts' | 'metrics';

export function MethodPanel({
  caseId,
  method,
  title,
  subtitle,
  order,
  materials = [],
  hideHeader = false,
  artifactBase,
  quantityUnit = 'kGy/h',
  deterministicAir = false,
  positionYaw,
  tool,
  onToolChange,
}: {
  caseId: string;
  method: MethodResult;
  title: string;
  subtitle: string;
  order: string;
  materials?: VtkMaterial[];
  hideHeader?: boolean;
  artifactBase?: string;
  quantityUnit?: string;
  deterministicAir?: boolean;
  positionYaw?: number;
  tool?: ResultTool;
  onToolChange?: (tool: ResultTool) => void;
}) {
  const [internalTool, setInternalTool] = useState<ResultTool>('volume');
  const analysisTab = tool ?? internalTool;
  const setAnalysisTab = onToolChange ?? setInternalTool;
  const route = title.includes('Monte Carlo') ? 'monte_carlo' : 'deterministic';
  const volumetricStudy = route === 'monte_carlo' || deterministicAir;
  const isPhits = title.includes('PHITS') || method.summary?.dose_tally === 'phits' || String(method.summary?.backend ?? '').startsWith('phits-');
  const statisticalEvaluation = method.summary?.statistical_evaluation as Record<string, unknown> | undefined;
  const average = numeric(statisticalEvaluation ?? null, 'average_dose')
    ?? numeric(method.summary, 'average_dose', 'average_theoretical_dose');
  const visualization = method.summary?.visualization as Record<string, unknown> | undefined;
  const ratio = numeric(statisticalEvaluation ?? null, 'dose_uniformity_ratio')
    ?? numeric(method.summary, 'real_flux_ratio', 'flux_ratio', 'dose_uniformity_ratio')
    ?? numeric(visualization ?? null, 'dose_uniformity_ratio');
  const robustRatio = numeric(statisticalEvaluation ?? null, 'robust_dose_uniformity_ratio')
    ?? numeric(method.summary, 'robust_dose_uniformity_ratio');
  const operationalRatio = numeric(statisticalEvaluation ?? null, 'operational_dose_uniformity_ratio')
    ?? numeric(method.summary, 'operational_dose_uniformity_ratio');
  const extendedRatio = numeric(statisticalEvaluation ?? null, 'd98_d2_uniformity_ratio')
    ?? numeric(method.summary, 'd98_d2_uniformity_ratio');
  const minimum = numeric(statisticalEvaluation ?? null, 'minimum_dose')
    ?? numeric(method.summary, 'minimum_dose')
    ?? numeric(visualization ?? null, 'minimum_dose');
  const maximum = numeric(statisticalEvaluation ?? null, 'maximum_dose')
    ?? numeric(method.summary, 'maximum_dose')
    ?? numeric(visualization ?? null, 'maximum_dose');
  const rawRatio = minimum !== null && maximum !== null && minimum > 0
    && Number.isFinite(minimum) && Number.isFinite(maximum) ? maximum / minimum : null;
  const doseP98 = numeric(statisticalEvaluation ?? null, 'dose_p98')
    ?? numeric(method.summary, 'dose_p98')
    ?? numeric(visualization ?? null, 'dose_p98');
  const available = method.artifacts.vtk || method.artifacts.cut_xy || method.artifacts.cut_yz;
  const base = artifactBase ?? `${API_URL}/api/results/cases/${caseId}/${route}`;
  const artifactQuery = `?v=${encodeURIComponent(method.artifact_revision ?? '0')}`;
  const totalCells = numeric(method.summary, 'total_cells');
  const activeCells = numeric(method.summary, 'active_cells')
    ?? numeric(visualization ?? null, 'active_cells');
  const cellSize = numeric(method.summary, 'cell_size_cm');
  const relativeErrorMedian = numeric(method.summary, 'relative_error_median')
    ?? numeric(statisticalEvaluation ?? null, 'relative_error_median')
    ?? numeric(visualization ?? null, 'relative_error_median');
  const relativeErrorP90 = numeric(method.summary, 'relative_error_p90')
    ?? numeric(statisticalEvaluation ?? null, 'relative_error_p90')
    ?? numeric(visualization ?? null, 'relative_error_p90');
  const transportWallSeconds = numeric(method.summary, 'transport_wall_seconds');
  const relativeErrorMaximum = numeric(method.summary, 'relative_error_maximum')
    ?? numeric(statisticalEvaluation ?? null, 'relative_error_maximum');
  const displayedRelativeError = relativeErrorMedian;
  const simulatedParticles = numeric(method.summary, 'simulated_particles', 'particles');
  const requestedParticles = numeric(method.summary, 'requested_max_particles', 'particles');
  const actualBatches = numeric(method.summary, 'actual_batches');
  const aggregatedMaps = method.summary?.aggregated_maps === true;
  const minBatches = numeric(method.summary, 'batches_per_position_min');
  const maxBatches = numeric(method.summary, 'batches_per_position_max');
  const requestedBatches = numeric(method.summary, 'requested_batches');
  const adaptiveConverged = method.summary?.adaptive_converged === true;
  const varianceReduction = method.summary?.variance_reduction as Record<string, unknown> | undefined;
  const relativeErrorTarget = numeric(varianceReduction ?? null, 'relative_error_target');
  const varianceMethod = String(varianceReduction?.method ?? 'transporte configurado');
  const knownVarianceMethods: Record<string, string> = {
    'scene-conditioned-area-mixture': 'Fuente hacia carga y vecinas',
    'full-support-angular-mixture': 'Fuente mixta', 'box-conditioned-angular-mixture': 'Fuente condicionada',
    'box-conditioned-angular-mixture-and-local-splitting': 'Fuente condicionada + división local',
    'box-conditioned-source-and-magic-photon-windows': 'Fuente condicionada + MAGIC',
    'magic-photon-weight-windows': 'Ventanas MAGIC', phits_windows: 'Ventanas T-WWG', phits_importance: 'Importancia regional',
  };
  const varianceMethodLabel = knownVarianceMethods[varianceMethod] ?? (varianceMethod === 'dynamic-load-cone-plus-photon-importance-splitting'
    ? 'cono dinámico + importancia fotónica regional'
    : varianceMethod === 'photon-region-importance-splitting'
      ? 'importancia fotónica regional'
      : varianceMethod === 'analog'
        ? 'analógico'
        : varianceMethod);

  return (
    <section className="surface method-result">
      {!hideHeader && <header className="surface-header">
        <div className="method-title"><span>{order}</span><div><span className="section-label">{subtitle}</span><h3>{title}</h3></div></div>
        <span className={method.state === 'completed' ? 'status-chip success' : 'status-chip'}>{method.state === 'completed' ? <CheckCircle2 size={14}/> : route === 'monte_carlo' ? <LockKeyhole size={14}/> : <Activity size={14}/>} {method.state}</span>
      </header>}

      {!available && <div className="method-empty">
        {route === 'deterministic' ? <BarChart3 size={30}/> : <FlaskConical size={30}/>}
        <div><h4>{route === 'deterministic' ? 'Sin distribución determinista disponible' : `${isPhits ? 'PHITS' : 'Monte Carlo'} pendiente de ejecución`}</h4><p>{route === 'deterministic' ? 'Ejecuta la optimización del caso para generar los cortes XY/YZ y el volumen VTK.' : `Esta sección recibirá los cortes y el VTK de ${isPhits ? 'PHITS' : 'OpenMC'} al completar el transporte.`}</p></div>
      </div>}

      {available && <>
        <div className="result-analysis-tabs" role="tablist" aria-label="Herramientas de resultados">
          {([['volume', 'Volumen y materiales'], ['isodoses', 'Isodosis 3D'], ['cuts', 'Cortes XY / YZ'], ['metrics', 'Métricas completas']] as const).map(([id, label]) => (
            <button type="button" role="tab" aria-selected={analysisTab === id} key={id} onClick={() => setAnalysisTab(id)}>{label}</button>
          ))}
        </div>
        {analysisTab === 'metrics' && <div className="dose-metrics">
          <div><span>{quantityUnit !== 'kGy' ? 'Tasa promedio' : 'Dosis promedio'}</span><strong>{average?.toExponential(4) ?? '—'}</strong><small>{statisticalEvaluation && !deterministicAir ? `${quantityUnit} · evaluación estadística` : quantityUnit}</small></div>
          {volumetricStudy && <div><span>{quantityUnit !== 'kGy' ? 'Tasa alta P98' : 'Dosis alta P98'}</span><strong>{doseP98?.toExponential(4) ?? '—'}</strong><small>{quantityUnit} · percentil 98</small></div>}
          <div><span>{operationalRatio !== null ? 'Razón P90/P10' : 'Uniformidad DUR'}</span><strong>{(operationalRatio ?? ratio)?.toFixed(3) ?? '—'}</strong><small>{operationalRatio !== null ? 'Indicador percentil · no sustituye máximo / mínimo' : 'máxima / mínima'}</small></div>
          {volumetricStudy && <div><span>Razón P95/P5</span><strong>{robustRatio?.toFixed(3) ?? '—'}</strong><small>Indicador percentil · sensibilidad a las colas</small></div>}
          {volumetricStudy && <div><span>Razón P98/P02</span><strong>{extendedRatio?.toFixed(3) ?? '—'}</strong><small>Indicador percentil · extremos percentiles</small></div>}
          <div><span>Intervalo</span><strong>{minimum !== null && maximum !== null ? `${minimum.toExponential(2)} – ${maximum.toExponential(2)}` : '—'}</strong><small>{quantityUnit} · extremos crudos</small></div>
          <div><span>DUR crudo</span><strong>{rawRatio !== null && Number.isFinite(rawRatio) ? rawRatio.toFixed(3) : '—'}</strong><small>{minimum === 0 ? 'No definido: mínimo crudo igual a cero' : 'Máximo / mínimo · extremos crudos'}</small></div>
          {volumetricStudy && <div><span>Tamaño de celda</span><strong>{cellSize !== null ? `${cellSize.toLocaleString(uiLocale())} cm` : '—'}</strong><small>instantánea ejecutada</small></div>}
          {volumetricStudy && <div><span>{deterministicAir ? 'Malla de calibración' : 'Malla Monte Carlo'}</span><strong>{totalCells?.toLocaleString(uiLocale()) ?? '—'}</strong><small>celdas totales</small></div>}
          {volumetricStudy && <div><span>Volumen de la carga</span><strong>{activeCells?.toLocaleString(uiLocale()) ?? '—'}</strong><small>celdas activas</small></div>}
          {route === 'monte_carlo' && <div><span>Error relativo mediano</span><strong>{displayedRelativeError !== null ? `${(displayedRelativeError * 100).toFixed(2)} %` : '—'}</strong><small>{relativeErrorP90 !== null ? `P90 ${(relativeErrorP90 * 100).toFixed(2)} %` : 'medido en la carga'}</small></div>}
          {route === 'monte_carlo' && <div><span>Historias simuladas</span><strong>{simulatedParticles?.toLocaleString(uiLocale()) ?? '—'}</strong><small>{requestedParticles !== null ? `máximo solicitado: ${requestedParticles.toLocaleString(uiLocale())}` : 'presupuesto ejecutado'}</small></div>}
          {route === 'monte_carlo' && <div><span>Plan estadístico</span><strong>{aggregatedMaps ? `${minBatches ?? '—'}${maxBatches !== minBatches ? `–${maxBatches ?? '—'}` : ''} / posición` : `${actualBatches?.toLocaleString(uiLocale()) ?? '—'} lotes`}</strong><small>{varianceReduction?.checkpointed_batching ? 'Lotes ajustados para guardar avances · ' : requestedBatches !== null ? `solicitados: ${requestedBatches.toLocaleString(uiLocale())} · ` : ''}{varianceMethodLabel}</small></div>}
          {route === 'monte_carlo' && <div><span>{aggregatedMaps ? 'Transporte de los mapas' : 'Tiempo de transporte'}</span><strong>{transportWallSeconds !== null ? `${transportWallSeconds.toLocaleString(uiLocale(), { maximumFractionDigits: 1 })} s` : '—'}</strong><small>{aggregatedMaps ? 'Coste histórico de los mapas; no es el tiempo de sumar' : isPhits ? 'PHITS, sin posprocesamiento' : 'OpenMC, sin posprocesamiento'}</small></div>}
          {route === 'monte_carlo' && <div><span>Convergencia estadística</span><strong>{method.summary?.transport_time_limited ? 'Tiempo agotado' : varianceReduction?.adaptive_statistics ? (adaptiveConverged ? 'Objetivo alcanzado' : 'Presupuesto agotado') : 'Ejecución fija'}</strong><small>{relativeErrorMaximum !== null ? `error máximo ${(relativeErrorMaximum * 100).toFixed(2)} %${relativeErrorTarget !== null ? ` · objetivo ${(relativeErrorTarget * 100).toFixed(2)} %` : ''} · transporte ${varianceMethodLabel}` : 'error evaluado directamente en la carga'}</small></div>}
        </div>}
        {analysisTab === 'cuts' && (positionYaw !== undefined && method.artifacts.vtk && method.artifacts.vtk_format === 'vti'
          ? <PositionDoseCuts url={`${base}/vtk${artifactQuery}`} yaw={positionYaw} unit={quantityUnit}/> : <div className="cut-grid">
          {(['xy', 'yz'] as const).map((plane) => {
            const exists = plane === 'xy' ? method.artifacts.cut_xy : method.artifacts.cut_yz;
            return exists && <figure key={plane}><div><ScanLine size={15}/><strong>Corte {plane.toUpperCase()}</strong></div><img loading="lazy" src={`${base}/cut/${plane}${artifactQuery}`} alt={`Mapa de isodosis ${plane.toUpperCase()} de ${title}`}/></figure>;
          })}
        </div>)}
        {analysisTab === 'volume' && method.artifacts.vtk && <VtkDoseViewer key={`${base}-${method.artifact_revision}`} url={`${base}/vtk${artifactQuery}`} format={method.artifacts.vtk_format ?? 'vtk'} label={`Distribución volumétrica · ${title}`} materials={volumetricStudy ? materials : undefined} quantityLabel={quantityUnit === 'kGy' ? 'Dosis absorbida [kGy]' : 'Tasa de dosis absorbida [kGy/h]'} positionYaw={positionYaw}/>}
        {analysisTab === 'isodoses' && volumetricStudy && method.artifacts.vtk && <Isodose3DViewer key={`isodose-${base}-${method.artifact_revision}`} url={`${base}/vtk${artifactQuery}`} format={method.artifacts.vtk_format ?? 'vtk'} unit={quantityUnit} positionYaw={positionYaw}/>}
      </>}
    </section>
  );
}

function MovementOutput({ movement }: { movement: Movement }) {
  const angles = movement.step_angle_degrees_best_real;
  const levels = Object.entries(movement.step_length);
  const initialLevel = levels.find(([key]) => key.includes('best'))?.[0] ?? levels[0]?.[0] ?? '';
  const [selectedAngleIndex, setSelectedAngleIndex] = useState(0);
  const [selectedLevelKey, setSelectedLevelKey] = useState(initialLevel);
  const selectedAngle = angles[selectedAngleIndex] ?? 0;
  const selectedLevel = movement.step_length[selectedLevelKey];

  return (
    <>
      <div className="movement-output-grid">
        <div className="movement-visualization">
          <div className="movement-figure-heading">
            <div><RotateCw size={16}/><span>Trayectoria optimizada</span></div>
            <small>Selecciona un ángulo o una posición para inspeccionarlos</small>
          </div>

          <div className="movement-summary">
            <div className="movement-control">
              <span className="movement-control-label">Secuencia angular</span>
              <div className="angle-orbit" aria-label="Ángulos optimizados de la trayectoria">
                <div className="orbit-center">
                  <RotateCw size={18}/>
                  <strong>{selectedAngle}°</strong>
                  <small>{selectedAngleIndex + 1} de {angles.length}</small>
                </div>
                {angles.map((angle, index) => {
                  const radians = (angle - 90) * Math.PI / 180;
                  const style = {
                    left: `${50 + Math.cos(radians) * 43}%`,
                    top: `${50 + Math.sin(radians) * 43}%`,
                  };
                  return (
                    <button
                      key={`${angle}-${index}`}
                      type="button"
                      className={selectedAngleIndex === index ? 'active' : ''}
                      style={style}
                      onClick={() => setSelectedAngleIndex(index)}
                      aria-pressed={selectedAngleIndex === index}
                      aria-label={`Ángulo ${angle} grados, paso ${index + 1}`}
                    >
                      {angle}°
                    </button>
                  );
                })}
              </div>
              <div className="movement-readout"><span>Ángulo seleccionado</span><strong>{selectedAngle}°</strong></div>
            </div>

            <div className="movement-control level-control">
              <span className="movement-control-label">Desplazamiento longitudinal</span>
              <div className="level-route" aria-label="Posiciones optimizadas de la trayectoria">
                {levels.map(([key, value], index) => {
                  const isBest = key.includes('best');
                  const selected = selectedLevelKey === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      className={`${isBest ? 'best' : ''} ${selected ? 'active' : ''}`}
                      onClick={() => setSelectedLevelKey(key)}
                      aria-pressed={selected}
                    >
                      <i aria-hidden="true"/>
                      <span>Posición {index + 1}</span>
                      <strong>{value} mm</strong>
                      {isBest && <small>óptima</small>}
                    </button>
                  );
                })}
              </div>
              <div className="movement-readout"><span>Posición seleccionada</span><strong>{selectedLevel ?? '—'} mm</strong></div>
            </div>
          </div>

          <div className="movement-selection">
            <div><RotateCw size={15}/><span>Paso angular</span><strong>{selectedAngle}°</strong></div>
            <div><MoveVertical size={15}/><span>Posición longitudinal</span><strong>{selectedLevel ?? '—'} mm</strong></div>
            <div><Activity size={15}/><span>Dosis promedio</span><strong>{movement.average_dose.toExponential(5)}</strong></div>
          </div>
        </div>

        <div className="json-preview">
          <header><FileJson2 size={19}/><div><span className="section-label">Contrato de salida</span><strong>Vista previa del archivo</strong></div></header>
          <pre>{JSON.stringify(movement, null, 2)}</pre>
        </div>
      </div>

      <div className="contract-grid">
        <div><span>Descripción de irradiación</span><p>{movement.description}</p></div>
        <div><span>Sección</span><strong>{movement.work_section} · [{movement.work_section_position.join(', ')}]</strong></div>
        <div><span>Ángulos</span><strong>{movement.step_angle_degrees_best_real.join('° · ')}°</strong></div>
        <div><span>Dosis promedio</span><strong>{movement.average_dose.toExponential(5)}</strong></div>
      </div>
    </>
  );
}

export default function ResultsView({
  preferredCaseId,
  refreshKey,
  irradiator,
}: {
  preferredCaseId?: string;
  refreshKey: number;
  irradiator: IrradiatorType;
}) {
  const [cases, setCases] = useState<IrradiationCase[]>([]);
  const [caseId, setCaseId] = useState('');
  const [catalog, setCatalog] = useState<ResultCatalog | null>(null);
  const [movement, setMovement] = useState<{ caseId: string; document: Movement } | null>(null);
  const [error, setError] = useState('');
  const [casesLoading, setCasesLoading] = useState(true);

  useEffect(() => {
    apiGet<{ cases: IrradiationCase[] }>('/api/cases')
      .then((response) => {
        setCases(response.cases);
        setCaseId((current) => {
          if (preferredCaseId && response.cases.some((item) => item.case_id === preferredCaseId)) {
            return preferredCaseId;
          }
          if (current && response.cases.some((item) => item.case_id === current)) return current;
          return response.cases[0]?.case_id ?? '';
        });
      })
      .catch(() => setError('No fue posible cargar los casos.'))
      .finally(() => setCasesLoading(false));
  }, [preferredCaseId, refreshKey]);

  useEffect(() => {
    if (!caseId) return;
    let cancelled = false;
    apiGet<ResultCatalog>(`/api/results/cases/${caseId}`)
      .then((response) => {
        if (cancelled) return;
        setCatalog(response);
        setError('');
      })
      .catch(() => {
        if (!cancelled) setError('No fue posible cargar los resultados del caso.');
      });
    apiGet<Movement>(`/api/results/cases/${caseId}/movements`)
      .then((response) => {
        if (!cancelled) setMovement({ caseId, document: response });
      })
      .catch(() => {
        if (!cancelled) setMovement(null);
      });
    return () => { cancelled = true; };
  }, [caseId, refreshKey]);

  const activeCatalog = catalog?.case_id === caseId ? catalog : null;
  const activeMovement = movement?.caseId === caseId ? movement.document : null;
  const monteCarloTitle = activeCatalog?.monte_carlo_dose_tally === 'heating'
    ? 'Monte Carlo · Cálculo intensivo'
    : activeCatalog?.monte_carlo_dose_tally === 'alanine'
      ? 'Monte Carlo · Cálculo alternativo'
      : activeCatalog?.monte_carlo_dose_tally === 'phits'
        ? 'Monte Carlo · Cálculo Comparativo PHITS'
      : 'Monte Carlo · Cálculo rápido';

  if (!cases.length && error) return <div className="empty-state"><FileJson2 size={30}/><h2>Sin casos disponibles</h2><p>{error}</p></div>;

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div><span className="section-label">Comparación de métodos</span><h2>Resultados · Categoría {irradiator.id}</h2><p>Resultados exclusivos de este tipo de irradiador. Revisa el método determinista y la validación Monte Carlo de cada caso.</p></div>
        <label className="result-case-select"><span>Caso de irradiación</span><select value={caseId} disabled={!cases.length} onChange={(event) => setCaseId(event.target.value)}>{!cases.length && <option value="">{casesLoading ? 'Cargando casos…' : 'No hay casos disponibles'}</option>}{cases.map((item) => <option key={item.case_id} value={item.case_id}>{item.name}</option>)}</select></label>
      </section>

      {activeCatalog ? <>
        <div className="result-context"><div><span className="section-label">Caso seleccionado</span><h3>{activeCatalog.name}</h3><p>{activeCatalog.description}</p></div></div>
        <details className="surface result-collapsible">
          <summary>
            <div className="method-title"><span>01</span><div><span className="section-label">Distribución calculada</span><h3>Método determinista</h3><small>Estimación en aire, referida a la respuesta dosimétrica de alanina</small></div></div>
            <span className="collapse-hint">Mostrar resultados</span>
          </summary>
          <MethodPanel caseId={activeCatalog.case_id} method={activeCatalog.methods.deterministic} title="Método determinista" subtitle="Estimación en aire · respuesta dosimétrica de alanina" order="01" hideHeader/>
        </details>
        <MethodPanel caseId={activeCatalog.case_id} method={activeCatalog.methods.monte_carlo} title={monteCarloTitle} subtitle="Validación de transporte" order="02" materials={activeCatalog.load_materials}/>
      </> : casesLoading
        ? <div className="loading-panel">Cargando resultados del caso…</div>
        : cases.length
          ? <div className="loading-panel">Cargando resultados del caso…</div>
          : <div className="surface method-empty"><FileJson2 size={28}/><div><h4>Sin casos de irradiación</h4><p>Crea y ejecuta un caso para consultar aquí sus resultados.</p></div></div>}

      <section className="surface final-contract">
        <header className="surface-header"><div><span className="section-label">Salida para monitoreo y control</span><h3>movements_parameters_optimized.json</h3></div>{activeMovement && <a className="button primary" href={`${API_URL}/api/results/cases/${caseId}/movements`} download><Download size={15}/> Descargar JSON</a>}</header>
        {activeMovement ? <MovementOutput key={caseId} movement={activeMovement}/> : <div className="method-empty"><FileJson2 size={28}/><div><h4>Resultados aún no guardados</h4><p>Completa ambas simulaciones y guarda los resultados desde el caso de irradiación.</p></div></div>}
      </section>
    </div>
  );
}
