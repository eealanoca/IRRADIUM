'use client';
import { uiLocale, numberFormatter } from '../../lib/i18n/number';

import { useEffect, useState } from 'react';
import { API_URL, apiGet, apiJson } from '../../lib/api';
import type { IrradiationCase } from '../../lib/types';
import { readableError } from '../route/RouteConfigurationView';
import { DoseCalculator, type RouteJob, type RouteRates, type StudySettings } from '../route/RouteResultsView';
import StudyProgressBar from '../route/StudyProgressBar';

const activeStates = ['queued', 'running', 'pausing', 'cancelling'];
const number = numberFormatter({ maximumSignificantDigits: 5 });
const activeJob = (job?: RouteJob) => job && activeStates.includes(job.state);

function CompareRates({ reference, simulation }: { reference: RouteRates; simulation: RouteRates }) {
  const matches = simulation.positions.flatMap((point) => {
    const expected = reference.positions.find((item) => item.name === point.name);
    return expected ? [{ expected, point }] : [];
  });
  const totalReference = matches.reduce((sum, row) => sum + row.expected.mean_kgy_h, 0);
  const totalSimulation = matches.reduce((sum, row) => sum + row.point.mean_kgy_h, 0);
  const difference = (value: number, expected: number) => expected > 0 ? `${number.format(100 * (value / expected - 1))} %` : '—';
  return <section className="surface">
    <header className="surface-header"><div><h3>Determinista y OpenMC rápido</h3><p>Respuesta de alanina en aire seco · Diferencia = (OpenMC / determinista − 1) × 100.</p></div></header>
    <div className="data-table-wrap"><table>
      <thead><tr><th>Posición</th><th>Determinista [kGy/h]</th><th>OpenMC [kGy/h]</th><th>Diferencia</th><th>Error Monte Carlo P90</th></tr></thead>
      <tbody>
        <tr><th scope="row">{matches.length === reference.positions.length ? 'Suma del recorrido' : 'Suma de posiciones comunes'} · {matches.length}</th><td>{number.format(totalReference)}</td><td>{number.format(totalSimulation)}</td><td>{difference(totalSimulation, totalReference)}</td><td>Consulta el mapa de la suma</td></tr>
        {matches.map(({ expected, point }) => <tr key={point.name}><th scope="row">{point.name}</th><td>{number.format(expected.mean_kgy_h)}</td><td>{number.format(point.mean_kgy_h)}</td><td>{difference(point.mean_kgy_h, expected.mean_kgy_h)}</td><td>{point.relative_error_p90 == null ? '—' : `${number.format(point.relative_error_p90 * 100)} %`}</td></tr>)}
      </tbody>
    </table></div>
  </section>;
}

function CalibrationStudy({ study, comparison, refreshCases, preferOpenMc }: {
  study: IrradiationCase; comparison?: IrradiationCase; refreshCases: () => void; preferOpenMc: boolean;
}) {
  const [data, setData] = useState<{ rates: RouteRates; layout: StudySettings }>();
  const [job, setJob] = useState<RouteJob>();
  const [simulation, setSimulation] = useState<RouteRates>();
  const [view, setView] = useState<'deterministic' | 'openmc' | 'comparison'>(preferOpenMc ? 'openmc' : 'deterministic');
  const [particles, setParticles] = useState('100000000');
  const [minutes, setMinutes] = useState('240');
  const [error, setError] = useState('');
  const [jobError, setJobError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const referencePath = `/api/route/cases/${study.case_id}`;
  const comparisonId = comparison?.case_id;
  const currentSimulation = simulation?.run_id === job?.run_id ? simulation : undefined;

  useEffect(() => {
    let active = true;
    Promise.all([apiGet<RouteRates>(`${referencePath}/results`), apiGet<StudySettings>(`${referencePath}/study`)])
      .then(([rates, layout]) => { if (active) setData({ rates, layout }); })
      .catch((failure) => { if (active) setError(readableError(failure)); });
    return () => { active = false; };
  }, [referencePath, attempt]);

  useEffect(() => {
    if (!comparisonId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let loadedVersion = '';
    async function poll() {
      try {
        const status = await apiGet<RouteJob>(`/api/cases/${comparisonId}/monte-carlo`);
        if (!active) return;
        setJob(status);
        if (status.run_id) {
          const route = `/api/route/cases/${comparisonId}`;
          const { studies } = await apiGet<{ studies: { run_id: string; count: number; complete: boolean; stop_reason?: string }[] }>(`${route}/studies`);
          const saved = studies.find((item) => item.run_id === status.run_id);
          const version = JSON.stringify([saved, status.state]);
          if (active && saved && loadedVersion !== version) {
            const rates = await apiGet<RouteRates>(`${route}/results?run_id=${status.run_id}`);
            if (active) {
              loadedVersion = version;
              setSimulation({ ...rates, analysis_method: 'monte_carlo_air' });
            }
          }
        }
        if (active) setJobError('');
      } catch (failure) {
        if (active) setJobError(readableError(failure));
      } finally {
        if (active) timer = setTimeout(() => void poll(), 3500);
      }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [comparisonId]);

  async function start(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const status = await apiJson<RouteJob>(`/api/route/calibration/${study.case_id}/openmc`, 'POST', {
        particles: Number(particles), time_limit_minutes: Number(minutes),
      });
      setJob(status);
      setSimulation(undefined);
      setView('openmc');
      refreshCases();
    } catch (failure) { setError(readableError(failure)); }
    finally { setBusy(false); }
  }

  async function control(action: 'pause' | 'resume' | 'cancel') {
    setBusy(true);
    setError('');
    try {
      setJob(await apiJson<RouteJob>(`/api/cases/${comparisonId}/monte-carlo/${action}`, 'POST',
        action === 'resume' ? { run_id: job?.run_id } : undefined));
    } catch (failure) { setError(readableError(failure)); }
    finally { setBusy(false); }
  }

  if (!data) return error
    ? <p className="inline-notice" role="alert">{error} <button type="button" className="button secondary" onClick={() => { setError(''); setAttempt((value) => value + 1); }}>Reintentar</button></p>
    : <p className="inline-notice" role="status">Cargando los mapas de calibración…</p>;
  const active = activeJob(job);
  const completed = currentSimulation?.positions.length ?? job?.saved_position_count ?? 0;
  return <>
    <section className="surface air-reference">
      <header className="surface-header"><div><h3>OpenMC rápido · Flujo y conversión</h3>
        <p>Usa la carga, la malla, las fuentes y la fecha de esta calibración para calcular la respuesta de alanina en todo el recorrido.</p></div>
        {study.stl_input && <a className="button secondary" href={`${API_URL}/api/cases/${study.case_id}/inputs/stl`}>Descargar STL de la calibración</a>}
      </header>
      <form className="form-stack" onSubmit={start}>
        <span className="section-label">{active ? 'Parámetros en ejecución' : 'Nuevo cálculo'}</span>
        <div className="simulation-form-grid">
          <label className="field"><span>Historias por posición</span><input required type="number" min={100} max={1e12} step={1} value={active ? job?.route_study?.particles ?? particles : particles} disabled={busy || active} onChange={(event) => setParticles(event.target.value)}/></label>
          <label className="field"><span>Límite de tiempo [min]</span><input required type="number" min={1} max={1440} step={1} value={active && job?.route_study?.time_limit_seconds != null ? job.route_study.time_limit_seconds / 60 : minutes} disabled={busy || active} onChange={(event) => setMinutes(event.target.value)}/></label>
        </div>
        <div className="route-row-controls">
          <button type="submit" className="button primary" disabled={busy || active}>Calcular con OpenMC rápido</button>
          {active && <><button type="button" className="button secondary" disabled={busy || job?.state === 'pausing'} onClick={() => void control('pause')}>Pausar</button>
            <button type="button" className="button ghost" disabled={busy} onClick={() => void control('cancel')}>Detener</button></>}
          {!active && job?.can_resume && <button type="button" className="button secondary" disabled={busy} onClick={() => void control('resume')}>Continuar simulación</button>}
        </div>
      </form>
      {job?.route_study && <p className="inline-notice">Estudio guardado · {number.format(job.route_study.particles ?? 0)} historias por posición · {job.route_study.cell_size_cm ?? data.rates.cell_size_cm} cm · {job.route_study.irradiation_date}</p>}
      {job && job.state !== 'not-started' && <StudyProgressBar completed={completed} total={data.rates.positions.length} stage={job.stage} state={job.state} preparation={job.preparation} transportThreads={job.cpu_threads_used}/>}
      {job?.error && <p className="inline-notice" role="status">{job.error}</p>}
      {(error || jobError) && <p className="inline-notice" role="alert">{error || jobError}</p>}
    </section>
    <div className="result-analysis-tabs cad-mode-tabs" role="tablist" aria-label="Método de calibración">
      <button type="button" role="tab" aria-selected={view === 'deterministic'} onClick={() => setView('deterministic')}>Determinista</button>
      <button type="button" role="tab" aria-selected={view === 'openmc'} onClick={() => setView('openmc')}>OpenMC rápido</button>
      <button type="button" role="tab" aria-selected={view === 'comparison'} disabled={!currentSimulation} onClick={() => setView('comparison')}>Comparar tasas</button>
    </div>
    {view === 'deterministic' && <DoseCalculator rates={data.rates} layout={data.layout} caseId={study.case_id} caseName={study.name} visible/>}
    {view === 'openmc' && (currentSimulation && comparison
      ? <DoseCalculator key={currentSimulation.run_id} scenarioRevision={job?.state === 'completed' ? 1 : 0} rates={currentSimulation} layout={data.layout} caseId={comparison.case_id} caseName={comparison.name} visible running={Boolean(active)}/>
      : <p className="inline-notice" role="status">{active ? 'Los mapas se mostrarán a medida que termine cada posición.' : 'Inicia el cálculo rápido para generar los mapas de comparación.'}</p>)}
    {view === 'comparison' && currentSimulation && <CompareRates reference={data.rates} simulation={currentSimulation}/>}
  </>;
}

export default function AirCalibrationResults({ preferredCaseId, preferOpenMc = false, onSelect }: {
  preferredCaseId: string;
  preferOpenMc?: boolean;
  onSelect: (caseId: string) => void;
}) {
  const [cases, setCases] = useState<IrradiationCase[]>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    apiGet<{ cases: IrradiationCase[] }>('/api/cases')
      .then(({ cases }) => { if (active) setCases(cases); })
      .catch((failure) => { if (active) setError(readableError(failure)); });
    return () => { active = false; };
  }, [attempt]);

  const references = cases?.filter((item) => item.analysis_method === 'deterministic_air');
  const selected = references?.find((item) => item.case_id === preferredCaseId) ?? references?.[0];
  const comparison = cases?.find((item) => item.calibration_case_id === selected?.case_id && item.analysis_method === 'monte_carlo_air');
  if (error) return <p className="inline-notice" role="alert">{error} <button type="button" className="button secondary" onClick={() => { setError(''); setAttempt((value) => value + 1); }}>Reintentar</button></p>;
  if (!selected) return <p className="inline-notice" role="status">{cases ? 'Todavía no hay calibraciones guardadas. Calcula un recorrido para ver sus mapas aquí.' : 'Cargando calibraciones guardadas…'}</p>;
  return <>
    <section className="surface route-study-history">
      <label className="field"><span>Calibraciones guardadas</span><select value={selected.case_id} onChange={(event) => onSelect(event.target.value)}>
        {references?.map((item) => {
          const dimensions = item.step_input?.original_name.match(/^airload-([\d.]+)-([\d.]+)-([\d.]+)\.step$/)?.slice(1);
          return <option key={item.case_id} value={item.case_id}>
            {new Date(item.created_at).toLocaleString(uiLocale())} · {dimensions ? `${dimensions.join(' × ')} mm` : item.name}
          </option>;
        })}
      </select></label>
    </section>
    <CalibrationStudy key={`${selected.case_id}/${preferOpenMc}`} study={selected} preferOpenMc={preferOpenMc} comparison={comparison} refreshCases={() => setAttempt((value) => value + 1)}/>
  </>;
}
