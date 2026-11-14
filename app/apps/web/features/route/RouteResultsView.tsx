'use client';
import { numberFormatter } from '../../lib/i18n/number';

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Activity, Box, ChartNoAxesCombined, ChevronRight, Clock3, Layers3, PanelsTopLeft, Pause, Play, RefreshCw, Rows3, Settings2, ShieldCheck, Timer } from 'lucide-react';
import { API_URL, apiGet, apiJson } from '../../lib/api';
import { availableStudyInterval, selectStudyCase, selectStudyRun } from '../../lib/studySelection';
import { requestedPositionCount, type StudyProgressInput } from '../../lib/studyProgress';
import type { GammaSource, IrradiationCase } from '../../lib/types';
import { methodLabels, readableError, VarianceOptions, type RouteConfiguration, type RoutePosition } from './RouteConfigurationView';
import type { VtkMaterial } from '../../components/results/VtkDoseViewer';
import RouteAnalysisView from './RouteAnalysisView';
import RouteRateChart from './RouteRateChart';
import StudyPositionOverview from './StudyPositionOverview';
import RouteSuperpositionCheck, { type RouteAlignment } from './RouteSuperpositionCheck';
import type { ResultTool } from '../results/ResultsView';
import StudyProgressBar from './StudyProgressBar';
import RoutePositionPicker from './RoutePositionPicker';
import StudyActivityRibbon from '../../components/layout/StudyActivityRibbon';
import AirDoseMethodNote, { isAirReference } from './AirDoseMethodNote';

type Precision = {
  state: 'recommended' | 'initial' | 'insufficient';
  meets_initial: boolean; meets_recommended: boolean;
  active_cells: number; cells_with_error_estimate: number; relative_error_maximum: number | null;
};
const precisionLabels = { recommended: 'Recomendado · < 5 %', initial: 'Inicial · < 10 %', insufficient: 'Precisión insuficiente' };

type RatePosition = RoutePosition & {
  mean_kgy_h: number; minimum_kgy_h: number; maximum_kgy_h: number; relative_error_p90: number | null;
  simulated_particles?: number; materials?: VtkMaterial[]; summary?: string;
  relative_error_maximum?: number; precision?: Precision;
  material_statistics?: (VtkMaterial & {
    average_dose: number; minimum_dose: number; maximum_dose: number; relative_error_p90?: number | null;
  })[];
};
type IntervalPrecision = {
  target: number; meets_target: boolean; passes: number;
  relative_error_maximum: number | null; relative_error_p90: number | null;
  relative_error_p95?: number | null; relative_error_p98?: number | null;
};
type IntegralMaterial = { name: string; mass_kg: number; mean_rate_kgy_h: number; std_dev_rate_kgy_h: number };
export type RouteRates = {
  analysis_method?: IrradiationCase['analysis_method'];
  case_id: string; run_id: string; activity_date: string; positions: RatePosition[];
  occupancy?: RouteConfiguration['occupancy']; dose_tally?: RouteConfiguration['dose_tally'];
  cell_size_cm?: number; quick?: boolean; wall_seconds?: number; complete?: boolean; requested_position_count?: number; stop_reason?: string;
  variance_reduction?: RouteConfiguration['variance_reduction'];
  relative_error_target?: number;
  source_mixture_fraction?: number;
  score_interval?: [number, number];
  selected_position_names?: string[];
  resources?: RouteJob['resources'];
  accumulated_error_target?: number | null; interval_precision?: IntervalPrecision; completed_calculations?: number;
  transport_geometry_revision?: number; exterior_medium?: string;
  adaptive_statistics?: boolean;
  requested_particles_per_position?: number;
  integrated_material_check?: { positions: { name: string; materials: IntegralMaterial[] }[]; not_a_voxel_error: boolean };
  extended_time?: boolean;
  cpu_cores?: number;
  precision?: { meets_initial: boolean; meets_recommended: boolean };
};
type PrecisionEstimate = { available: boolean; reason?: string; estimated_total_histories?: number; estimated_seconds?: number;
  estimated_histories_per_position_maximum?: number; suggested_max_particles_per_position?: number; suggested_time_limit_seconds?: number; note?: string; };
type ResourcePlan = { cells: number; estimated_ram_mb: number; memory_limit_mb: number; feasible: boolean; geometry_instances?: number; precision_estimate?: PrecisionEstimate | null };
export type StudySettings = Pick<RouteConfiguration, 'calculation_positions' | 'cad_offset_degrees' | 'positions' | 'dose_tally' | 'occupancy' | 'variance_reduction' | 'particles' | 'batches' | 'cell_size_cm' | 'relative_error_target'> & {
  statistical_refinement_enabled?: boolean; fixed_particles?: number;
  accumulated_error_target?: number; interval_max_particles?: number; time_limit_seconds?: number;
  today: string; minimum_date: string | null; source_mixture_fraction: number; memory_recommended_mb: number;
  sources?: GammaSource[]; position_reference?: RouteConfiguration['position_reference'];
  adaptive_statistics?: boolean;
  extended_time_limit_seconds?: number;
};
type Study = {
  run_id: string; activity_date: string; first: string; last: string; count: number; quick: boolean;
  cell_size_cm?: number; variance_reduction?: string; complete?: boolean; requested_position_count?: number;
  occupancy?: RouteConfiguration['occupancy'];
  adaptive_statistics?: boolean;
  accumulated_error_target?: number | null; interval_precision?: IntervalPrecision; completed_calculations?: number;
  precision?: { meets_initial: boolean; meets_recommended: boolean };
};
const varianceLabels: Record<string, string> = {
  analog: 'Analógico', magic: 'MAGIC', source_mixture: 'Fuente mixta',
  source_scene: 'Carga y vecinas', source_box: 'Dirección condicionada', source_box_magic: 'Dirección condicionada + MAGIC', source_box_windows: 'Dirección condicionada + división',
  phits_importance: 'Importancia 4:1', phits_windows: 'T-WWG',
};
export type RouteJob = {
  can_resume?: boolean; saved_position_count?: number;
  state: string; progress: number; execution_enabled: boolean; error?: string; run_id?: string;
  route_study?: {
    irradiation_date?: string; particles?: number; start?: number; end?: number; position_names?: string[] | null;
    quick?: boolean; adaptive_statistics?: boolean; accumulated_error_target?: number | null;
    budget_reference_run_id?: string | null; cell_size_cm?: number; memory_limit_mb?: number;
    time_limit_seconds?: number; extended_time?: boolean; cpu_cores?: number | null;
    variance_reduction?: RouteConfiguration['variance_reduction']; occupancy?: RouteConfiguration['occupancy'];
    source_mixture_fraction?: number; relative_error_target?: number;
  };
  stage?: string;
  preparation?: StudyProgressInput['preparation']; cpu_threads_used?: number;
  position_name?: string; position_number?: number; position_count?: number;
  resources?: { elapsed_seconds: number; time_limit_seconds: number; peak_rss_mb: number; memory_limit_mb: number };
};
const activeStates = new Set(['queued', 'running', 'cancelling', 'pausing']);
const jobLabels: Record<string, string> = {
  'not-started': 'Listo para calcular', queued: 'En espera', running: 'Calculando tasas',
  cancelling: 'Cancelando', completed: 'Cálculo completado', failed: 'Error en el cálculo',
  cancelled: 'Cálculo cancelado', interrupted: 'Cálculo interrumpido',
  pausing: 'Pausa solicitada', paused: 'Estudio pausado',
  'time-limited': 'Límite de tiempo alcanzado', 'memory-limited': 'Límite de RAM alcanzado',
  'disk-limited': 'Espacio libre insuficiente en disco',
};

type Mode = 'total' | 'uniform' | 'by_position' | 'target_dose';
type TargetMetric = 'mean' | 'minimum' | 'maximum';
const targetLabels: Record<TargetMetric, string> = { mean: 'Media de los vóxeles activos', minimum: 'Dosis mínima en la carga', maximum: 'Dosis máxima en la carga' };
type DoseScenario = {
  positions: RatePosition[];
  dose_kgy?: number;
  total_minutes?: number;
  times_minutes?: number[];
  doses_kgy?: number[];
  quantity?: 'rate_sum';
  summed_rate_kgy_h?: number;
  scenario_id?: string;
  target_dose_kgy?: number;
  target_metric?: TargetMetric;
  request?: { start: number; end: number; mode: Mode | 'rate_sum'; value?: number; times_minutes?: number[]; target_metric?: TargetMetric };
  dose_map?: {
    minimum_kgy?: number; maximum_kgy?: number; minimum_kgy_h?: number; maximum_kgy_h?: number;
    relative_error_p90?: number | null; relative_error_p95?: number | null; relative_error_p98?: number | null;
    relative_error_maximum?: number | null; precision?: Precision;
    alignment?: RouteAlignment | null;
  };
};

const number = numberFormatter({ maximumSignificantDigits: 6 });
const uiNumber = numberFormatter({ maximumFractionDigits: 2 });
const format = (value: number) => number.format(value);
const errorPercent = (value: number | null | undefined) => value == null ? '—' : `${format(value * 100)} %`;

export function DoseCalculator({ rates, caseId, caseName, visible, layout, running = false, scenarioRevision = 0 }: { rates: RouteRates; caseId: string; caseName: string; visible: boolean; layout?: StudySettings; running?: boolean; scenarioRevision?: number }) {
  const deterministicAir = rates.analysis_method === 'deterministic_air';
  const calibration = deterministicAir || rates.analysis_method === 'monte_carlo_air';
  const doseTabTitle = calibration ? 'Suma del recorrido' : 'Dosis y tiempos';
  const calibrationId = useId();
  const panelId = calibration ? `calibration-${calibrationId}` : 'study';
  const [startIndex, setStart] = useState(0);
  const [endIndex, setEnd] = useState<number>();
  const { start, end } = availableStudyInterval(rates.positions.length, startIndex, endIndex);
  const followAvailable = startIndex === 0 && endIndex === undefined;
  const studyRefining = running && !!rates.accumulated_error_target && rates.complete === false;
  const [mode, setMode] = useState<Mode>('total');
  const [targetMetric, setTargetMetric] = useState<TargetMetric>('mean');
  const [value, setValue] = useState('');
  const [times, setTimes] = useState<Record<number, string>>({});
  const [calculation, setScenario] = useState<DoseScenario>();
  const [savedScenarios, setSavedScenarios] = useState<DoseScenario[]>([]);
  const fullRoute = start === 0 && end === rates.positions.length - 1;
  const savedRouteSum = calibration && fullRoute ? savedScenarios.find((item) =>
    item.quantity === 'rate_sum' && item.request?.start === 0 && item.request.end === end) : undefined;
  const scenario = calculation ?? savedRouteSum;
  const alignment = scenario?.dose_map?.alignment;
  const combinedPrecision = scenario?.dose_map?.precision;
  const combinedCoverage = !!combinedPrecision?.active_cells
    && combinedPrecision.cells_with_error_estimate === combinedPrecision.active_cells;
  const combinedMaximum = scenario?.dose_map?.relative_error_maximum;
  const intervalErrorTarget = layout?.accumulated_error_target ?? .02;
  const intervalTarget = !combinedCoverage || combinedMaximum == null ? 'Cobertura incompleta'
    : combinedMaximum <= intervalErrorTarget ? 'Cumple' : 'Supera el objetivo';
  const [mapIndex, setMapIndex] = useState<number | undefined>(0);
  const [resultTool, setResultTool] = useState<ResultTool>('volume');
  const [analysisTab, setAnalysisTab] = useState<'scene' | 'position' | 'dose'>(calibration ? 'position' : 'scene');
  const [resultLayout, setResultLayout] = useState<'tabs' | 'stacked'>('tabs');
  const showScene = resultLayout === 'stacked' || analysisTab === 'scene';
  const showPosition = resultLayout === 'stacked' || analysisTab === 'position';
  const showDose = resultLayout === 'stacked' || analysisTab === 'dose';
  useEffect(() => {
    let current = true;
    void Promise.resolve().then(() => {
      try { if (current && localStorage.getItem('irradium:IV:results-layout') === 'stacked') setResultLayout('stacked'); }
      catch { /* Storage may be unavailable in private browser sessions. */ }
    });
    return () => { current = false; };
  }, []);
  function changeResultLayout() {
    const next = resultLayout === 'tabs' ? 'stacked' : 'tabs';
    setResultLayout(next);
    try { localStorage.setItem('irradium:IV:results-layout', next); } catch { /* Keep the current-session choice. */ }
  }
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [integralReferenceDose, setIntegralReferenceDose] = useState('1');
  const interval = rates.positions.slice(start, end + 1);
  const positionTimes = interval.map((_, i) => Number(times[start + i] || 0));
  const validDoseInput = mode === 'by_position'
    ? positionTimes.every((time) => Number.isFinite(time) && time >= 0) && positionTimes.some((time) => time > 0)
    : Number.isFinite(Number(value)) && Number(value) > 0;
  const meanRates = interval.map((point) => point.mean_kgy_h);
  const integralPositions = interval.map((point) => rates.integrated_material_check?.positions.find((row) => row.name === point.name));
  const integralMaterials = integralPositions.length && integralPositions.every(Boolean)
    ? (integralPositions[0]?.materials ?? []).flatMap((material) => {
      const selected = integralPositions.map((point) => point?.materials.find((item) => item.name === material.name));
      if (!selected.every(Boolean)) return [];
      return [{ name: material.name, mass: material.mass_kg,
        rate: selected.reduce((sum, item) => sum + item!.mean_rate_kgy_h, 0),
        deviation: Math.hypot(...selected.map((item) => item!.std_dev_rate_kgy_h)) }];
    }) : [];

  const studyPath = `/api/route/cases/${caseId}/studies/${rates.run_id}`;
  const worstP90 = interval.flatMap((point) => point.relative_error_p90 == null ? [] : [point.relative_error_p90]);
  const worstMaximum = interval.flatMap((point) => point.relative_error_maximum == null ? [] : [point.relative_error_maximum]);
  const initialCount = interval.filter((point) => point.precision?.meets_initial).length;
  const recommendedCount = interval.filter((point) => point.precision?.meets_recommended).length;
  const map = mapIndex === undefined ? undefined : rates.positions[mapIndex];

  useEffect(() => {
    let active = true;
    apiGet<{ scenarios: DoseScenario[] }>(`${studyPath}/scenarios`)
      .then(({ scenarios }) => { if (active) setSavedScenarios(scenarios); })
      .catch((error) => { if (active) setMessage(readableError(error)); });
    return () => { active = false; };
  }, [studyPath, scenarioRevision]);

  function restoreScenario(saved: DoseScenario) {
    const request = saved.request;
    if (!request) return;
    setStart(request.start);
    setEnd(request.end);
    setMode(request.mode === 'rate_sum' ? 'total' : request.mode);
    setTargetMetric(request.target_metric ?? saved.target_metric ?? 'mean');
    setValue(request.value == null ? '' : String(request.value));
    setTimes(Object.fromEntries((request.times_minutes ?? []).map((time, index) => [request.start + index, String(time)])));
    setScenario(saved);
    setMessage('');
  }

  function resetCalculation() { setScenario(undefined); setMessage(''); }

  async function calculate(save: boolean, dose_map = true, sumRates = false) {
    if (!sumRates && !validDoseInput) {
      setMessage('Indica tiempos válidos y al menos una estancia mayor que cero.');
      return;
    }
    if (studyRefining) {
      setMessage('El estudio está ajustando las historias del intervalo. Espera a que termine la verificación.');
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      const requestMode = sumRates ? 'rate_sum' : mode;
      const payload = sumRates ? {} : mode === 'by_position'
        ? { times_minutes: positionTimes }
        : { value: Number(value), ...(mode === 'target_dose' ? { target_metric: targetMetric } : {}) };
      const result = await apiJson<DoseScenario>(`/api/route/cases/${caseId}/dose`, 'POST', { start, end, mode: requestMode, run_id: rates.run_id, save, dose_map, ...payload });
      setScenario(result);
      if (result.scenario_id) {
        setSavedScenarios((current) => [{ ...result, request: { start, end, mode: requestMode, ...payload } }, ...current]);
        setMessage('Cálculo guardado en los resultados del caso.');
      }
    } catch (error) {
      setScenario(undefined);
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {!deterministicAir && rates.dose_tally !== 'phits' && rates.transport_geometry_revision !== 2 && <p className="inline-notice" role="status">Geometría anterior a la corrección del aire exterior. Este estudio se conserva como histórico; su precisión y su tiempo no validan el nuevo modelo con aire y no se usarán para planificarlo.</p>}
      {rates.stop_reason && !running && <p className="inline-notice" role="status">{rates.stop_reason === 'paused' ? 'El estudio está pausado. Los mapas guardados se conservan para continuar.' : rates.stop_reason === 'precision-limited' ? 'El ajuste automático terminó sin alcanzar el objetivo de error del intervalo. Se conservan los mapas calculados.' : rates.stop_reason === 'time-limited' ? 'Este estudio alcanzó el presupuesto de tiempo. Se muestra la última estimación guardada de cada posición disponible.' : 'Este estudio terminó al alcanzar su límite de recursos.'} Comprueba la cobertura y el error antes de usar las dosis.</p>}
      <section className={`surface route-analysis-workbench${resultLayout === 'stacked' ? ' workbench-stacked' : ''}`}>
        <header className="surface-header study-analysis-heading"><div><span className="section-label">Análisis del estudio</span><h3>Tasas del estudio</h3>
          <p className="study-case-context" title={caseName}>{caseName}</p></div>
          <div className="study-meta-pills"><span>{rates.activity_date}</span><span>{format(rates.cell_size_cm ?? 2)} cm</span><span>{deterministicAir ? 'Determinista · Aire' : rates.dose_tally === 'phits' ? 'PHITS' : 'OpenMC'}</span>{rates.complete === false && <span className={running ? 'live-pill' : ''}>{running ? <><i/>{rates.accumulated_error_target ? 'En refinamiento' : 'Calculando'}</> : rates.stop_reason === 'paused' ? 'Resultado parcial · Pausado' : 'Resultado parcial · Detenido'}</span>}
            <button type="button" className="result-layout-toggle" aria-label={resultLayout === 'tabs' ? 'Mostrar resultados juntos' : 'Usar pestañas de resultados'} aria-pressed={resultLayout === 'stacked'} onClick={changeResultLayout}>
              {resultLayout === 'tabs' ? <Rows3 size={13}/> : <PanelsTopLeft size={13}/>}{resultLayout === 'tabs' ? 'Mostrar todo' : 'Usar pestañas'}
            </button>
          </div>
        </header>
        <nav hidden={resultLayout === 'stacked'} className="study-workspace-tabs" aria-label="Vistas de resultados" role="tablist" onKeyDown={(event) => {
          const tabs = ['scene', 'position', 'dose'] as const;
          const index = tabs.indexOf(analysisTab);
          const next = event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length]
            : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length]
              : event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[2] : undefined;
          if (next) { event.preventDefault(); setAnalysisTab(next); document.getElementById(`${panelId}-tab-${next}`)?.focus(); }
        }}>
          <button type="button" role="tab" id={`${panelId}-tab-scene`} aria-controls={`${panelId}-scene-panel`} aria-selected={analysisTab === 'scene'} onClick={() => setAnalysisTab('scene')}><Box size={16}/>Irradiador 3D</button>
          <button type="button" role="tab" id={`${panelId}-tab-position`} aria-controls={`${panelId}-position-panel`} aria-selected={analysisTab === 'position'} onClick={() => setAnalysisTab('position')}><ChartNoAxesCombined size={16}/>Por posición</button>
          <button type="button" role="tab" id={`${panelId}-tab-dose`} aria-controls={`${panelId}-dose-panel`} aria-selected={analysisTab === 'dose'} onClick={() => setAnalysisTab('dose')}><Timer size={16}/>{doseTabTitle}</button>
        </nav>
        {resultLayout === 'stacked' && <nav className="study-section-links" aria-label="Secciones de resultados"><a href={`#${panelId}-scene-panel`}><Box size={14}/>Irradiador 3D</a><a href={`#${panelId}-position-panel`}><ChartNoAxesCombined size={14}/>Por posición</a><a href={`#${panelId}-dose-panel`}><Timer size={14}/>{doseTabTitle}</a></nav>}
        {deterministicAir && <p className="route-precision-note">Respuesta de alanina en una carga llena de aire seco. Referencia de campo libre, sin atenuación, dispersión ni incertidumbre estadística de Monte Carlo.</p>}
        <div className="study-interval-bar" hidden={!showPosition && !showDose}>
        <div className="simulation-form-grid">
          <label className="field"><span>Posición inicial para integrar</span><select value={start} disabled={busy} onChange={(event) => { const next = Number(event.target.value); setStart(next); if (endIndex !== undefined && next > end) setEnd(next); resetCalculation(); }}>{rates.positions.map((point, index) => <option key={point.name} value={index}>{point.name}</option>)}</select></label>
          <ChevronRight className="interval-direction" size={16}/>
          <label className="field"><span>Posición final para integrar</span><select value={end} disabled={busy} onChange={(event) => { setEnd(Number(event.target.value)); resetCalculation(); }}>{rates.positions.map((point, index) => <option key={point.name} value={index} disabled={index < start}>{point.name}</option>)}</select></label>
        </div>
        <label className="checkbox-row"><input type="checkbox" checked={followAvailable} disabled={busy} onChange={(event) => {
          if (event.target.checked) { setStart(0); setEnd(undefined); }
          else setEnd(end);
          resetCalculation();
        }}/>Seguir el intervalo disponible<span className="interval-count">{interval.length} posiciones</span></label>
        <button type="button" className="button ghost" aria-label="Todas las tasas del estudio" disabled={busy} onClick={() => { setStart(0); setEnd(undefined); resetCalculation(); }}>{rates.selected_position_names ? 'Todas las posiciones calculadas' : 'Recorrido completo'}</button>
        </div>
        {rates.accumulated_error_target && <details className="study-precision-details"><summary><ShieldCheck size={15}/><span>Precisión del estudio completo</span><strong>Máximo {errorPercent(rates.interval_precision?.relative_error_maximum)}</strong>
          <span className="precision-percentile">P90 <b>{errorPercent(rates.interval_precision?.relative_error_p90)}</b></span><span className="precision-percentile">P95 <b>{errorPercent(rates.interval_precision?.relative_error_p95)}</b></span><span className="precision-percentile">P98 <b>{errorPercent(rates.interval_precision?.relative_error_p98)}</b></span>
          <span className="precision-goal">Objetivo actual ≤ {errorPercent(intervalErrorTarget)}</span></summary><p className="route-precision-note" role="status">
          Objetivo histórico de esta ejecución ≤ {errorPercent(rates.accumulated_error_target)}
          {rates.interval_precision && ` · Máximo acumulado ${errorPercent(rates.interval_precision.relative_error_maximum)} · ${rates.interval_precision.meets_target ? 'Cumple' : studyRefining ? 'Ajustando' : 'No alcanzado'} · ${rates.interval_precision.passes} ${rates.interval_precision.passes === 1 ? 'pasada' : 'pasadas'}`}
          {rates.interval_precision && <><br/>P90 {errorPercent(rates.interval_precision.relative_error_p90)} · P95 {errorPercent(rates.interval_precision.relative_error_p95)} · P98 {errorPercent(rates.interval_precision.relative_error_p98)}. Corresponde al intervalo completo calculado con igual peso por posición.</>}
          {' '}Los intervalos que selecciones para integrar se verifican con sus propios mapas frente al límite actual de {errorPercent(intervalErrorTarget)} máximo en todos los vóxeles activos.
        </p></details>}
        <div hidden={!showScene} id={`${panelId}-scene-panel`} role={resultLayout === 'tabs' ? 'tabpanel' : 'region'} aria-label="Irradiador 3D" className="study-scene-panel">
          <StudyPositionOverview deterministicAir={deterministicAir} positions={rates.positions} studyPath={studyPath} active={visible && showScene} occupancy={rates.occupancy} requestedCount={rates.requested_position_count ?? rates.positions.length} layout={layout} selected={mapIndex} onSelect={(index) => { setMapIndex(index); setAnalysisTab('position'); }}/>
        </div>
        <div hidden={!showPosition} id={`${panelId}-position-panel`} role={resultLayout === 'tabs' ? 'tabpanel' : 'region'} aria-label="Por posición" className="study-position-panel">
        {resultLayout === 'stacked' && <h3 className="continuous-section-heading"><ChartNoAxesCombined size={18}/>Análisis por posición</h3>}
        <div className="metric-grid route-metrics">
          <article><span>Posiciones seleccionadas</span><strong>{interval.length} de {rates.positions.length}</strong></article>
          <article><span>Rango de tasas medias [kGy/h]</span><strong>{format(Math.min(...meanRates))} – {format(Math.max(...meanRates))}</strong></article>
          {!deterministicAir && <article><span>Mayor error individual por posición</span><strong>{worstMaximum.length ? errorPercent(Math.max(...worstMaximum)) : '—'}</strong><small>P90 · {worstP90.length ? errorPercent(Math.max(...worstP90)) : '—'}</small></article>}
        </div>
        {!deterministicAir && <p className="route-precision-note" role="status">{initialCount}/{interval.length} posiciones con todos los vóxeles activos por debajo del 10 % · {recommendedCount}/{interval.length} por debajo del 5 %. Estos errores individuales no son el error de la superposición; consulta el mapa acumulado con el reparto de tiempos elegido.</p>}
        <RouteRateChart deterministic={deterministicAir} points={interval} selected={mapIndex == null ? undefined : mapIndex - start} onSelect={(index) => setMapIndex(start + index)}/>
        <details className="route-rate-table"><summary>{deterministicAir ? 'Tasas' : 'Tasas y precisión'} de las {interval.length} posiciones · Mostrar tabla</summary>
        <div className="data-table-wrap">
          <table>
            <thead><tr><th>Posición</th><th>Tasa media [kGy/h]</th><th>Tasa mínima – máxima [kGy/h]</th>{!deterministicAir && <><th>Error relativo P90</th><th>Error máximo</th><th>Precisión</th><th>Historias</th></>}<th>Distribución</th></tr></thead>
            <tbody>{interval.map((point, index) => <tr key={point.name}>
              <td>{point.name}</td><td>{format(point.mean_kgy_h)}</td>
              <td>{format(point.minimum_kgy_h)} – {format(point.maximum_kgy_h)}</td>
              {!deterministicAir && <><td>{errorPercent(point.relative_error_p90)}</td>
              <td>{errorPercent(point.relative_error_maximum)}</td>
              <td><span className={`status-chip ${point.precision?.meets_recommended ? 'success' : point.precision?.meets_initial ? 'precision-initial' : 'precision-insufficient'}`}>{precisionLabels[point.precision?.state ?? 'insufficient']}</span>
                {point.precision && <small className="route-coverage">{point.precision.cells_with_error_estimate}/{point.precision.active_cells} vóxeles con estimación</small>}
              </td>
              <td>{point.simulated_particles == null ? '—' : format(point.simulated_particles)}</td></>}
              <td><button type="button" className="button secondary route-map-button" aria-label={`Ver mapa de ${point.name}`} onClick={() => setMapIndex(start + index)}>Ver mapa</button></td>
            </tr>)}</tbody>
          </table>
        </div></details>
        <div className="study-position-details">
          <header className="surface-header"><div><h3>{map ? `Mapa de tasa · ${map.name}` : 'Mapa de tasa por posición'}</h3>
            <p>{map ? `Ejes del irradiador, con el origen en el centro de la carga. Referencia [mm] · X ${format(map.center_mm[0])} · Y ${format(map.center_mm[1])} · Z ${format(map.center_mm[2])}.`
              : 'Selecciona una posición en el recorrido para consultar su distribución.'}</p></div>
            <label className="field"><span>Posición visualizada</span><select value={mapIndex ?? ''} onChange={(event) => setMapIndex(event.target.value === '' ? undefined : Number(event.target.value))}>
              <option value="">Seleccionar posición</option>
              {rates.positions.map((point, index) => <option key={point.name} value={index}>{point.name}</option>)}
            </select></label>
          </header>
          {map && visible && showPosition && <RouteAnalysisView deterministicAir={deterministicAir} key={`${map.name}-${map.summary ?? map.simulated_particles}`} path={`${studyPath}/positions/${mapIndex}/analysis`} caseId={caseId} title={`Tasa absorbida · ${map.name}`} materials={map.materials} positionYaw={map.yaw_degrees} tool={resultTool} onToolChange={setResultTool}/>}
          {!!map?.material_statistics?.length && <div className="data-table-wrap"><table>
            <thead><tr><th>Material</th><th>Tasa media [kGy/h]</th><th>Tasa mínima – máxima [kGy/h]</th><th>Error relativo P90</th></tr></thead>
            <tbody>{map.material_statistics.map((material) => <tr key={material.id}><td>{material.name}</td><td>{format(material.average_dose)}</td>
              <td>{format(material.minimum_dose)} – {format(material.maximum_dose)}</td><td>{errorPercent(material.relative_error_p90)}</td></tr>)}</tbody>
          </table></div>}
          {!!map?.material_statistics?.length && <p className="route-precision-note">
            Estadísticas agrupadas por el material dominante en los vóxeles activos (ocupación de carga ≥ 50 %).
            {!!map.materials?.some((material) => !map.material_statistics?.some((statistic) => statistic.name === material.name)) && <>
              {' '}Sin vóxeles activos dominantes: {map.materials.filter((material) => !map.material_statistics?.some((statistic) => statistic.name === material.name)).map((material) => material.name).join(', ')}.
              {' '}Estos materiales siguen incluidos en el transporte; esta malla no ofrece una estadística espacial separada de sus piezas delgadas. Usa una malla más fina para estudiarlas.
            </>}
          </p>}
        </div>
        </div>
      </section>
      <div hidden={!showDose} id={`${panelId}-dose-panel`} role={resultLayout === 'tabs' ? 'tabpanel' : 'region'} aria-label={doseTabTitle} className="study-dose-panel">
      {integralMaterials.length > 0 && <details className="surface study-integral-details">
        <summary><ShieldCheck size={16}/>Control integral por material<span>Media física, masa e incertidumbre</span><ChevronRight size={15}/></summary>
        <header className="surface-header"><div><span className="section-label">Control independiente · Media del material</span><h3>Dosis media y tiempo por material</h3>
          <p>Calculado a partir de la energía total absorbida por cada material. Evalúa la media en su masa completa; no certifica la dosis mínima, máxima ni el error de los vóxeles de 5 mm.</p></div>
          <label className="field"><span>Dosis media de referencia [kGy]</span><input type="number" min={0} step="any" value={integralReferenceDose} onChange={(event) => setIntegralReferenceDose(event.target.value)}/><small>Referencia numérica para comparar tiempos.</small></label>
        </header>
        <p className="route-precision-note">{interval[0]?.name} → {interval.at(-1)?.name} · {interval.length} posiciones con igual tiempo de estancia. El tiempo total no incluye traslados.</p>
        <div className="data-table-wrap"><table><thead><tr><th>Material</th><th>Masa [kg]</th><th>Suma de tasas medias [kGy/h]</th><th>Error de la suma de medias</th><th>Tiempo por posición [min]</th><th>Tiempo total [min]</th></tr></thead>
          <tbody>{integralMaterials.map((material) => {
            const minutes = Number(integralReferenceDose) > 0 && material.rate > 0 ? 60 * Number(integralReferenceDose) / material.rate : null;
            return <tr key={material.name}><td>{material.name}</td><td>{format(material.mass)}</td><td>{format(material.rate)}</td><td>{material.rate > 0 ? errorPercent(material.deviation / material.rate) : 'Sin estimación'}</td><td>{minutes == null ? '—' : format(minutes)}</td><td>{minutes == null ? '—' : format(minutes * interval.length)}</td></tr>;
          })}</tbody></table></div>
      </details>}
      <form className="surface" onSubmit={(event) => { event.preventDefault(); void calculate(false); }}>
        <header className="surface-header"><div><span className="section-label">03 · Integrar</span><h3>{deterministicAir ? 'Suma de tasas y dosis de referencia' : 'Dosis acumulada y tiempo de irradiación'}</h3><p>La superposición alinea los mismos puntos físicos de la carga a lo largo del recorrido. Para la dosis acumulada, cada tasa se multiplica por su tiempo de estancia.</p></div></header>
        {studyRefining && <p className="inline-notice study-pending-note" role="status">El estudio sigue refinando su precisión. Puedes preparar el intervalo y los valores; los cálculos de dosis y tiempo se habilitarán al finalizar.</p>}
        {rates.accumulated_error_target && !studyRefining && !rates.interval_precision?.meets_target && <p className="inline-notice" role="alert">Esta ejecución no alcanzó su objetivo histórico. La aceptación actual exige un error máximo acumulado ≤ {errorPercent(intervalErrorTarget)} con cobertura completa. Revisa el mapa del intervalo y los tiempos seleccionados.</p>}
        <p className="route-precision-note">{deterministicAir
          ? 'La suma del recorrido se guarda automáticamente. Selecciona otro intervalo si deseas sumar solo algunas posiciones.'
          : '1. Selecciona el intervalo arriba. 2. Introduce una dosis objetivo o un tiempo. 3. Calcula y revisa el mapa y su precisión. Cada cálculo se guarda en este estudio.'}</p>
        <div className="simulation-form-grid">
          {savedScenarios.length > 0 && <label className="field"><span>Cálculos guardados</span><select value={scenario?.scenario_id ?? ''} disabled={busy} onChange={(event) => {
            const saved = savedScenarios.find((item) => item.scenario_id === event.target.value);
            if (saved) restoreScenario(saved); else resetCalculation();
          }}>
            <option value="">Nuevo cálculo</option>
            {savedScenarios.map((saved, index) => <option key={saved.scenario_id} value={saved.scenario_id}>{index + 1}. {saved.quantity === 'rate_sum' ? `Suma de tasas · ${format(saved.summed_rate_kgy_h ?? 0)} kGy/h` : `${saved.target_dose_kgy != null ? `${targetLabels[saved.target_metric ?? 'mean']} objetivo ${format(saved.target_dose_kgy)} kGy · ` : ''}Media ${format(saved.dose_kgy ?? 0)} kGy · ${format(saved.total_minutes ?? 0)} min`}{saved.dose_map ? ' · mapa' : ''}</option>)}
          </select></label>}
          <label className="field"><span>Dato de entrada</span><select value={mode} disabled={busy} onChange={(event) => { setMode(event.target.value as Mode); setValue(''); resetCalculation(); }}>
            <option value="total">Tiempo total del intervalo</option>
            <option value="uniform">Mismo tiempo en cada posición</option>
            <option value="by_position">Tiempo diferente por posición</option>
            <option value="target_dose">Dosis objetivo → calcular tiempos</option>
          </select></label>
          {mode === 'target_dose' && <label className="field"><span>Criterio de dosis objetivo</span><select value={targetMetric} disabled={busy} onChange={(event) => { setTargetMetric(event.target.value as TargetMetric); resetCalculation(); }}>
            {Object.entries(targetLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select><small>{targetMetric === 'minimum' ? 'Ajusta el tiempo para que el mínimo estimado del mapa alcance el objetivo.' : targetMetric === 'maximum' ? 'Ajusta el tiempo para que el máximo estimado del mapa alcance el objetivo.' : 'Promedia los vóxeles activos, sin ponderación por masa. Para la media física de un material consulta el control integral.'} Se conserva el mismo tiempo en todas las posiciones.</small></label>}
          {mode !== 'by_position' && <label className="field"><span>{mode === 'target_dose' ? 'Dosis objetivo [kGy]' : mode === 'total' ? 'Tiempo total [min]' : 'Tiempo por posición [min]'}</span>
            <input type="number" required min={0} step="any" disabled={busy} value={value} onChange={(event) => { setValue(event.target.value); resetCalculation(); }}/>
            {['total', 'target_dose'].includes(mode) && <small>El tiempo total se reparte por igual entre las posiciones seleccionadas.</small>}
          </label>}
          {mode === 'by_position' && interval.map((point, i) => (
            <label className="field" key={point.name}><span>{point.name} [min]</span><input type="number" min={0} step="any" disabled={busy} value={times[start + i] ?? ''} placeholder="0" onChange={(event) => {
              const minutes = event.target.value;
              setTimes((current) => ({ ...current, [start + i]: minutes }));
              resetCalculation();
            }}/></label>
          ))}
          {mode === 'by_position' && <p className="field-hint">Los campos vacíos equivalen a 0 min. Indica un tiempo mayor que cero en al menos una posición.</p>}
        </div>
        <div className="route-action-row">
          <button className="button primary" disabled={busy || studyRefining || !validDoseInput}>{busy ? 'Calculando mapa…' : mode === 'target_dose' ? 'Calcular tiempos y mapa' : 'Calcular dosis y mapa'}</button>
          <button type="button" className="button secondary" disabled={busy || studyRefining} onClick={() => void calculate(true, true, true)}>Sumar tasas del intervalo</button>
          {scenario && <button type="button" className="button secondary" disabled={busy || !!scenario.scenario_id} onClick={() => void calculate(true)}>Guardar cálculo</button>}
          {scenario && !scenario.dose_map && <button type="button" className="button secondary" disabled={busy} onClick={() => void calculate(true, true)}>Generar mapa acumulado</button>}
          {scenario?.dose_map && <a className="button secondary" href={`${API_URL}/api/route/cases/${caseId}/dose-maps/${scenario.scenario_id}`}>Descargar mapa VTI</a>}
        </div>
        {message && <p className="inline-notice" role="status">{message}</p>}
        {scenario && (
          <div className="route-scenario">
            <div className="metric-grid">
              {scenario.quantity === 'rate_sum' ? <article><span>Suma de tasas medias</span><strong>{format(scenario.summed_rate_kgy_h ?? 0)} kGy/h</strong>{deterministicAir && <small>{scenario.positions[0]?.name} → {scenario.positions.at(-1)?.name} · {scenario.positions.length} posiciones</small>}</article>
                : <><article><span>Dosis media de los vóxeles activos</span><strong>{format(scenario.dose_kgy ?? 0)} kGy</strong></article><article><span>Tiempo total de estancia</span><strong>{format(scenario.total_minutes ?? 0)} min</strong><small>{format((scenario.total_minutes ?? 0) / 60)} h · {interval.length} posiciones</small></article>
                  {scenario.times_minutes?.length && scenario.times_minutes.every((time) => Math.abs(time - scenario.times_minutes![0]) < 1e-9)
                    ? <article><span>Tiempo por posición</span><strong>{format(scenario.times_minutes[0])} min</strong><small>{format(scenario.times_minutes[0] * 60)} s por posición</small></article> : null}</>}
              {scenario.dose_map?.precision && <article><span>Error relativo máximo del mapa</span><strong>{errorPercent(scenario.dose_map.relative_error_maximum)}</strong>
                <small>P90 {errorPercent(scenario.dose_map.relative_error_p90)} · P95 {errorPercent(scenario.dose_map.relative_error_p95)} · P98 {errorPercent(scenario.dose_map.relative_error_p98)}</small>
                <small>{scenario.dose_map.precision.cells_with_error_estimate}/{scenario.dose_map.precision.active_cells} vóxeles con estimación</small>
                <small>Objetivo del intervalo ≤ {errorPercent(intervalErrorTarget)} · {intervalTarget}</small>
              </article>}
            </div>
            {scenario.target_dose_kgy != null && <p><strong>{targetLabels[scenario.target_metric ?? 'mean']} objetivo · {format(scenario.target_dose_kgy)} kGy.</strong> El tiempo se obtiene del mapa superpuesto considerando los giros del recorrido; el objetivo usa la estimación de dosis, sin margen adicional por incertidumbre.</p>}
            {scenario.quantity !== 'rate_sum' && <p className="route-precision-note">El tiempo total suma las estancias de las posiciones seleccionadas. No incluye traslados, elevación ni descarga. Puedes cambiar los tiempos y recalcular a partir de los mapas guardados.</p>}
            {scenario.dose_map && <>
              {scenario.quantity === 'rate_sum'
                ? <p>Suma de tasas por vóxel · {format(scenario.dose_map.minimum_kgy_h ?? 0)}–{format(scenario.dose_map.maximum_kgy_h ?? 0)} kGy/h. Cada posición aporta su tasa con el mismo peso.</p>
                : <p>Dosis acumulada por vóxel · {format(scenario.dose_map.minimum_kgy ?? 0)}–{format(scenario.dose_map.maximum_kgy ?? 0)} kGy.</p>}
              {alignment && <p>
                <strong>Orientación del mapa · {alignment.reference_position} ({format(alignment.reference_yaw_degrees)}°)</strong>
                {alignment.turns.length
                  ? <> · Giros considerados · {alignment.turns.map(({ from_position, to_position, yaw_degrees }) =>
                    `${from_position} → ${to_position} (${format(Math.abs(yaw_degrees) === 180 ? 180 : yaw_degrees)}°)`).join(' · ')}</>
                  : ' · El tramo mantiene la misma orientación.'}
              </p>}
              {visible && showDose && scenario.quantity === 'rate_sum' && !!alignment?.turns.length && <RouteSuperpositionCheck
                key={`superposition-${scenario.scenario_id}`} alignment={alignment} positions={rates.positions} interval={interval}
                studyPath={studyPath} sumPath={`/api/route/cases/${caseId}/dose-maps/${scenario.scenario_id}`}/>}
              {visible && showDose && <RouteAnalysisView deterministicAir={deterministicAir} key={scenario.scenario_id} path={`/api/route/cases/${caseId}/scenarios/${scenario.scenario_id}/analysis`} caseId={caseId} title={scenario.quantity === 'rate_sum' ? 'Suma de tasas' : 'Dosis acumulada'} accumulated={scenario.quantity !== 'rate_sum'} positionYaw={alignment?.reference_yaw_degrees} materials={interval[0]?.materials}/>}
            </>}
            {scenario.times_minutes && scenario.doses_kgy && <div className="data-table-wrap"><table>
              <thead><tr><th>Posición</th><th>Tiempo [min]</th><th>Aporte a la dosis media [kGy]</th></tr></thead>
              <tbody>{interval.map((point, i) => <tr key={point.name}><td>{point.name}</td><td>{format(scenario.times_minutes?.[i] ?? 0)}</td><td>{format(scenario.doses_kgy?.[i] ?? 0)}</td></tr>)}</tbody>
            </table></div>}
          </div>
        )}
      </form>
      </div>
    </>
  );
}

function StudyErrorNotice({ error }: { error: string }) {
  const geometry = /TETRA (INTERSECTION|GEOMETRY) ERROR|error geométrico/i.test(error);
  return <aside className="inline-notice study-error-notice" role="alert">
    <strong>{geometry ? 'PHITS detectó una geometría incompatible' : 'El cálculo se interrumpió'}</strong>
    <p>{geometry ? 'La comprobación encontró tetraedros superpuestos. Revisa la conversión de la geometría antes de volver a ejecutar el estudio.' : error.split('\n')[0]}</p>
    <details><summary>Ver diagnóstico técnico</summary><pre>{error}</pre></details>
  </aside>;
}

function CaseStudy({ caseId, caseName, revision, visible, airReference = false }: { caseId: string; caseName: string; revision: number; visible: boolean; airReference?: boolean }) {
  const [settings, setSettings] = useState<StudySettings>();
  const [date, setDate] = useState('');
  const [scope, setScope] = useState<'all' | 'interval' | 'selected'>('all');
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [quick, setQuick] = useState(false);
  const [target, setTarget] = useState(.05);
  const [histories, setHistories] = useState(100_000_000);
  const [budgetReference, setBudgetReference] = useState<string>();
  const [accumulatedTarget, setAccumulatedTarget] = useState(.02);
  const [intervalMaximum, setIntervalMaximum] = useState(300_000_000);
  const [cellSize, setCellSize] = useState(2);
  const [variance, setVariance] = useState<RouteConfiguration['variance_reduction']>('analog');
  const [occupancy, setOccupancy] = useState<RouteConfiguration['occupancy']>('isolated');
  const [adaptive, setAdaptive] = useState(false);
  const [accumulated, setAccumulated] = useState(false);
  const [mixtureFraction, setMixtureFraction] = useState(.9);
  const [minutesLimit, setMinutesLimit] = useState(15);
  const [extendedTime, setExtendedTime] = useState(false);
  const [cpuCores, setCpuCores] = useState<number>();
  const [memoryLimit, setMemoryLimit] = useState(4096);
  const [expanded, setExpanded] = useState(true);
  const [plan, setPlan] = useState<ResourcePlan>();
  const [planError, setPlanError] = useState('');
  const [job, setJob] = useState<RouteJob>();
  const [studies, setStudies] = useState<Study[]>([]);
  const [runId, setRunId] = useState('');
  const [followingCurrent, setFollowingCurrent] = useState(true);
  const [rates, setRates] = useState<RouteRates>();
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [configurationAttempt, setConfigurationAttempt] = useState(0);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const pendingRun = useRef<string | undefined>(undefined);
  const manualRun = useRef<string | undefined>(undefined);
  const routePath = `/api/route/cases/${caseId}`;
  const jobPath = `/api/cases/${caseId}/monte-carlo`;
  const active = !!job && activeStates.has(job.state);
  const selectionValid = scope !== 'selected' || selectedNames.length > 0;
  const positionCount = scope === 'selected' ? selectedNames.length : scope === 'interval' ? end - start + 1 : settings?.positions.length ?? 0;
  const displayedHistories = active && job?.route_study?.particles != null
    ? job.route_study.particles : accumulated ? intervalMaximum : histories;
  const extendedMinutes = (settings?.extended_time_limit_seconds ?? 86400) / 60;
  const selectedStudy = studies.find((study) => study.run_id === runId);
  const hasPublishedStudy = Boolean(selectedStudy);
  const selectedComplete = selectedStudy?.complete;
  const selectedVersion = JSON.stringify(selectedStudy ?? null);
  const currentStudy = studies.find((study) => study.run_id === job?.run_id);
  const publishedCount = currentStudy?.count ?? job?.saved_position_count ?? 0;
  const requestedCount = currentStudy?.requested_position_count ?? requestedPositionCount(job?.route_study) ?? job?.position_count ?? settings?.positions.length ?? 0;
  const refining = !!currentStudy?.accumulated_error_target && publishedCount > 0 && publishedCount === requestedCount;
  const budgetGuide = budgetReference ?? (rates && rates.complete !== false && !rates.quick && rates.transport_geometry_revision === 2
    && rates.cell_size_cm === cellSize && rates.occupancy === occupancy
    && rates.variance_reduction === variance && rates.source_mixture_fraction === mixtureFraction
    ? rates.run_id : undefined);

  // The persisted request is authoritative while a run is active or resumable.
  // Case defaults may differ from the settings entered just before submission.
  const loadedSettings = useRef<StudySettings | undefined>(undefined);
  const latestJob = useRef<RouteJob | undefined>(undefined);
  const restoredRequest = useRef('');
  const restoreRequest = useCallback((status: RouteJob) => {
    const settings = loadedSettings.current;
    if (!settings || !(activeStates.has(status.state) || status.can_resume) || !status.route_study) return;
    const key = JSON.stringify([status.run_id, status.route_study]);
    if (restoredRequest.current === key) return;
    restoredRequest.current = key;
    const request = status.route_study;
    if (request.irradiation_date) setDate(request.irradiation_date);
    const first = request.start ?? 0;
    const last = request.end ?? settings.positions.length - 1;
    setStart(first); setEnd(last);
    setSelectedNames(request.position_names ?? []);
    setScope(request.position_names ? 'selected' : first === 0 && last === settings.positions.length - 1 ? 'all' : 'interval');
    setQuick(Boolean(request.quick));
    setAdaptive(Boolean(request.adaptive_statistics));
    setAccumulated(Boolean(request.accumulated_error_target));
    if (request.accumulated_error_target != null) setAccumulatedTarget(request.accumulated_error_target);
    setBudgetReference(request.budget_reference_run_id ?? undefined);
    if (request.particles != null) {
      setHistories(request.particles);
      if (request.accumulated_error_target != null) setIntervalMaximum(request.particles);
    }
    if (request.cell_size_cm != null) setCellSize(request.cell_size_cm);
    if (request.memory_limit_mb != null) setMemoryLimit(request.memory_limit_mb);
    if (request.time_limit_seconds != null) setMinutesLimit(request.time_limit_seconds / 60);
    setExtendedTime(request.extended_time ?? (request.time_limit_seconds ?? 900) > 900);
    setCpuCores(request.cpu_cores ?? undefined);
    if (request.variance_reduction) setVariance(request.variance_reduction);
    if (request.occupancy) setOccupancy(request.occupancy);
    if (request.source_mixture_fraction != null) setMixtureFraction(request.source_mixture_fraction);
    if (request.relative_error_target != null) setTarget(request.relative_error_target);
  }, []);


  useEffect(() => {
    let current = true;
    apiGet<StudySettings>(`${routePath}/study`).then((data) => {
      if (!current) return;
      setSettings(data);
      setDate(data.today);
      setHistories(data.statistical_refinement_enabled ? data.particles : data.fixed_particles ?? data.particles);
      setAccumulatedTarget(data.accumulated_error_target ?? .02);
      setIntervalMaximum(data.interval_max_particles ?? 300_000_000);
      setMinutesLimit(Math.ceil((data.time_limit_seconds ?? 18000) / 60));
      setExtendedTime((data.time_limit_seconds ?? 18000) > 900);
      setAccumulated(Boolean(data.statistical_refinement_enabled) && data.dose_tally !== 'phits');
      setCellSize(data.cell_size_cm);
      setMemoryLimit(data.memory_recommended_mb ?? 4096);
      setVariance(data.variance_reduction);
      setOccupancy(data.occupancy);
      setAdaptive(Boolean(data.statistical_refinement_enabled) && (data.adaptive_statistics ?? false));
      setMixtureFraction(data.source_mixture_fraction ?? .5);
      setTarget(data.relative_error_target > .05 ? .10 : .05);
      setStart(0);
      setEnd(data.positions.length - 1);
      setSelectedNames(data.calculation_positions ?? []);
      setScope(data.calculation_positions?.length ? 'selected' : 'all');
      loadedSettings.current = data;
      restoredRequest.current = '';
      if (latestJob.current) restoreRequest(latestJob.current);
    }).catch((error) => { if (current) setError(readableError(error)); });
    return () => { current = false; };
  }, [routePath, configurationAttempt, restoreRequest]);


  useEffect(() => {
    if (!visible) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    let lastPosition: number | undefined;
    let lastArchiveCheck = 0;
    async function poll(checkArchive: boolean) {
      try {
        const status = await apiGet<RouteJob>(jobPath);
        if (!current) return;
        latestJob.current = status;
        restoreRequest(status);
        setJob(status);
        const running = activeStates.has(status.state);
        const positionChanged = status.position_number !== lastPosition;
        lastPosition = status.position_number;
        if (checkArchive || !running || positionChanged || Date.now() - lastArchiveCheck >= 3000) {
          const { studies } = await apiGet<{ studies: Study[] }>(`${routePath}/studies`);
          if (!current) return;
          lastArchiveCheck = Date.now();
          setStudies(studies);
          const nextRun = selectStudyRun(studies, manualRun.current, pendingRun.current, status.run_id);
          if (!running) pendingRun.current = undefined;
          setRunId(nextRun);
        }
        if (current) timer = setTimeout(() => void poll(false), running ? 1250 : 5000);
      } catch (error) {
        if (current) {
          setError(readableError(error));
          timer = setTimeout(() => void poll(checkArchive), 3000);
        }
      }
    }
    void poll(true);
    return () => { current = false; clearTimeout(timer); };
  }, [jobPath, routePath, refresh, revision, visible, restoreRequest]);

  useEffect(() => {
    if (!runId) return;
    // A run exists before its first position map is published. Its archive is
    // created with that first map; a missing archive here is normal preparation.
    if (!hasPublishedStudy && (pendingRun.current === runId || (active && job?.run_id === runId))) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    function loadRates() {
      void apiGet<RouteRates>(`${routePath}/results?run_id=${encodeURIComponent(runId)}`)
      .then((result) => {
        if (!current) return;
        if (result.case_id !== caseId || result.run_id !== runId) {
          setRates(undefined);
          setError('Los resultados recibidos no corresponden al caso y al estudio seleccionados. Actualiza los estudios.');
          return;
        }
        setRates(result);
        setError('');
        setExpanded(false);
      })
      .catch((error) => {
        if (current) {
          setError(readableError(error));
          timer = setTimeout(loadRates, 3000);
        }
      });
    }
    loadRates();
    return () => { current = false; clearTimeout(timer); };
  }, [caseId, routePath, runId, selectedVersion, hasPublishedStudy, revision, refresh, active, job?.run_id]);

  useEffect(() => {
    if (!settings || !(cellSize > 0) || !selectionValid) return;
    let current = true;
    const timer = setTimeout(() => {
      apiJson<ResourcePlan>(`${routePath}/estimate`, 'POST', {
        start: scope === 'interval' ? start : 0, end: scope === 'interval' ? end : settings.positions.length - 1,
        position_names: scope === 'selected' ? selectedNames : undefined,
        cell_size_cm: cellSize, memory_limit_mb: memoryLimit, occupancy, variance_reduction: variance,
        source_mixture_fraction: mixtureFraction, cpu_cores: cpuCores,
        accumulated_error_target: accumulated ? accumulatedTarget : null, adaptive_statistics: false,
      }).then((data) => { if (current) { setPlan(data); setPlanError(''); } })
        .catch((error) => { if (current) { setPlan(undefined); setPlanError(readableError(error)); } });
    }, 300);
    return () => { current = false; clearTimeout(timer); };
  }, [cellSize, memoryLimit, routePath, settings, scope, start, end, selectedNames, selectionValid, occupancy, variance, mixtureFraction, cpuCores, accumulated, accumulatedTarget, selectedComplete]);

  async function changeJob(cancel: boolean) {
    if (!settings || (!cancel && !selectionValid)) return;
    setBusy(true);
    setActionError('');
    try {
      const next = cancel
        ? await apiJson<RouteJob>(`${jobPath}/cancel`, 'POST')
        : await apiJson<RouteJob>(`${routePath}/study`, 'POST', {
          start: scope === 'interval' ? start : 0,
          end: scope === 'interval' ? end : settings.positions.length - 1,
          position_names: scope === 'selected' ? selectedNames : undefined,
          irradiation_date: date,
          quick, particles: accumulated ? intervalMaximum : adaptive && settings?.dose_tally !== 'phits' ? 1e12 : histories,
          adaptive_statistics: adaptive && !accumulated, accumulated_error_target: accumulated ? accumulatedTarget : null,
          budget_reference_run_id: accumulated ? budgetGuide : undefined,
          cell_size_cm: cellSize, relative_error_target: accumulated ? accumulatedTarget : target, variance_reduction: variance,
          source_mixture_fraction: mixtureFraction, occupancy, cpu_cores: cpuCores,
          time_limit_seconds: minutesLimit * 60, extended_time: extendedTime, memory_limit_mb: memoryLimit,
        });
      setJob(next);
      if (!cancel) {
        pendingRun.current = next.run_id;
        manualRun.current = undefined;
        setFollowingCurrent(true);
        setRunId(next.run_id ?? '');
        setExpanded(false);
      }
      setRefresh((version) => version + 1);
    } catch (error) {
      setActionError(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  async function controlStudy(action: 'pause' | 'resume') {
    if (!job?.run_id) return;
    setBusy(true);
    setActionError('');
    try {
      const next = await apiJson<RouteJob>(`${jobPath}/${action}`, 'POST', action === 'resume' ? { run_id: job.run_id } : {});
      setJob(next);
      manualRun.current = undefined;
      pendingRun.current = next.run_id;
      setFollowingCurrent(true);
      setRunId(next.run_id ?? '');
      setRefresh((value) => value + 1);
    } catch (error) {
      setActionError(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  function reuseStudy() {
    if (!rates || !settings) return;
    const range = rates.score_interval ?? [0, rates.positions.length - 1];
    const first = Math.max(0, Math.min(settings.positions.length - 1, range[0]));
    const last = Math.max(first, Math.min(settings.positions.length - 1, range[1]));
    setStart(first); setEnd(last);
    setSelectedNames(rates.selected_position_names ?? []);
    setScope(rates.selected_position_names ? 'selected' : first === 0 && last === settings.positions.length - 1 ? 'all' : 'interval');
    setDate(rates.activity_date);
    setQuick(Boolean(rates.quick));
    setAdaptive(Boolean(settings.statistical_refinement_enabled) && (rates.adaptive_statistics ?? false));
    setAccumulated(Boolean(settings.statistical_refinement_enabled && rates.accumulated_error_target));
    setAccumulatedTarget(settings.accumulated_error_target ?? .02);
    setBudgetReference(rates.complete === false || rates.quick || rates.transport_geometry_revision !== 2 ? undefined : rates.run_id);
    setCellSize(rates.cell_size_cm ?? 2);
    const historicalBudget = (!settings.statistical_refinement_enabled && rates.accumulated_error_target ? undefined : rates.requested_particles_per_position)
      ?? Math.max(...rates.positions.map((point) => point.simulated_particles ?? settings.particles));
    setHistories(Math.max(settings.batches, historicalBudget));
    if (rates.accumulated_error_target) setIntervalMaximum(Math.max(settings.batches, historicalBudget));
    setVariance(rates.variance_reduction ?? settings.variance_reduction);
    setOccupancy(rates.occupancy ?? settings.occupancy);
    setCpuCores(rates.cpu_cores && rates.cpu_cores > 0 ? rates.cpu_cores : undefined);
    setMixtureFraction(rates.transport_geometry_revision === 2 ? rates.source_mixture_fraction ?? .5 : .5);
    setTarget((rates.relative_error_target ?? .05) > .05 ? .10 : .05);
    if (rates.resources) {
      const minutes = Math.ceil(rates.resources.time_limit_seconds / 60);
      setExtendedTime(Boolean(rates.extended_time || minutes > 15));
      setMinutesLimit(Math.max(1, Math.min(extendedMinutes, minutes)));
      setMemoryLimit(Math.floor(rates.resources.memory_limit_mb));
    }
    setExpanded(true);
    setActionError('');
    document.querySelector('.route-study-summary')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  return <>
    {error && <div className="inline-notice" role="status">{error} <button type="button" className="button secondary" onClick={() => { setError(''); setConfigurationAttempt((value) => value + 1); setRefresh((value) => value + 1); }}>Reintentar conexión</button></div>}
    {airReference && <AirDoseMethodNote/>}
    {job && (active || job.can_resume) && <section className="surface route-live-status" aria-label="Seguimiento del estudio en curso">
      <div className="live-status-identity"><span className="live-status-orb">{active ? <Activity size={19}/> : <Pause size={19}/>}</span><div><span className="live-status-eyebrow"><i/>{jobLabels[job.state]}</span><h3>{!active ? job.state === 'paused' ? 'Simulación pausada' : 'Simulación interrumpida' : job.state === 'pausing' ? 'Terminando la posición actual' : job.stage === 'finalizing-study' ? 'Guardando resultados' : refining ? 'Refinando la precisión' : 'Simulación en curso'}</h3><p>{!active ? `${publishedCount} posiciones guardadas` : job.position_name ? `Posición ${job.position_name}` : 'Preparando el estudio'}<span> · {job.run_id?.slice(0, 8)}</span></p></div></div>
      <details className="study-resource-details"><summary>Recursos y precisión</summary><div className="live-status-metrics">
        <div className="live-kpi"><span><Layers3 size={13}/>Mapas disponibles</span><strong>{publishedCount}<small> / {requestedCount}</small></strong><small>{publishedCount === requestedCount && requestedCount > 0 ? 'Primera estimación completa' : 'Posiciones ya calculadas'}</small></div>
        <div className="live-kpi"><span><ShieldCheck size={13}/>Error acumulado máximo</span><strong title={errorPercent(currentStudy?.interval_precision?.relative_error_maximum)}>{currentStudy?.interval_precision?.relative_error_maximum != null ? `${uiNumber.format(currentStudy.interval_precision.relative_error_maximum * 100)} %` : '—'}</strong><small>{currentStudy?.accumulated_error_target ? `Objetivo ≤ ${errorPercent(currentStudy.accumulated_error_target)}` : 'Disponible al sumar los mapas'}</small></div>
        <div className="live-kpi"><span><Clock3 size={13}/>Tiempo transcurrido</span><strong>{job.resources ? `${Math.floor(job.resources.elapsed_seconds / 3600)}h ${String(Math.floor(job.resources.elapsed_seconds / 60) % 60).padStart(2, '0')}m` : '—'}</strong><small>{job.resources ? `Pico de RAM ${uiNumber.format(job.resources.peak_rss_mb / 1024)} GiB · hasta ${format(job.resources.time_limit_seconds / 3600)} h` : 'Calculando recursos'}</small></div>
      </div></details>
      <div className="study-run-actions">
        {active ? <>
          <button type="button" className="button secondary" disabled={busy || job.state === 'pausing' || job.state === 'cancelling' || job.stage === 'finalizing-study'} onClick={() => void controlStudy('pause')}><Pause size={14}/>{job.state === 'pausing' ? 'Pausando…' : 'Pausar'}</button>
          <button type="button" className="button ghost stop-study-button" disabled={busy || !settings || job.state === 'cancelling'} onClick={() => void changeJob(true)}>Detener estudio</button>
        </> : <button type="button" className="button primary" disabled={busy} onClick={() => void controlStudy('resume')}><Play size={14}/>Continuar simulación</button>}
      </div>
      <StudyProgressBar compact={active && job.state !== 'pausing'} completed={publishedCount} total={requestedCount} target={currentStudy?.accumulated_error_target}
        maximum={currentStudy?.interval_precision?.relative_error_maximum} stage={job.stage} state={job.state}
        preparation={job.preparation} transportThreads={job.cpu_threads_used}/>
      {!active && <p className="study-resume-note">Se conservan {publishedCount} mapas. Continuar mantiene el mismo estudio, la fecha y los parámetros originales{job.route_study?.particles != null ? ` (${format(job.route_study.particles)} historias por posición)` : ''}. Una posición interrumpida antes de publicarse se calcula de nuevo; las posiciones completas se reutilizan.</p>}
    </section>}
    {actionError && <p className="inline-notice" role="alert">{actionError}</p>}
    <div className="route-control-deck">
    {settings && <section className="surface route-study">
      <details open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="route-study-summary"><Settings2 size={16}/><div>Configurar estudio <span>{format(cellSize)} cm · {format(memoryLimit / 1024)} GiB · hasta {minutesLimit < 60 ? `${minutesLimit} min` : `${uiNumber.format(minutesLimit / 60)} h`}</span></div><ChevronRight size={15}/></summary>
      <form onSubmit={(event) => { event.preventDefault(); void changeJob(false); }}>
      <header className="surface-header"><div><span className="section-label">01 · Calcular</span><h3>Estudio de irradiación</h3>
        <p>{methodLabels[settings.dose_tally]} · {occupancy === 'occupied' ? 'Ocupación completa' : 'Carga aislada'} · Malla de {format(cellSize)} cm</p></div>
        {job && <span className="status-chip" role="status">{jobLabels[job.state] ?? job.state}</span>}
      </header>
      <div className="route-study-fields">
        <label className="field"><span>Fecha de irradiación</span><input type="date" required min={settings.minimum_date ?? undefined} disabled={active || busy} value={active ? job.route_study?.irradiation_date ?? date : date} onInput={(event) => setDate(event.currentTarget.value)} onChange={(event) => setDate(event.target.value)}/>
          <small>La actividad de las fuentes se ajusta a esta fecha.</small>
        </label>
        <label className="field"><span>Alcance del cálculo</span><select disabled={active || busy} value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}>
          <option value="all">Recorrido completo · {settings.positions.length} posiciones</option><option value="selected">Posiciones seleccionadas</option><option value="interval">Intervalo consecutivo</option>
        </select><small>En ocupación completa se mantienen las cargas vecinas de todo el recorrido.</small></label>
        <label className="field"><span>Ocupación del irradiador</span><select value={occupancy} disabled={active || busy} onChange={(event) => setOccupancy(event.target.value as typeof occupancy)}>
          <option value="isolated">Carga aislada</option><option value="occupied">Ocupación completa · con cargas vecinas</option>
        </select><small>{occupancy === 'isolated' ? 'Incluye la atenuación dentro de la propia carga.' : 'Incluye la atenuación y la dispersión causadas por las cargas vecinas.'}</small></label>
        <label className="field"><span>Modo de ejecución</span><select value={quick ? 'quick' : 'normal'} disabled={active || busy} onChange={(event) => {
          const nextQuick = event.target.value === 'quick';
          setQuick(nextQuick);
          if (nextQuick) { setAccumulated(false); setExtendedTime(false); setMinutesLimit((value) => Math.min(15, value)); }
        }}>
          <option value="normal">Parámetros del caso</option><option value="quick">Prueba rápida</option>
        </select><small>{quick ? 'Hasta 60 000 historias por posición. Para revisar el flujo y la geometría.' : accumulated ? budgetGuide ? 'El estudio seleccionado guía el presupuesto inicial; la precisión se comprueba de nuevo.' : 'Las historias se reparten según el error del mapa combinado.' : adaptive && settings.dose_tally !== 'phits' ? 'Las historias se ajustan al objetivo y al tiempo disponible.' : `${format(displayedHistories)} historias solicitadas por posición.`}</small></label>
        {scope === 'selected' && <RoutePositionPicker positions={settings.positions} yawOffset={settings.cad_offset_degrees} selected={selectedNames} onChange={setSelectedNames} disabled={active || busy}/>}
        {scope === 'interval' && <>
          <label className="field"><span>Primera posición a calcular</span><select value={start} disabled={active || busy} onChange={(event) => {
            const next = Number(event.target.value); setStart(next); setEnd(Math.max(end, next));
          }}>{settings.positions.map((point, index) => <option key={point.name} value={index}>{index + 1}. {point.name}</option>)}</select></label>
          <label className="field"><span>Última posición a calcular</span><select value={end} disabled={active || busy} onChange={(event) => setEnd(Number(event.target.value))}>
            {settings.positions.map((point, index) => <option key={point.name} value={index} disabled={index < start}>{index + 1}. {point.name}</option>)}</select></label>
        </>}
        {(adaptive || accumulated) && <label className="field"><span>{accumulated ? 'Objetivo del intervalo' : 'Objetivo por posición'}</span><select value={accumulated ? accumulatedTarget : target} disabled={active || busy} onChange={(event) => accumulated ? setAccumulatedTarget(Number(event.target.value)) : setTarget(Number(event.target.value))}>
          {accumulated ? <><option value={.005}>Máximo acumulado ≤ 0,5 %</option><option value={.01}>Máximo acumulado ≤ 1 %</option><option value={.02}>Máximo acumulado ≤ 2 %</option></>
            : <><option value={.10}>Inicial · error menor del 10 %</option><option value={.05}>Recomendado · error menor del 5 %</option></>}
        </select><small>{accumulated ? 'Se verifica el máximo en todos los vóxeles activos de la suma del intervalo calculado.' : 'Se verifica el error máximo en todos los vóxeles activos de cada posición.'}</small></label>}
        <label className="field"><span>Reducción de varianza del estudio</span><select value={variance} disabled={active || busy} onChange={(event) => setVariance(event.target.value as typeof variance)}>
          <VarianceOptions method={settings.dose_tally}/>
        </select></label>
        <label className="field"><span>Control de convergencia</span><select aria-label="Control de convergencia" value={accumulated ? 'interval' : adaptive ? 'adaptive' : 'fixed'} disabled={active || busy} onChange={(event) => { setAccumulated(event.target.value === 'interval'); setAdaptive(event.target.value === 'adaptive'); }}>
          {settings.statistical_refinement_enabled && !quick && settings.dose_tally !== 'phits' && <option value="interval">Intervalo acumulado · objetivo del mapa</option>}
          {settings.statistical_refinement_enabled && <option value="adaptive">Automático · objetivo por posición</option>}<option value="fixed">Fijo · presupuesto de historias</option>
        </select><small>{!settings.statistical_refinement_enabled ? `Refinamiento deshabilitado por ahora; objetivo predeterminado al activarlo: ${errorPercent(settings.accumulated_error_target ?? .02)} máximo acumulado. Una pasada con el presupuesto indicado.` : accumulated ? 'Comienza con un presupuesto corto y aumenta las historias que más contribuyen al error combinado.' : adaptive ? 'Se comprueba la cobertura completa; los vóxeles sin estimación no permiten declarar convergencia.' : 'Útil para comparar métodos con el mismo número de historias.'}</small></label>
        {(!adaptive || accumulated || settings.dose_tally === 'phits' || quick) && <label className="field"><span>{accumulated ? 'Máximo de historias por posición' : 'Historias por posición'}</span><input aria-label="Historias por posición" required type="number" min={settings.batches} max={1e12} step={1} value={displayedHistories} disabled={active || busy || quick} onInput={(event) => { const value = Number(event.currentTarget.value); if (accumulated) setIntervalMaximum(value); else setHistories(value); }} onChange={(event) => { const value = Number(event.target.value); if (accumulated) setIntervalMaximum(value); else setHistories(value); }}/>
          <small>{quick ? 'La prueba rápida limita el presupuesto a 60 000 historias.' : accumulated ? 'Límite para el ajuste automático. Se guardan los resultados y se informa si el objetivo no se alcanza.' : 'Valor predeterminado: 100 millones de historias por posición. Las mallas finas, las cargas vecinas y los intervalos cortos pueden necesitar más; el error se comprueba en la acumulada.'}</small></label>
        }
        {['source_mixture', 'source_box', 'source_box_windows', 'source_box_magic', 'source_scene'].includes(variance) && <label className="field"><span>Historias orientadas hacia la carga [%]</span><input required type="number" min={1} max={99} step={1} value={mixtureFraction * 100} disabled={active || busy} onChange={(event) => setMixtureFraction(Number(event.target.value) / 100)}/>
          <small>La fracción restante es isotrópica. El peso de cada historia compensa el muestreo angular.</small></label>}
        {!quick && <label className="field"><span>Presupuesto de tiempo</span><select value={extendedTime ? 'extended' : 'standard'} disabled={active || busy} onChange={(event) => {
          const nextExtended = event.target.value === 'extended';
          setExtendedTime(nextExtended);
          if (!nextExtended) setMinutesLimit((value) => Math.min(15, value));
        }}><option value="standard">Estándar · hasta 15 min</option><option value="extended">Extendido · hasta {format(extendedMinutes / 60)} h</option></select></label>}
        <label className="field"><span>Tiempo máximo del estudio [min]</span><input type="number" min={1} max={extendedTime ? extendedMinutes : 15} step={1} required value={minutesLimit} disabled={active || busy} onChange={(event) => setMinutesLimit(Number(event.target.value))}/>
          <small>Incluye preparación, cálculo y posprocesamiento. La espera por otro estudio no consume este presupuesto.</small></label>
        <label className="field"><span>RAM máxima del estudio [MiB]</span><input type="number" min={512} max={8192} step={1} required value={memoryLimit} disabled={active || busy} onChange={(event) => setMemoryLimit(Number(event.target.value))}/>
          <small>El límite efectivo se ajusta a la memoria disponible del equipo.</small></label>
        <label className="field"><span>Malla del estudio [cm]</span><input required type="number" min={.01} max={100} step="any" disabled={active || busy} value={cellSize} onChange={(event) => setCellSize(Number(event.target.value))}/>
          <small>Una malla fina requiere más historias para alcanzar el mismo error por vóxel.</small></label>
      </div>
      {plan && <p className="route-precision-note" role="status">{format(plan.cells)} vóxeles{(plan.geometry_instances ?? 1) > 1 && ` · Escena de ${plan.geometry_instances} cargas`} · RAM estimada {format(plan.estimated_ram_mb / 1024)} GiB · límite efectivo {format(plan.memory_limit_mb / 1024)} GiB{!plan.feasible && ' · La geometría y la malla superan el presupuesto de memoria.'}</p>}
      {accumulated && plan?.precision_estimate && <p className="route-precision-note" role="status">{plan.precision_estimate.available
        ? <>Estimación inicial para {errorPercent(accumulatedTarget)}: {format(plan.precision_estimate.estimated_total_histories ?? 0)} historias totales · {format((plan.precision_estimate.estimated_seconds ?? 0) / 60)} min en el equipo del piloto. {plan.precision_estimate.note}
          <button type="button" className="button secondary" disabled={active || busy} onClick={() => { const seconds = plan.precision_estimate?.suggested_time_limit_seconds ?? 900;
            setIntervalMaximum(Math.min(1e12, plan.precision_estimate?.suggested_max_particles_per_position ?? intervalMaximum));
            setExtendedTime(seconds > 900); setMinutesLimit(Math.min(extendedMinutes, Math.ceil(seconds / 60))); }}>Aplicar presupuesto estimado</button></>
        : plan.precision_estimate.reason}</p>}
      {planError && <p className="inline-notice" role="status">{planError}</p>}
      <div className="route-action-row">
        {active ? <button type="button" className="button secondary" disabled={busy || job.state === 'cancelling'} onClick={() => void changeJob(true)}>Cancelar cálculo</button>
          : <button className="button primary" disabled={busy || !job?.execution_enabled || !settings.positions.length || !selectionValid || !plan || !plan.feasible || !!planError}>Calcular tasas</button>}
        <span className="route-action-summary">{positionCount} posiciones</span>
      </div>
    </form></details>
    {job?.error && !actionError && <StudyErrorNotice error={job.error}/>}
    {!active && job?.resources && <div className="route-resource-bar" role="status"><span>{Math.floor(job.resources.elapsed_seconds / 60)} min {Math.floor(job.resources.elapsed_seconds % 60)} s / {format(job.resources.time_limit_seconds / 60)} min</span>
      <span>Pico de RAM {format(job.resources.peak_rss_mb / 1024)} / {format(job.resources.memory_limit_mb / 1024)} GiB</span>
    </div>}
    {(job?.state === 'time-limited' || job?.state === 'memory-limited' || job?.state === 'disk-limited') && <p className="inline-notice" role="status">{jobLabels[job.state]}. Las posiciones completadas están disponibles en el historial.</p>}
    </section>}
    {studies.length > 0 && <section className="surface route-study-history">
      <button type="button" className="button ghost refresh-study-button" aria-label="Actualizar estudios" title="Actualizar estudios" disabled={busy} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={16}/></button>
      <label className="field"><span>Estudios guardados</span><select value={runId} onChange={(event) => { pendingRun.current = undefined; manualRun.current = event.target.value; setFollowingCurrent(false); setRunId(event.target.value); }}>
        {!runId && <option value="">Esperando los primeros resultados de la ejecución actual…</option>}
        {studies.map((study) => <option key={study.run_id} value={study.run_id} title={`Ejecución ${study.run_id} · ${study.count} posiciones · ${study.variance_reduction ? varianceLabels[study.variance_reduction] ?? study.variance_reduction : ''}`}>{study.activity_date} · {study.first}{study.count > 1 ? ` → ${study.last}` : ''}{study.cell_size_cm && ` · ${format(study.cell_size_cm)} cm`} · {study.run_id.slice(0, 8)}{study.complete === false ? study.count < (study.requested_position_count ?? study.count) ? ` · Parcial ${study.count}/${study.requested_position_count}` : active && job?.run_id === study.run_id ? refining ? ' · Refinando' : ' · Calculando' : ' · Detenido' : ' · Finalizado'}{study.quick ? ' · Prueba rápida' : ''}</option>)}
      </select></label>
      {!followingCurrent && <button type="button" className="button secondary" onClick={() => { manualRun.current = undefined; setFollowingCurrent(true); setRefresh((value) => value + 1); }}>Seguir estudio actual</button>}
      {!active && rates?.run_id === runId && <button type="button" className="button secondary" disabled={busy} onClick={reuseStudy}>Usar parámetros de este estudio</button>}
    </section>}
    </div>
    {runId && (!rates || rates.run_id !== runId) && <p className="inline-notice" role="status">Cargando las tasas del estudio seleccionado…</p>}
    {rates && rates.case_id === caseId && rates.run_id === runId && <DoseCalculator key={`${caseId}/${runId}`} rates={rates} caseId={caseId} caseName={caseName} visible={visible} layout={settings} running={active && job?.run_id === runId}/>}
  </>;
}

export default function RouteResultsView({ preferredCaseId, revision, visible = true, requestedCase, onInspect, onSelectCase, onOpenCalibration }: {
  preferredCaseId: string;
  requestedCase?: { caseId: string };
  revision: number;
  visible?: boolean;
  onInspect: (caseId: string) => void;
  onSelectCase?: (caseId: string) => void;
  onOpenCalibration?: (caseId: string) => void;
}) {
  const [cases, setCases] = useState<IrradiationCase[]>([]);
  const [caseId, setCaseId] = useState('');
  const [followActiveCase, setFollowActiveCase] = useState(true);
  const handledNavigation = useRef<typeof requestedCase>(undefined);
  const [activeCaseIds, setActiveCaseIds] = useState<string[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refreshCases() {
      try {
        const { cases } = await apiGet<{ cases: IrradiationCase[] }>('/api/cases');
        const available = cases.filter((item) => item.workflow === 'suspended_litters' && !item.analysis_method);
        const statuses = await Promise.allSettled(available.map((item) => apiGet<RouteJob>(`/api/cases/${item.case_id}/monte-carlo`)));
        if (!active) return;
        const running = available.flatMap((item, index) => {
          const result = statuses[index];
          return result.status === 'fulfilled' && activeStates.has(result.value.state) ? [item.case_id] : [];
        });
        setCases(available);
        setActiveCaseIds(running);
        if (requestedCase && handledNavigation.current !== requestedCase && available.some((item) => item.case_id === requestedCase.caseId)) {
          handledNavigation.current = requestedCase;
          setFollowActiveCase(false);
          setCaseId(requestedCase.caseId);
        } else {
          setCaseId((current) => selectStudyCase(available, running, followActiveCase, preferredCaseId, current));
        }
        setError('');
      } catch (error) {
        if (active) setError(readableError(error));
      } finally {
        if (active) timer = setTimeout(() => void refreshCases(), 5000);
      }
    }
    void refreshCases();
    return () => { active = false; clearTimeout(timer); };
  }, [followActiveCase, preferredCaseId, requestedCase, revision, visible]);

  const selectedCase = cases.find((item) => item.case_id === caseId);
  return (
    <div className="view-stack route-results-page">
      <StudyActivityRibbon hidden={!visible || !caseId || activeCaseIds.includes(caseId)} onOpen={(runningCase, referenceId) => {
        if (referenceId) onOpenCalibration?.(referenceId);
        else { setFollowActiveCase(true); setCaseId(runningCase); }
      }}/>
      <section className="hero-row"><div><span className="section-label">Categoría IV · Literas suspendidas</span><h2>Análisis de irradiación</h2><p>Explora el recorrido y transforma las tasas en dosis y tiempos.</p></div>
        <div className="result-case-actions">
          <label className="result-case-select"><span>Caso de irradiación</span><select value={caseId} disabled={!cases.length} onChange={(event) => { setFollowActiveCase(false); setCaseId(event.target.value); onSelectCase?.(event.target.value); }}>
            {!cases.length && <option value="">No hay casos disponibles</option>}
            {cases.map((item) => <option key={item.case_id} value={item.case_id}>{item.name}{activeCaseIds.includes(item.case_id) ? ' · En curso' : ''}</option>)}
          </select></label>
          {!followActiveCase && activeCaseIds.length > 0 && !activeCaseIds.includes(caseId) && <button type="button" className="button secondary" onClick={() => { setFollowActiveCase(true); setCaseId(activeCaseIds[0]); }}>Seguir simulación en curso</button>}
          {selectedCase?.step_input && <button type="button" className="button secondary" onClick={() => onInspect(caseId)}>Ver geometría del caso</button>}
        </div>
      </section>
      {error && <p className="inline-notice" role="status">{error}</p>}
      {caseId ? <CaseStudy key={caseId} caseId={caseId} caseName={selectedCase?.name ?? caseId} airReference={isAirReference(selectedCase?.step_material_tags)} revision={revision} visible={visible}/> : <div className="surface empty-list">{cases.length ? 'Selecciona un caso de irradiación para empezar.' : 'Consultando casos y simulaciones en curso…'}</div>}
    </div>
  );
}
