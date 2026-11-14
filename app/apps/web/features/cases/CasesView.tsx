'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle2, ChevronRight, Clock3, Cpu, FileJson2, FileText,
  LockKeyhole, MoveVertical, Plus, Save, Square, Trash2, Zap,
} from 'lucide-react';
import { API_URL, apiGet, apiJson } from '../../lib/api';
import type { DeterministicCaseParameters, IrradiationCase, SimulationBackendsResponse, SimulationParameters, SimulationProfile, StepMaterial, WorkSectionsResponse } from '../../lib/types';

type RunStatus = {
  state: string;
  stage?: string;
  progress: number;
  error?: string;
  particles?: number;
  cpu_cores?: number;
  cpu_threads_used?: number;
};
type DeterministicResult = {
  step_angle_degrees_best_real: number[];
  length_points_mm: number[];
  best_length_points_mm: number[];
  average_dose?: number;
  average_theoretical_dose?: number;
};

const EMPTY_RUN: RunStatus = { state: 'not-started', stage: '', progress: 0 };
const ACTIVE_RUN_STATES = ['queued', 'running', 'cancelling'];
const DEFAULT_PARAMETERS: DeterministicCaseParameters = {
  work_section: 'A',
  num_points: 10000,
  cores: -1,
  longitudinal_positions_mm: [-800, -300, 200, 700],
};
const DEFAULT_SIMULATION: SimulationParameters = {
  radionuclide: 'Co-60',
  movement: {
    default_work_section: 'A',
    machine_angle_start_degrees: 0,
    machine_angle_stop_degrees: 360,
    machine_angle_step_degrees: 10,
    angular_positions_range: [1, 13],
    cad_rotation_degrees: 0,
    length_complete_lower_range_mm: [-800, 200],
    length_complete_upper_range_mm: [200, 700],
    resolution_length_mm: 500,
  },
  deterministic: { num_points: 10000 },
  compute: { cpu_cores: -1 },
  monte_carlo: {
    cell_size_cm: 2,
    particles: 100_000_000,
    batches: 100,
    relative_error_target: 0.03,
    dose_tally: 'flux',
  },
};
const caseStepName = (item: IrradiationCase) => item.step_input?.original_name
  ?? (item.input_snapshot.includes('load.step') ? 'load.step' : 'Sin archivo STEP');
const caseStlName = (item: IrradiationCase) => item.stl_input?.original_name
  ?? (item.input_snapshot.includes('world_load.stl') ? 'world_load.stl' : 'Sin archivo STL');
const caseParameters = (item: IrradiationCase): DeterministicCaseParameters => ({
  ...DEFAULT_PARAMETERS,
  ...(item.deterministic_parameters ?? {}),
  longitudinal_positions_mm: item.deterministic_parameters?.longitudinal_positions_mm
    ?? DEFAULT_PARAMETERS.longitudinal_positions_mm,
});
const caseSimulation = (item: IrradiationCase): SimulationParameters => {
  const snapshot = item.simulation_parameters;
  if (!snapshot) return DEFAULT_SIMULATION;
  // Algunos casos antiguos contienen una instantánea válida pero anterior a
  // batches/relative_error_target. Mezclar cada bloque evita que un campo
  // ausente llegue como undefined a la vista o al ejecutor.
  return {
    ...DEFAULT_SIMULATION,
    ...snapshot,
    movement: {
      ...DEFAULT_SIMULATION.movement,
      ...(snapshot.movement ?? {}),
    },
    deterministic: {
      ...DEFAULT_SIMULATION.deterministic,
      ...(snapshot.deterministic ?? {}),
    },
    compute: {
      ...DEFAULT_SIMULATION.compute,
      ...(snapshot.compute ?? {}),
    },
    monte_carlo: {
      ...DEFAULT_SIMULATION.monte_carlo,
      ...(snapshot.monte_carlo ?? {}),
    },
  };
};
const angularSummary = (item: IrradiationCase) => {
  const parameters = caseSimulation(item).movement;
  return `${parameters.machine_angle_start_degrees}°–${parameters.machine_angle_stop_degrees - parameters.machine_angle_step_degrees}° · paso ${parameters.machine_angle_step_degrees}°`;
};
const profileUsesPhits = (profile: SimulationProfile) => profile.parameters?.monte_carlo?.dose_tally === 'phits';

export default function CasesView({
  stepFile,
  stlFile,
  stepMaterials,
  stepMaterialTags,
  configurationRevision,
  onOpenLoad,
  onOpenResults,
}: {
  stepFile: File | null;
  stlFile: File | null;
  stepMaterials: StepMaterial[];
  stepMaterialTags: string[];
  configurationRevision: number;
  onOpenLoad: () => void;
  onOpenResults: (caseId?: string) => void;
}) {
  const [cases, setCases] = useState<IrradiationCase[]>([]);
  const [selected, setSelected] = useState<IrradiationCase | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [profiles, setProfiles] = useState<SimulationProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [caseProfileId, setCaseProfileId] = useState('');
  const [caseStlFile, setCaseStlFile] = useState<File | null>(null);
  const [usePreparedStep, setUsePreparedStep] = useState(false);
  const [sections, setSections] = useState<Record<string, [number, number, number]>>({});
  const [workSection, setWorkSection] = useState('A');
  const [creating, setCreating] = useState(false);
  const [deletingCaseId, setDeletingCaseId] = useState('');
  const [notice, setNotice] = useState('');
  const [run, setRun] = useState<RunStatus>(EMPTY_RUN);
  const [monteCarloRun, setMonteCarloRun] = useState<RunStatus>(EMPTY_RUN);
  const [deterministicResult, setDeterministicResult] = useState<DeterministicResult | null>(null);
  const [savingResults, setSavingResults] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stoppingMonteCarlo, setStoppingMonteCarlo] = useState(false);
  const [startingMonteCarlo, setStartingMonteCarlo] = useState(false);
  const [applyingMaterials, setApplyingMaterials] = useState(false);
  const [phitsAvailability, setPhitsAvailability] = useState<SimulationBackendsResponse['phits'] | null>(null);

  const loadLists = useCallback(async () => {
    try {
      const [caseData, sectionData, profileData, backendData] = await Promise.all([
        apiGet<{ cases: IrradiationCase[] }>('/api/cases'),
        apiGet<WorkSectionsResponse>('/api/config/work-sections'),
        apiGet<{ profiles: SimulationProfile[] }>('/api/config/simulation-profiles'),
        apiGet<SimulationBackendsResponse>('/api/simulation/backends'),
      ]);
      setCases(caseData.cases);
      setSections(sectionData.positions);
      setProfiles(profileData.profiles);
      setPhitsAvailability(backendData.phits);
      setSelectedProfileId((current) => current || profileData.profiles.find((profile) => !profileUsesPhits(profile) || backendData.phits.available)?.profile_id || '');
    } catch {
      setNotice('No fue posible cargar los casos o la configuración.');
    }
  }, []);

  useEffect(() => {
    Promise.all([
      apiGet<{ cases: IrradiationCase[] }>('/api/cases'),
      apiGet<WorkSectionsResponse>('/api/config/work-sections'),
      apiGet<{ profiles: SimulationProfile[] }>('/api/config/simulation-profiles'),
      apiGet<SimulationBackendsResponse>('/api/simulation/backends'),
    ])
      .then(([caseData, sectionData, profileData, backendData]) => {
        setCases(caseData.cases);
        setSections(sectionData.positions);
        setProfiles(profileData.profiles);
        setPhitsAvailability(backendData.phits);
        setSelectedProfileId((current) => current || profileData.profiles.find((profile) => !profileUsesPhits(profile) || backendData.phits.available)?.profile_id || '');
      })
      .catch(() => setNotice('No fue posible cargar los casos o la configuración.'));
  }, [configurationRevision]);

  useEffect(() => {
    if (!selected || !ACTIVE_RUN_STATES.includes(run.state)) return;
    const timer = window.setInterval(() => {
      apiGet<RunStatus>(`/api/cases/${selected.case_id}/deterministic`)
        .then(setRun)
        .catch(() => setNotice('Se perdió la comunicación con el proceso determinista.'));
    }, 1800);
    return () => window.clearInterval(timer);
  }, [run.state, selected]);

  useEffect(() => {
    if (!selected || !ACTIVE_RUN_STATES.includes(monteCarloRun.state)) return;
    const timer = window.setInterval(() => {
      apiGet<RunStatus>(`/api/cases/${selected.case_id}/monte-carlo`)
        .then(setMonteCarloRun)
        .catch(() => setNotice('Se perdió la comunicación con el proceso Monte Carlo.'));
    }, 1800);
    return () => window.clearInterval(timer);
  }, [monteCarloRun.state, selected]);

  useEffect(() => {
    if (!selected || run.state !== 'completed') return;
    apiGet<DeterministicResult>(`/api/cases/${selected.case_id}/deterministic/result`)
      .then(setDeterministicResult)
      .catch(() => setNotice('El proceso terminó, pero su resultado no está disponible.'));
  }, [run.state, selected]);

  const create = async () => {
    if (!name.trim() || !description.trim()) {
      setNotice('El nombre y la descripción son obligatorios.');
      return;
    }
    const creationProfile = profiles.find((profile) => profile.profile_id === selectedProfileId);
    if (creationProfile && profileUsesPhits(creationProfile) && !phitsAvailability?.available) {
      setNotice(phitsAvailability?.message ?? 'PHITS no está disponible en este sistema.');
      return;
    }
    setCreating(true);
    setNotice('');
    try {
      const created = await apiJson<IrradiationCase>('/api/cases', 'POST', {
        name,
        description,
        simulation_profile_id: selectedProfileId || null,
        step_materials: stepFile ? null : stepMaterials,
      });
      let persistedCase = created;
      if (stepFile || stlFile) {
        const form = new FormData();
        if (stepFile) form.append('step', stepFile);
        if (stlFile) form.append('stl', stlFile);
        if (stepFile) form.append('materials_json', JSON.stringify(stepMaterials));
        const response = await fetch(`${API_URL}/api/cases/${created.case_id}/inputs`, { method: 'POST', body: form });
        if (!response.ok) throw new Error('Geometry upload failed');
        const upload = await response.json() as { case: IrradiationCase };
        persistedCase = upload.case;
      }
      if ((persistedCase.step_materials?.length ?? 0) !== stepMaterials.length) {
        persistedCase = await apiJson<IrradiationCase>(
          `/api/cases/${created.case_id}/inputs/materials`,
          'PUT',
          stepMaterials,
        );
      }
      setSelected(persistedCase);
      setCaseProfileId(persistedCase.simulation_profile?.profile_id ?? '');
      setCaseStlFile(null);
      setUsePreparedStep(false);
      const parameters = caseParameters(persistedCase);
      setWorkSection(parameters.work_section);
      setName('');
      setDescription('');
      setRun(EMPTY_RUN);
      setMonteCarloRun(EMPTY_RUN);
      setNotice('Caso creado. La descripción quedó vinculada a la salida final.');
      await loadLists();
    } catch {
      setNotice('No fue posible crear el caso.');
    } finally {
      setCreating(false);
    }
  };

  const selectCase = async (item: IrradiationCase) => {
    setSelected(item);
    setCaseProfileId(
      item.simulation_profile?.profile_id === 'configuracion-anterior'
        ? ''
        : item.simulation_profile?.profile_id ?? '',
    );
    setCaseStlFile(null);
    setUsePreparedStep(false);
    const parameters = caseParameters(item);
    setWorkSection(parameters.work_section);
    setDeterministicResult(null);
    setNotice('');
    try {
      const [deterministicStatus, monteCarloStatus] = await Promise.all([
        apiGet<RunStatus>(`/api/cases/${item.case_id}/deterministic`),
        apiGet<RunStatus>(`/api/cases/${item.case_id}/monte-carlo`),
      ]);
      setRun(deterministicStatus);
      setMonteCarloRun(monteCarloStatus);
    } catch {
      setRun(EMPTY_RUN);
      setMonteCarloRun(EMPTY_RUN);
    }
  };

  const saveCase = async () => {
    if (!selected || !selected.name.trim() || !selected.description.trim()) {
      setNotice('El nombre y la descripción no pueden quedar vacíos.');
      return;
    }
    const pendingProfile = profiles.find((profile) => profile.profile_id === caseProfileId);
    if (pendingProfile && profileUsesPhits(pendingProfile) && !phitsAvailability?.available) {
      setNotice(phitsAvailability?.message ?? 'PHITS no está disponible en este sistema.');
      return;
    }
    const parameters = { ...caseParameters(selected), work_section: workSection };
    try {
      let updated = await apiJson<IrradiationCase>(`/api/cases/${selected.case_id}`, 'PUT', {
        name: selected.name,
        description: selected.description,
        deterministic_parameters: parameters,
        simulation_profile_id: caseProfileId || null,
      });
      if ((usePreparedStep && stepFile) || caseStlFile) {
        const form = new FormData();
        if (usePreparedStep && stepFile) {
          form.append('step', stepFile);
          form.append('materials_json', JSON.stringify(stepMaterials));
        }
        if (caseStlFile) form.append('stl', caseStlFile);
        const response = await fetch(`${API_URL}/api/cases/${selected.case_id}/inputs`, {
          method: 'POST',
          body: form,
        });
        if (!response.ok) throw new Error('Geometry upload failed');
        const upload = await response.json() as { case: IrradiationCase };
        updated = upload.case;
      }
      setSelected(updated);
      setCaseProfileId(updated.simulation_profile?.profile_id ?? caseProfileId);
      setCaseStlFile(null);
      setUsePreparedStep(false);
      setNotice('Caso de irradiación actualizado, incluida su instantánea de entradas.');
      await loadLists();
    } catch {
      setNotice('No fue posible actualizar el caso.');
    }
  };

  const applyMaterialsToSelected = async () => {
    if (!selected) return;
    if (stepMaterialTags.length === 0 || stepMaterials.length !== stepMaterialTags.length) {
      setNotice('Completa primero todos los materiales detectados en Carga CAD.');
      return;
    }
    setApplyingMaterials(true);
    setNotice('Validando los materiales con el STEP guardado…');
    try {
      const updated = await apiJson<IrradiationCase>(
        `/api/cases/${selected.case_id}/inputs/materials`,
        'PUT',
        stepMaterials,
      );
      setSelected(updated);
      setCases((current) => current.map((item) => item.case_id === updated.case_id ? updated : item));
      setNotice(`${updated.step_materials?.length ?? 0} materiales vinculados correctamente al caso.`);
    } catch {
      setNotice('Los materiales actuales no coinciden con los nombres del STEP guardado en este caso.');
    } finally {
      setApplyingMaterials(false);
    }
  };

  const deleteCase = async (item: IrradiationCase) => {
    const confirmed = window.confirm(
      `¿Eliminar el caso «${item.name}»? Se eliminarán su STEP, STL, configuración, resultados y registros. Esta acción no se puede deshacer.`,
    );
    if (!confirmed) return;

    setDeletingCaseId(item.case_id);
    setNotice('');
    try {
      const response = await apiJson<{ cases: IrradiationCase[] }>(
        `/api/cases/${item.case_id}`,
        'DELETE',
      );
      setCases(response.cases);
      if (selected?.case_id === item.case_id) {
        setSelected(null);
        setCaseProfileId('');
        setCaseStlFile(null);
        setUsePreparedStep(false);
        setWorkSection('A');
        setRun(EMPTY_RUN);
        setMonteCarloRun(EMPTY_RUN);
        setDeterministicResult(null);
      }
      setNotice(`Caso «${item.name}» eliminado del historial.`);
    } catch {
      setNotice('No fue posible eliminar el caso. Verifica que no tenga una optimización en ejecución.');
    } finally {
      setDeletingCaseId('');
    }
  };

  const start = async () => {
    if (!selected) return;
    const parameters = { ...caseParameters(selected), work_section: workSection };
    setNotice('');
    setDeterministicResult(null);
    try {
      const updated = await apiJson<IrradiationCase>(`/api/cases/${selected.case_id}`, 'PUT', {
        name: selected.name,
        description: selected.description,
        deterministic_parameters: parameters,
      });
      setSelected(updated);
      setCases((current) => current.map((item) => item.case_id === updated.case_id ? updated : item));
      setRun(await apiJson<RunStatus>(`/api/cases/${selected.case_id}/deterministic`, 'POST', {
        num_points: parameters.num_points,
        cores: parameters.cores,
        work_section: parameters.work_section,
        longitudinal_positions_mm: parameters.longitudinal_positions_mm,
        angle_degrees: caseSimulation(selected).movement.cad_rotation_degrees,
      }));
    } catch {
      setNotice('No fue posible iniciar la optimización determinista.');
    }
  };

  const stop = async () => {
    if (!selected) return;
    setStopping(true);
    setNotice('Deteniendo la optimización y sus procesos paralelos…');
    try {
      const status = await apiJson<RunStatus>(
        `/api/cases/${selected.case_id}/deterministic/cancel`,
        'POST',
      );
      setRun(status);
      setNotice(
        status.state === 'cancelled'
          ? 'Simulación determinista detenida. Puede iniciarla nuevamente cuando lo necesite.'
          : 'El proceso ya había finalizado; se actualizó su estado.',
      );
    } catch {
      setNotice('No fue posible detener la simulación. Revisa el estado del servicio local.');
    } finally {
      setStopping(false);
    }
  };

  const stopMonteCarlo = async () => {
    if (!selected) return;
    setStoppingMonteCarlo(true);
    setNotice(`Deteniendo ${selectedMonteCarloEngine} y todos sus procesos paralelos…`);
    try {
      const status = await apiJson<RunStatus>(
        `/api/cases/${selected.case_id}/monte-carlo/cancel`,
        'POST',
      );
      setMonteCarloRun(status);
      setNotice('Simulación Monte Carlo detenida.');
    } catch {
      setNotice('No fue posible detener Monte Carlo. Revisa el estado del servicio local.');
    } finally {
      setStoppingMonteCarlo(false);
    }
  };

  const startMonteCarlo = async () => {
    if (!selected) return;
    setStartingMonteCarlo(true);
    setNotice(selectedDoseTally === 'phits'
      ? 'Preparando STEP, geometría PHITS y modelo de transporte…'
      : 'Preparando STEP, geometría DAGMC y modelo OpenMC…');
    try {
      const status = await apiJson<RunStatus>(
        `/api/cases/${selected.case_id}/monte-carlo`,
        'POST',
      );
      setMonteCarloRun(status);
      setNotice(`Monte Carlo iniciado con ${caseSimulation(selected).monte_carlo.particles.toLocaleString(uiLocale())} partículas. Puede detenerse en cualquier momento.`);
    } catch {
      setNotice('No fue posible iniciar Monte Carlo. Verifica que el resultado determinista esté completo.');
    } finally {
      setStartingMonteCarlo(false);
    }
  };

  const saveResults = async () => {
    if (!selected) return;
    setSavingResults(true);
    try {
      await apiJson(`/api/cases/${selected.case_id}/results/save`, 'POST');
      setNotice('Los resultados y la trayectoria optimizada se guardaron correctamente.');
      onOpenResults(selected.case_id);
    } catch {
      setNotice('No fue posible guardar los resultados. Verifica que ambas simulaciones estén completas.');
    } finally {
      setSavingResults(false);
    }
  };

  const simulationsComplete = run.state === 'completed' && monteCarloRun.state === 'completed';

  const workflow = [
    { n: '01', t: 'Carga y condiciones', state: selected ? 'done' : 'active' },
    { n: '02', t: 'Optimización', state: ACTIVE_RUN_STATES.includes(run.state) ? 'active' : run.state === 'completed' ? 'done' : '' },
    { n: '03', t: 'Revisión', state: deterministicResult ? 'active' : '' },
    { n: '04', t: 'Monte Carlo', state: ACTIVE_RUN_STATES.includes(monteCarloRun.state) ? 'active' : monteCarloRun.state === 'completed' ? 'done' : run.state === 'completed' ? '' : 'locked' },
    { n: '05', t: 'Guardar resultados', state: simulationsComplete ? 'active' : 'locked' },
  ];
  const averageDose = deterministicResult?.average_dose ?? deterministicResult?.average_theoretical_dose;
  const selectedCellSize = selected
    ? caseSimulation(selected).monte_carlo.cell_size_cm
    : DEFAULT_SIMULATION.monte_carlo.cell_size_cm;
  const selectedParticles = selected
    ? caseSimulation(selected).monte_carlo.particles
    : DEFAULT_SIMULATION.monte_carlo.particles;
  const selectedBatches = selected
    ? caseSimulation(selected).monte_carlo.batches
    : DEFAULT_SIMULATION.monte_carlo.batches;
  const selectedRelativeErrorTarget = selected
    ? caseSimulation(selected).monte_carlo.relative_error_target
    : DEFAULT_SIMULATION.monte_carlo.relative_error_target;
  const selectedDoseTally = selected
    ? caseSimulation(selected).monte_carlo.dose_tally
    : 'flux';
  const selectedMonteCarloMethod = selectedDoseTally === 'heating'
    ? 'Cálculo intensivo'
    : selectedDoseTally === 'alanine'
      ? 'Cálculo alternativo'
      : selectedDoseTally === 'phits'
        ? 'Cálculo Comparativo PHITS'
      : 'Cálculo rápido';
  const selectedMonteCarloEngine = selectedDoseTally === 'phits' ? 'PHITS' : 'OpenMC';

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div><span className="section-label">Procedimiento secuencial</span><h2>Preparación y ejecución del caso</h2><p>Cada caso conserva su geometría, configuración, descripción, resultados y registros.</p></div>
      </section>

      <div className="workflow-line">
        {workflow.map((step) => <div key={step.n} className={`workflow-node ${step.state}`}><span>{step.state === 'locked' ? <LockKeyhole size={14} /> : step.state === 'done' ? <CheckCircle2 size={14} /> : step.n}</span><strong>{step.t}</strong></div>)}
      </div>

      <div className="cases-layout">
        <section className="surface new-case-panel">
          <header className="surface-header"><div><span className="section-label">Nuevo caso</span><h3>Datos de irradiación</h3></div><Plus size={19} /></header>
          <div className="form-stack">
            <label className="field"><span>Nombre del caso</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Ej. Lote piloto 08-2026" /></label>
            <label className="field"><span>Descripción de la irradiación</span><textarea className="prose-input" rows={5} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Describe la carga, el objetivo de dosis y cualquier condición relevante…" /><small>Esta descripción se copiará en movements_parameters_optimized.json.</small></label>
            <label className="field"><span>Configuración de simulación</span><select value={selectedProfileId} onChange={(event) => setSelectedProfileId(event.target.value)}><option value="">Configuración actual</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id} disabled={profileUsesPhits(profile) && !phitsAvailability?.available}>{profile.name}{profileUsesPhits(profile) && !phitsAvailability?.available ? ' · PHITS no disponible' : ''}</option>)}</select><small>El caso guardará una copia independiente de la configuración seleccionada.</small></label>
            <div className="attached-file"><FileText size={17} /><div><span>STEP · Monte Carlo</span><strong>{stepFile?.name ?? 'Modelo STEP de ejemplo'} · {stepMaterials.length}/{stepMaterialTags.length} materiales</strong></div><button onClick={onOpenLoad}>Cambiar</button></div>
            <div className="attached-file"><FileText size={17} /><div><span>STL · método determinista</span><strong>{stlFile?.name ?? 'world_load.stl predeterminado'}</strong></div><button onClick={onOpenLoad}>Cambiar</button></div>
            {notice && <p className="inline-notice">{notice}</p>}
            <button className="button primary full" disabled={creating || stepMaterialTags.length === 0 || stepMaterials.length !== stepMaterialTags.length} onClick={create}>{creating ? 'Creando caso…' : 'Crear caso de irradiación'}</button>
          </div>
        </section>

        <section className="surface cases-list">
          <header className="surface-header"><div><span className="section-label">Historial</span><h3>Casos disponibles</h3></div><span className="count-badge">{cases.length}</span></header>
          <div className="case-rows">
            {cases.length === 0 && <div className="empty-list">Todavía no hay casos.</div>}
            {cases.map((item) => <div key={item.case_id} className={selected?.case_id === item.case_id ? 'case-row active' : 'case-row'}><button className="case-row-select" onClick={() => void selectCase(item)}><span className="case-state"><Clock3 size={15} /></span><div><strong>{item.name}</strong><small>{item.description}</small><span className="case-step"><FileText size={12} /> STEP: {caseStepName(item)}</span><span className="case-step"><FileText size={12} /> STL: {caseStlName(item)}</span><span className="case-longitudinal"><MoveVertical size={12} /> {caseParameters(item).longitudinal_positions_mm.join(' · ')} mm</span><span className="case-profile">Configuración: {item.simulation_profile?.name ?? 'Configuración anterior'}</span><i>{new Date(item.created_at).toLocaleString(uiLocale())}</i></div><ChevronRight size={16} /></button><button className="case-row-delete" disabled={deletingCaseId === item.case_id} onClick={() => void deleteCase(item)} aria-label={`Eliminar caso ${item.name}`} title="Eliminar caso"><Trash2 size={16} /></button></div>)}
          </div>
        </section>
      </div>

      {selected && <section className="surface case-workspace">
        <header className="surface-header"><div><span className="section-label">Caso seleccionado</span><h3>{selected.name}</h3></div><span className="status-chip"><Clock3 size={14} /> {run.state}</span></header>
        <div className="case-work-grid">
          <div className="case-description">
            <label className="field"><span>Nombre del caso</span><input value={selected.name} onChange={(event) => setSelected({ ...selected, name: event.target.value })} /></label>
            <label className="field"><span>Descripción vinculada a la salida final</span><textarea className="prose-input" rows={4} value={selected.description} onChange={(event) => setSelected({ ...selected, description: event.target.value })} /></label>
            <div className="attached-file stored-step"><FileText size={17} /><div><span>STEP guardado en este caso</span><strong>{usePreparedStep && stepFile ? stepFile.name : caseStepName(selected)} · {usePreparedStep ? stepMaterials.length : selected.step_materials?.length ?? 0} materiales</strong></div><div className="stored-step-actions">{selected.step_input && <a href={`${API_URL}/api/cases/${selected.case_id}/inputs/step`} download>Descargar</a>}<button type="button" disabled={applyingMaterials || stepMaterialTags.length === 0 || stepMaterials.length !== stepMaterialTags.length} onClick={applyMaterialsToSelected}>{applyingMaterials ? 'Aplicando…' : `Aplicar ${stepMaterials.length} materiales`}</button><button type="button" onClick={() => { if (!stepFile) onOpenLoad(); else setUsePreparedStep(true); }}>{stepFile ? 'Usar STEP preparado' : 'Preparar reemplazo'}</button></div></div>
            <div className="attached-file stored-step"><FileText size={17} /><div><span>STL determinista guardado</span><strong>{caseStlFile?.name ?? caseStlName(selected)}</strong></div><div className="stored-step-actions">{selected.stl_input && <a href={`${API_URL}/api/cases/${selected.case_id}/inputs/stl`} download>Descargar</a>}<label><input type="file" accept=".stl" onChange={(event) => setCaseStlFile(event.target.files?.[0] ?? null)} />Reemplazar</label></div></div>
            <button className="button secondary" onClick={saveCase}><Save size={15} /> Guardar cambios del caso</button>
          </div>
          <div className="parameter-grid">
            <label className="field"><span>Sección de trabajo</span><select value={workSection} onChange={(event) => setWorkSection(event.target.value)}>{Object.entries(sections).map(([section, position]) => <option key={section} value={section}>{section} · [{position.join(', ')}] mm</option>)}</select></label>
            <label className="field"><span>Configuración de simulación</span><select value={caseProfileId} onChange={(event) => setCaseProfileId(event.target.value)}>{!caseProfileId && <option value="">Configuración anterior</option>}<option value="configuracion-actual">Configuración actual</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id} disabled={profileUsesPhits(profile) && !phitsAvailability?.available}>{profile.name}{profileUsesPhits(profile) && !phitsAvailability?.available ? ' · PHITS no disponible' : ''}</option>)}</select><small>Al guardar se reemplazará la instantánea de configuración del caso.</small></label>
            <div className="fixed-parameter"><span>Rango angular</span><strong>{angularSummary(selected)}</strong></div>
            <div className="fixed-parameter wide"><span>Desplazamiento vertical</span><strong>{caseParameters(selected).longitudinal_positions_mm.join(' · ')} mm</strong></div>
            <div className="fixed-parameter"><span>Puntos interiores</span><strong>{caseParameters(selected).num_points.toLocaleString(uiLocale())}</strong></div>
            <div className="fixed-parameter"><span>Núcleos CPU</span><strong>{caseParameters(selected).cores === -1 ? 'Todos los disponibles' : caseParameters(selected).cores}</strong></div>
            <div className="fixed-parameter"><span>Método Monte Carlo guardado</span><strong>{selectedMonteCarloMethod}</strong><small>Se actualiza únicamente al guardar los cambios del caso.</small></div>
            <div className="fixed-parameter"><span>Tamaño de celda Monte Carlo guardado</span><strong>{selectedCellSize.toLocaleString(uiLocale(), { maximumFractionDigits: 6 })} cm</strong><small>Los cambios de la configuración seleccionada se aplican solo al guardar el caso.</small></div>
            <div className="fixed-parameter"><span>Partículas Monte Carlo guardadas</span><strong>{selectedParticles.toLocaleString(uiLocale())}</strong><small>El valor pertenece a la instantánea guardada del caso.</small></div>
            <div className="fixed-parameter"><span>Batches Monte Carlo guardados</span><strong>{selectedBatches.toLocaleString(uiLocale())}</strong><small>{selectedDoseTally === 'phits' ? 'PHITS puede ajustar el valor efectivo al paralelismo MPI.' : 'OpenMC puede finalizar antes al alcanzar el objetivo estadístico.'}</small></div>
            <div className="fixed-parameter"><span>Objetivo de error relativo</span><strong>{(selectedRelativeErrorTarget * 100).toLocaleString(uiLocale(), { maximumFractionDigits: 2 })} %</strong><small>Objetivo estadístico de la instantánea guardada.</small></div>
            <div className="fixed-parameter"><span>Desfase inicial del CAD</span><strong>{caseSimulation(selected).movement.cad_rotation_degrees}°</strong></div>
          </div>
        </div>
        <div className="execution-grid">
          <article className="execution-card allowed"><Cpu size={21} /><div><span>Etapa disponible</span><strong>Optimización determinista</strong><p>Proceso separado con distribución multinúcleo.</p></div>{ACTIVE_RUN_STATES.includes(run.state) ? <button className="danger-button" disabled={stopping || run.state === 'cancelling'} onClick={stop}><Square size={14} fill="currentColor" /> {stopping || run.state === 'cancelling' ? 'Deteniendo…' : 'Detener simulación'}</button> : <button className="button primary" disabled={stopping} onClick={start}>Ejecutar</button>}</article>
          <article className={`execution-card ${ACTIVE_RUN_STATES.includes(monteCarloRun.state) || run.state === 'completed' ? 'allowed' : 'locked'}`}><LockKeyhole size={21} /><div><span>{selectedDoseTally === 'phits' && !phitsAvailability?.available ? 'Simulador no disponible' : ACTIVE_RUN_STATES.includes(monteCarloRun.state) ? 'Proceso activo' : monteCarloRun.state === 'completed' ? 'Validación completada' : run.state === 'completed' ? 'Etapa disponible' : 'Requiere resultado determinista'}</span><strong>Monte Carlo · {selectedMonteCarloEngine}</strong><p>{selectedDoseTally === 'phits' && !phitsAvailability?.available ? phitsAvailability?.message ?? 'Verificando la disponibilidad de PHITS…' : `Ejecuta ${selectedParticles.toLocaleString(uiLocale())} partículas; la detención incluye ${selectedMonteCarloEngine} y todos sus procesos paralelos.`}</p></div>{ACTIVE_RUN_STATES.includes(monteCarloRun.state) ? <button className="danger-button" disabled={stoppingMonteCarlo || monteCarloRun.state === 'cancelling'} onClick={stopMonteCarlo}><Square size={14} fill="currentColor" /> {stoppingMonteCarlo || monteCarloRun.state === 'cancelling' ? 'Deteniendo…' : 'Detener Monte Carlo'}</button> : <button className="button primary" disabled={run.state !== 'completed' || startingMonteCarlo || (selectedDoseTally === 'phits' && !phitsAvailability?.available)} onClick={startMonteCarlo}>{startingMonteCarlo ? 'Iniciando…' : monteCarloRun.state === 'completed' ? 'Ejecutar nuevamente' : 'Ejecutar Monte Carlo'}</button>}</article>
        </div>
        {run.state !== 'not-started' && <div className={`job-progress ${run.state}`}><div><Zap size={15} /><strong>{run.error || run.stage || run.state}</strong><span>{run.progress}%</span></div><div><i style={{ width: `${run.progress}%` }} /></div>{run.state === 'completed' && <p><CheckCircle2 size={14} /> Resultado determinista listo para revisión.</p>}</div>}
        {monteCarloRun.state !== 'not-started' && <div className={`job-progress ${monteCarloRun.state}`}><div><Zap size={15} /><strong>{selectedMonteCarloEngine} · {monteCarloRun.error || monteCarloRun.stage || monteCarloRun.state}</strong><span>{monteCarloRun.progress}%</span></div><div><i style={{ width: `${monteCarloRun.progress}%` }} /></div>{monteCarloRun.cpu_threads_used != null && <p className="job-resources"><Cpu size={14} /> {monteCarloRun.cpu_threads_used} núcleos CPU asignados{selectedDoseTally === 'phits' ? ' mediante MPI' : ' mediante OpenMP'}{monteCarloRun.particles != null ? ` · ${monteCarloRun.particles.toLocaleString(uiLocale())} partículas totales` : ''}</p>}</div>}
      </section>}

      {selected && deterministicResult && <section className="surface review-panel">
        <header className="surface-header"><div><span className="section-label">Paso 03 · Revisión</span><h3>Trayectoria determinista propuesta</h3></div><FileJson2 size={19} /></header>
        <div className="review-grid">
          <div><span>Ángulos seleccionados</span><strong>{deterministicResult.step_angle_degrees_best_real.join('° · ')}°</strong></div>
          <div><span>Niveles seleccionados</span><strong>{deterministicResult.best_length_points_mm.join(' · ')} mm</strong></div>
          <div><span>Dosis promedio</span><strong>{averageDose?.toFixed(5) ?? '—'}</strong></div>
        </div>
        <div className="review-actions">
          <p>{simulationsComplete ? 'Se guardarán todos los resultados del caso y su trayectoria optimizada.' : 'Completa la simulación Monte Carlo para guardar todos los resultados del caso.'}</p>
          {simulationsComplete && <button className="button primary" disabled={savingResults} onClick={saveResults}><Save size={16} /> {savingResults ? 'Guardando…' : 'Guardar resultados'}</button>}
        </div>
      </section>}
    </div>
  );
}
