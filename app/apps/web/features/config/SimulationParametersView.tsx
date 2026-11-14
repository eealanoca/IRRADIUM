'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useEffect, useMemo, useState } from 'react';
import { Cpu, FlaskConical, MoveVertical, RotateCw, Save, SlidersHorizontal, Trash2 } from 'lucide-react';
import { apiGet, apiJson } from '../../lib/api';
import type { SimulationBackendsResponse, SimulationParameters, SimulationProfile, WorkSectionsResponse } from '../../lib/types';

type RadionuclideOption = { id: string; name: string; half_life_days: number };
type MonteCarloMethod = 'flux' | 'heating' | 'alanine' | 'phits';

const MONTE_CARLO_PRESETS: Record<MonteCarloMethod, {
  particles: number;
  batches: number;
  cellSizeCm: number;
  relativeErrorTarget: number;
  varianceReduction: string;
}> = {
  flux: {
    particles: 100_000_000,
    batches: 100,
    cellSizeCm: 2,
    relativeErrorTarget: 0.03,
    varianceReduction: 'Direccionamiento angular dinámico hacia la carga',
  },
  heating: {
    particles: 1_000_000_000,
    batches: 100,
    cellSizeCm: 2,
    relativeErrorTarget: 0.03,
    varianceReduction: 'Direccionamiento angular dinámico y calentamiento por material',
  },
  alanine: {
    particles: 200_000_000,
    batches: 100,
    cellSizeCm: 2,
    relativeErrorTarget: 0.03,
    varianceReduction: 'Direccionamiento angular dinámico y respuesta espectral de alanina',
  },
  phits: {
    particles: 1_000_000,
    batches: 20,
    cellSizeCm: 4,
    relativeErrorTarget: 0.03,
    varianceReduction: 'Direccionamiento angular dinámico hacia toda la carga + importancia fotónica regional 4:1',
  },
};

const DEFAULT_MONTE_CARLO = MONTE_CARLO_PRESETS.flux;

function formatParticles(value: number) {
  return String(value);
}

const DEFAULT_PARAMETERS: SimulationParameters = {
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
    cell_size_cm: DEFAULT_MONTE_CARLO.cellSizeCm,
    particles: DEFAULT_MONTE_CARLO.particles,
    batches: DEFAULT_MONTE_CARLO.batches,
    relative_error_target: DEFAULT_MONTE_CARLO.relativeErrorTarget,
    dose_tally: 'flux',
  },
};

function normalizeParameters(value: SimulationParameters): SimulationParameters {
  return {
    ...DEFAULT_PARAMETERS,
    ...value,
    movement: { ...DEFAULT_PARAMETERS.movement, ...value.movement },
    deterministic: { ...DEFAULT_PARAMETERS.deterministic, ...value.deterministic },
    compute: { ...DEFAULT_PARAMETERS.compute, ...value.compute },
    monte_carlo: { ...DEFAULT_PARAMETERS.monte_carlo, ...value.monte_carlo },
  };
}

function rangeValues(start: number, stop: number, step: number) {
  if (step <= 0 || start > stop) return [];
  const values: number[] = [];
  for (let value = start; value <= stop; value += step) values.push(value);
  return values;
}

export default function SimulationParametersView({ onProfilesChanged }: { onProfilesChanged?: () => void }) {
  const [parameters, setParameters] = useState<SimulationParameters>(DEFAULT_PARAMETERS);
  const [profiles, setProfiles] = useState<SimulationProfile[]>([]);
  const [profileName, setProfileName] = useState('Configuración base');
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [sections, setSections] = useState<string[]>(['A']);
  const [radionuclides, setRadionuclides] = useState<RadionuclideOption[]>([
    { id: 'Co-60', name: 'Cobalto-60', half_life_days: 1925.23 },
  ]);
  const [particlesInput, setParticlesInput] = useState(String(DEFAULT_MONTE_CARLO.particles));
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [phitsAvailability, setPhitsAvailability] = useState<SimulationBackendsResponse['phits'] | null>(null);

  useEffect(() => {
    Promise.all([
      apiGet<SimulationParameters>('/api/config/simulation-parameters'),
      apiGet<WorkSectionsResponse>('/api/config/work-sections'),
      apiGet<{ profiles: SimulationProfile[] }>('/api/config/simulation-profiles'),
      apiGet<{ radionuclides: RadionuclideOption[] }>('/api/config/radionuclides'),
    ])
      .then(([simulation, workSections, savedProfiles, radionuclideData]) => {
        const normalized = normalizeParameters(simulation);
        setParameters(normalized);
        setParticlesInput(formatParticles(normalized.monte_carlo.particles));
        setSections(Object.keys(workSections.positions));
        setProfiles(savedProfiles.profiles);
        setRadionuclides(radionuclideData.radionuclides);
      })
      .catch(() => setNotice('No fue posible cargar los parámetros de simulación.'));
  }, []);

  useEffect(() => {
    apiGet<SimulationBackendsResponse>('/api/simulation/backends')
      .then((response) => setPhitsAvailability(response.phits))
      .catch(() => setPhitsAvailability({
        available: false,
        message: 'No fue posible verificar la disponibilidad de PHITS.',
      }));
  }, []);

  const machineAngles = useMemo(() => rangeValues(
    parameters.movement.machine_angle_start_degrees,
    parameters.movement.machine_angle_stop_degrees - parameters.movement.machine_angle_step_degrees,
    parameters.movement.machine_angle_step_degrees,
  ), [parameters.movement]);

  const longitudinalPositions = useMemo(() => [...new Set([
    ...rangeValues(
      parameters.movement.length_complete_lower_range_mm[0],
      parameters.movement.length_complete_lower_range_mm[1],
      parameters.movement.resolution_length_mm,
    ),
    ...rangeValues(
      parameters.movement.length_complete_upper_range_mm[0],
      parameters.movement.length_complete_upper_range_mm[1],
      parameters.movement.resolution_length_mm,
    ),
  ])].sort((left, right) => left - right), [parameters.movement]);

  const updateMovement = <Key extends keyof SimulationParameters['movement']>(key: Key, value: SimulationParameters['movement'][Key]) => {
    setParameters((current) => ({ ...current, movement: { ...current.movement, [key]: value } }));
  };

  const commitParticles = () => {
    const normalized = Number(particlesInput.replaceAll(' ', ''));
    if (!Number.isInteger(normalized) || normalized < 1) {
      setParticlesInput(formatParticles(parameters.monte_carlo.particles));
      setNotice('El número de partículas debe ser un entero mayor que cero.');
      return;
    }
    setParameters((current) => ({
      ...current,
      monte_carlo: { ...current.monte_carlo, particles: normalized },
    }));
    setParticlesInput(formatParticles(normalized));
  };

  const selectMonteCarloMethod = (doseTally: MonteCarloMethod) => {
    if (doseTally === 'phits' && !phitsAvailability?.available) {
      setNotice(phitsAvailability?.message ?? 'PHITS no está disponible en este sistema.');
      return;
    }
    const preset = MONTE_CARLO_PRESETS[doseTally];
    setParameters((current) => ({
      ...current,
      monte_carlo: {
        ...current.monte_carlo,
        dose_tally: doseTally,
        particles: preset.particles,
        batches: preset.batches,
        cell_size_cm: preset.cellSizeCm,
        relative_error_target: preset.relativeErrorTarget,
      },
    }));
    setParticlesInput(formatParticles(preset.particles));
    setNotice('');
  };

  const selectProfile = (profileId: string) => {
    setSelectedProfileId(profileId);
    const profile = profiles.find((item) => item.profile_id === profileId);
    if (!profile) return;
    const normalized = normalizeParameters(profile.parameters);
    setParameters(normalized);
    setParticlesInput(formatParticles(normalized.monte_carlo.particles));
    setProfileName(profile.name);
    setNotice(
      normalized.monte_carlo.dose_tally === 'phits' && !phitsAvailability?.available
        ? phitsAvailability?.message ?? 'PHITS no está disponible en este sistema.'
        : `Configuración «${profile.name}» cargada para edición.`,
    );
  };

  const save = async () => {
    if (!profileName.trim()) {
      setNotice('Asigna un nombre a la configuración antes de guardarla.');
      return;
    }
    if (parameters.monte_carlo.dose_tally === 'phits' && !phitsAvailability?.available) {
      setNotice(phitsAvailability?.message ?? 'PHITS no está disponible en este sistema.');
      return;
    }
    const pendingParticles = Number(particlesInput.replaceAll(' ', ''));
    if (!Number.isSafeInteger(pendingParticles) || pendingParticles < 1) {
      setNotice('El número de partículas debe ser un entero positivo que pueda representarse con precisión.');
      return;
    }
    if (!Number.isInteger(parameters.monte_carlo.batches) || parameters.monte_carlo.batches < 2) {
      setNotice('El número de batches debe ser un entero mayor o igual que dos.');
      return;
    }
    if (!(parameters.monte_carlo.relative_error_target > 0 && parameters.monte_carlo.relative_error_target < 1)) {
      setNotice('El objetivo de error relativo debe estar entre 0 y 100 %.');
      return;
    }
    const parametersToSave: SimulationParameters = {
      ...parameters,
      monte_carlo: {
        ...parameters.monte_carlo,
        particles: pendingParticles,
      },
    };
    setSaving(true);
    setNotice('');
    try {
      const profile = await apiJson<SimulationProfile>('/api/config/simulation-profiles', 'POST', {
        name: profileName.trim(),
        parameters: parametersToSave,
      });
      const saved = normalizeParameters(profile.parameters);
      setParameters(saved);
      setParticlesInput(formatParticles(saved.monte_carlo.particles));
      setSelectedProfileId(profile.profile_id);
      setProfiles((current) => [
        ...current.filter((item) => item.profile_id !== profile.profile_id),
        profile,
      ].sort((left, right) => left.name.localeCompare(right.name, 'es')));
      onProfilesChanged?.();
      setNotice(`Configuración «${profile.name}» guardada y disponible para los casos de irradiación.`);
    } catch (error) {
      const details = error instanceof Error ? ` ${error.message}` : '';
      setNotice(`No fue posible guardar la configuración.${details}`);
    } finally {
      setSaving(false);
    }
  };

  const deleteSelectedProfile = async () => {
    const profile = profiles.find((item) => item.profile_id === selectedProfileId);
    if (!profile) return;
    const confirmed = window.confirm(
      `¿Eliminar la configuración «${profile.name}»? Los casos existentes conservarán su instantánea.`,
    );
    if (!confirmed) return;

    setDeleting(true);
    setNotice('');
    try {
      const response = await apiJson<{ profiles: SimulationProfile[] }>(
        `/api/config/simulation-profiles/${profile.profile_id}`,
        'DELETE',
      );
      setProfiles(response.profiles);
      setSelectedProfileId('');
      setProfileName('Nueva configuración');
      onProfilesChanged?.();
      setNotice(`Configuración «${profile.name}» eliminada. Los casos existentes no fueron modificados.`);
    } catch {
      setNotice('No fue posible eliminar la configuración seleccionada.');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="view-stack">
      <section className="hero-row">
        <div><span className="section-label">Configuración global</span><h2>Parámetros de simulación</h2><p>Define configuraciones reutilizables de movimiento, muestreo determinista y recursos de cálculo.</p></div>
        <button className="button primary" disabled={saving} onClick={save}><Save size={16} /> {saving ? 'Guardando…' : 'Guardar configuración'}</button>
      </section>

      <section className="surface simulation-profile-card">
        <div className="simulation-profile-controls">
          <div className="simulation-profile-selector">
            <label className="field"><span>Configuración guardada</span><select value={selectedProfileId} onChange={(event) => selectProfile(event.target.value)}><option value="">Configuración actual</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>)}</select><small>Carga un perfil sin modificar los casos que ya fueron creados.</small></label>
            <button className="button profile-delete" disabled={!selectedProfileId || deleting} onClick={deleteSelectedProfile}><Trash2 size={15} /> {deleting ? 'Eliminando…' : 'Eliminar configuración'}</button>
          </div>
          <label className="field"><span>Nombre de la configuración</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="Ej. Rack A · carga estándar" /><small>Al guardar quedará disponible al crear un nuevo caso.</small></label>
        </div>
      </section>

      <div className="simulation-summary">
        <div><RotateCw size={18}/><span>Ángulos de máquina</span><strong>{machineAngles.length} posiciones</strong><small>{parameters.movement.machine_angle_start_degrees}°–{parameters.movement.machine_angle_stop_degrees - parameters.movement.machine_angle_step_degrees}° · incremento {parameters.movement.machine_angle_step_degrees}°</small></div>
        <div><MoveVertical size={18}/><span>Desplazamiento longitudinal</span><strong>{longitudinalPositions.length} posiciones</strong><small>{longitudinalPositions.join(' · ')} mm</small></div>
        <div><Cpu size={18}/><span>Muestreo determinista</span><strong>{parameters.deterministic.num_points.toLocaleString(uiLocale())}</strong><small>puntos interiores</small></div>
      </div>

      <section className="surface simulation-config-card">
        <header className="surface-header"><div><span className="section-label">Parámetros generales</span><h3>Movimiento y búsqueda del optimizador</h3></div><SlidersHorizontal size={19}/></header>
        <div className="simulation-form-grid">
          <label className="field"><span>Radionúclido</span><select value={parameters.radionuclide} onChange={(event) => setParameters((current) => ({ ...current, radionuclide: event.target.value }))}>{radionuclides.map((radionuclide) => <option key={radionuclide.id} value={radionuclide.id}>{radionuclide.id} · {radionuclide.name}</option>)}</select><small>Opciones disponibles según los datos físicos configurados.</small></label>
          <label className="field"><span>Sección de trabajo predeterminada</span><select value={parameters.movement.default_work_section} onChange={(event) => updateMovement('default_work_section', event.target.value)}>{sections.map((section) => <option key={section}>{section}</option>)}</select><small>La sección puede cambiarse para cada caso.</small></label>

          <div className="simulation-field-group full"><h4>Ángulos disponibles en la máquina</h4><div>
            <label className="field"><span>Inicio [°]</span><input type="number" value={parameters.movement.machine_angle_start_degrees} onChange={(event) => updateMovement('machine_angle_start_degrees', Number(event.target.value))} /></label>
            <label className="field"><span>Final exclusivo [°]</span><input type="number" value={parameters.movement.machine_angle_stop_degrees} onChange={(event) => updateMovement('machine_angle_stop_degrees', Number(event.target.value))} /></label>
            <label className="field"><span>Incremento disponible [°]</span><input type="number" min="1" value={parameters.movement.machine_angle_step_degrees} onChange={(event) => updateMovement('machine_angle_step_degrees', Number(event.target.value))} /></label>
          </div></div>

          <div className="simulation-field-group full"><h4>Cantidad de posiciones angulares evaluadas</h4><p className="simulation-group-help">Cada valor N define un grupo de N posiciones angulares equidistantes durante una revolución. El límite máximo mostrado también se evalúa.</p><div>
            <label className="field"><span>Mínimo de posiciones por revolución</span><input type="number" min="1" value={parameters.movement.angular_positions_range[0]} onChange={(event) => updateMovement('angular_positions_range', [Number(event.target.value), parameters.movement.angular_positions_range[1]])} /></label>
            <label className="field"><span>Máximo de posiciones por revolución</span><input type="number" min="1" value={parameters.movement.angular_positions_range[1] - 1} onChange={(event) => updateMovement('angular_positions_range', [parameters.movement.angular_positions_range[0], Number(event.target.value) + 1])} /></label>
          </div></div>

          <label className="field"><span>Desfase inicial del CAD [°]</span><input type="number" value={parameters.movement.cad_rotation_degrees} onChange={(event) => updateMovement('cad_rotation_degrees', Number(event.target.value))} /></label>

          <div className="simulation-field-group full"><h4>Desplazamiento longitudinal respecto al centro geométrico</h4><div className="longitudinal-range-grid">
            <div className="longitudinal-range-row">
              <label className="field"><span>Rango inferior · inicio [mm]</span><input type="number" value={parameters.movement.length_complete_lower_range_mm[0]} onChange={(event) => updateMovement('length_complete_lower_range_mm', [Number(event.target.value), parameters.movement.length_complete_lower_range_mm[1]])} /></label>
              <label className="field"><span>Rango inferior · final [mm]</span><input type="number" value={parameters.movement.length_complete_lower_range_mm[1]} onChange={(event) => updateMovement('length_complete_lower_range_mm', [parameters.movement.length_complete_lower_range_mm[0], Number(event.target.value)])} /></label>
            </div>
            <div className="longitudinal-range-row">
              <label className="field"><span>Rango superior · inicio [mm]</span><input type="number" value={parameters.movement.length_complete_upper_range_mm[0]} onChange={(event) => updateMovement('length_complete_upper_range_mm', [Number(event.target.value), parameters.movement.length_complete_upper_range_mm[1]])} /></label>
              <label className="field"><span>Rango superior · final [mm]</span><input type="number" value={parameters.movement.length_complete_upper_range_mm[1]} onChange={(event) => updateMovement('length_complete_upper_range_mm', [parameters.movement.length_complete_upper_range_mm[0], Number(event.target.value)])} /></label>
            </div>
            <div className="longitudinal-range-row">
              <label className="field"><span>Resolución [mm]</span><input type="number" min="1" value={parameters.movement.resolution_length_mm} onChange={(event) => updateMovement('resolution_length_mm', Number(event.target.value))} /></label>
            </div>
          </div></div>

          <label className="field"><span>Límite de núcleos CPU</span><input type="number" min="-1" value={parameters.compute.cpu_cores} onChange={(event) => setParameters((current) => ({ ...current, compute: { cpu_cores: Number(event.target.value) } }))} /><small>−1 utiliza todos los núcleos disponibles. Es el límite general para el optimizador DUR y los métodos Monte Carlo.</small></label>
        </div>
      </section>

      <section className="surface simulation-config-card">
        <header className="surface-header"><div><span className="section-label">Método determinista</span><h3>Muestreo de la carga</h3></div><Cpu size={19}/></header>
        <div className="simulation-form-grid">
          <label className="field"><span>Puntos interiores</span><input type="number" min="100" value={parameters.deterministic.num_points} onChange={(event) => setParameters((current) => ({ ...current, deterministic: { num_points: Number(event.target.value) } }))} /><small>Cantidad de puntos interiores generados en la geometría de la carga.</small></label>
        </div>
      </section>

      <section className="surface simulation-config-card">
        <header className="surface-header"><div><span className="section-label">Recursos y discretización</span><h3>Configuración métodos de Monte Carlo</h3></div><FlaskConical size={19}/></header>
        <div className="simulation-form-grid">
          <label className="field"><span>Método de cálculo</span><select value={parameters.monte_carlo.dose_tally} onChange={(event) => selectMonteCarloMethod(event.target.value as MonteCarloMethod)}><option value="flux">Cálculo rápido</option><option value="heating">Cálculo intensivo</option><option value="alanine">Cálculo alternativo</option><option value="phits" disabled={!phitsAvailability?.available}>Cálculo Comparativo PHITS{phitsAvailability?.available ? '' : ' · no disponible'}</option></select>{parameters.monte_carlo.dose_tally === 'phits' && !phitsAvailability?.available && <small className="availability-warning">{phitsAvailability?.message ?? 'Verificando la disponibilidad de PHITS…'}</small>}</label>
          <label className="field"><span>Tamaño de celda [cm]</span><input type="number" min="0.01" step="0.01" value={parameters.monte_carlo.cell_size_cm} onChange={(event) => setParameters((current) => ({ ...current, monte_carlo: { ...current.monte_carlo, cell_size_cm: Number(event.target.value) } }))} /></label>
          <label className="field"><span>Número máximo de partículas</span><input type="text" inputMode="numeric" value={particlesInput} onChange={(event) => setParticlesInput(event.target.value)} onBlur={commitParticles} aria-label="Número máximo de partículas" /><small>{parameters.monte_carlo.dose_tally === 'phits' ? 'Presupuesto total de historias PHITS; no se multiplica por el número de batches.' : 'Presupuesto total; no se multiplica por el número de batches. OpenMC puede finalizar antes al alcanzar el objetivo.'}</small></label>
          <label className="field"><span>Número de batches</span><input type="number" min="2" max="10000" step="1" value={parameters.monte_carlo.batches} onChange={(event) => setParameters((current) => ({ ...current, monte_carlo: { ...current.monte_carlo, batches: Number(event.target.value) } }))} /><small>{parameters.monte_carlo.dose_tally === 'phits' ? 'PHITS ajusta este valor a un múltiplo compatible con los procesos MPI y muestra después el valor efectivo.' : 'OpenMC usa este máximo de realizaciones estadísticas y puede finalizar antes al alcanzar el objetivo.'}</small></label>
          <label className="field"><span>Objetivo de error relativo [%]</span><input type="number" min="0.1" max="99" step="0.1" value={parameters.monte_carlo.relative_error_target * 100} onChange={(event) => setParameters((current) => ({ ...current, monte_carlo: { ...current.monte_carlo, relative_error_target: Number(event.target.value) / 100 } }))} /><small>Objetivo recomendado: 3 %. Se acepta P90 cercano o inferior a 5 %.</small></label>
          <label className="field full"><span>Método de reducción de varianza</span><input value={MONTE_CARLO_PRESETS[parameters.monte_carlo.dose_tally].varianceReduction} readOnly /><small>Asignado automáticamente al método validado; no es editable para evitar combinaciones físicas sin comprobar.</small></label>
        </div>
      </section>

      {notice && <p className="inline-notice">{notice}</p>}
    </div>
  );
}
