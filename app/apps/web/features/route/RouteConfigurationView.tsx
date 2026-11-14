'use client';

import { useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import RoutePositionPicker from './RoutePositionPicker';
import { apiGet, apiJson } from '../../lib/api';
import type { SimulationProfile } from '../../lib/types';
import AirDoseMethodNote from './AirDoseMethodNote';

export type RoutePosition = { name: string; center_mm: [number, number, number]; yaw_degrees: number };
export type RouteConfiguration = {
  positions: RoutePosition[];
  calculation_positions?: string[] | null;
  radionuclide: string;
  cpu_cores: number;
  particles: number;
  batches: number;
  cell_size_cm: number;
  relative_error_target: number;
  accumulated_error_target?: number;
  interval_max_particles?: number;
  interval_time_limit_seconds?: number; source_mixture_fraction?: number;
  dose_tally: 'flux' | 'heating' | 'alanine' | 'phits';
  occupancy: 'isolated' | 'occupied';
  position_reference: 'center' | 'bottom_center';
  variance_reduction: 'analog' | 'magic' | 'source_mixture' | 'source_box' | 'source_box_windows' | 'source_box_magic' | 'source_scene' | 'phits_importance' | 'phits_windows';
  cad_offset_degrees: number;
  deterministic_samples: number;
};

export const methodLabels: Record<RouteConfiguration['dose_tally'], string> = {
  flux: 'OpenMC rápido · flujo y conversión',
  heating: 'OpenMC intensivo · depósito directo',
  alanine: 'OpenMC alternativo · alanina',
  phits: 'PHITS · depósito directo EGS5',
};

export function VarianceOptions({ method }: { method: RouteConfiguration['dose_tally'] }) {
  return <>
    <option value="analog">Analógico · referencia isotrópica</option>
    {method === 'phits' ? <>
      <option value="phits_importance">PHITS · importancia regional 4:1</option>
      <option value="phits_windows">PHITS · piloto T-WWG y ventanas fotónicas</option>
    </> : <>
      <option value="source_box">OpenMC · fuente condicionada hacia la carga</option>
      <option value="source_scene">OpenMC · carga y vecinas (experimental)</option>
      <option value="source_box_magic">OpenMC · dirección condicionada + MAGIC experimental</option>
      <option value="source_box_windows">OpenMC · división local (experimental)</option>
      <option value="source_mixture">OpenMC · fuente mixta con corrección de pesos</option>
      <option value="magic">OpenMC · piloto MAGIC y ventanas fotónicas</option>
    </>}
  </>;
}

export function readableError(error: unknown) {
  if (!(error instanceof Error)) return 'No fue posible completar la operación.';
  try {
    const response = JSON.parse(error.message) as { detail?: string | { msg: string }[] };
    if (typeof response.detail === 'string') return response.detail;
    if (Array.isArray(response.detail)) return response.detail.map((detail) => detail.msg).join('. ');
  } catch { /* Browser errors are plain text. */ }
  return error.message;
}

type SavedRoute = { profile_id: string; name: string; parameters: RouteConfiguration };

export default function RouteConfigurationView({ onSave, revision, onPositions, onCalibration }: {
  onSave: () => void; revision: number; onPositions: () => void; onCalibration: () => void;
}) {
  const [settings, setSettings] = useState<RouteConfiguration>();
  const [selectionMode, setSelectionMode] = useState<'all' | 'selected'>('all');
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [nuclides, setNuclides] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<SimulationProfile[]>([]);
  const [savedRoutes, setSavedRoutes] = useState<SavedRoute[]>([]);
  const [profileId, setProfileId] = useState('');
  const [profileName, setProfileName] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let active = true;
    Promise.all([
      apiGet<RouteConfiguration>('/api/route/configuration'),
      apiGet<{ radionuclides: { id: string }[] }>('/api/config/radionuclides'),
      apiGet<{ profiles: SimulationProfile[] }>('/api/config/simulation-profiles'),
      apiGet<{ profiles: SavedRoute[] }>('/api/route/profiles'),
    ]).then(([configuration, catalog, savedProfiles, routes]) => {
      if (!active) return;
      setSettings(configuration);
      setSelectionMode(configuration.calculation_positions ? 'selected' : 'all');
      setSelectedNames(configuration.calculation_positions ?? []);
      setNuclides(catalog.radionuclides.map((item) => item.id));
      setProfiles(savedProfiles.profiles);
      setSavedRoutes(routes.profiles);
    }).catch((error) => { if (active) setMessage(readableError(error)); });
    return () => { active = false; };
  }, [revision]);

  function selectProfile(id: string) {
    setProfileId(id);
    const saved = savedRoutes.find((item) => `route:${item.profile_id}` === id);
    setProfileName(saved?.name ?? '');
    if (saved) {
      const names = saved.parameters.calculation_positions;
      const available = names?.filter((name) => settings?.positions.some((position) => position.name === name)) ?? [];
      setSettings((current) => current && ({ ...saved.parameters, positions: current.positions, position_reference: current.position_reference, cad_offset_degrees: current.cad_offset_degrees }));
      setSelectionMode(available.length ? 'selected' : 'all');
      setSelectedNames(available);
      if (names && available.length !== names.length) setMessage('El perfil menciona posiciones que ya no están en el recorrido actual. Revisa la selección.');
      return;
    }
    const profile = profiles.find((item) => item.profile_id === id);
    if (!profile) return;
    const p = profile.parameters;
    setSettings((current) => current && ({
      ...current, ...p.monte_carlo, radionuclide: p.radionuclide,
      cpu_cores: p.compute.cpu_cores,
      variance_reduction: 'analog',
    }));
  }

  async function deleteProfile() {
    if (!profileId.startsWith('route:')) return;
    const selected = savedRoutes.find((item) => `route:${item.profile_id}` === profileId);
    if (!selected || !window.confirm(`¿Eliminar el perfil «${selected.name}»?`)) return;
    setBusy(true);
    try {
      const result = await apiJson<{ profiles: SavedRoute[] }>(`/api/route/profiles/${selected.profile_id}`, 'DELETE');
      setSavedRoutes(result.profiles);
      setProfileId('');
      setProfileName('');
      setMessage('Perfil de recorrido eliminado.');
    } catch (error) { setMessage(readableError(error)); }
    finally { setBusy(false); }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!settings) return;
    setBusy(true);
    setMessage('');
    try {
      if (selectionMode === 'selected' && !selectedNames.length) throw new Error('Añade al menos una posición para guardar la selección.');
      const configuration = {
        ...settings,
        calculation_positions: selectionMode === 'selected'
          ? settings.positions.filter((point) => selectedNames.includes(point.name)).map((point) => point.name)
          : null,
      };
      await apiJson('/api/route/configuration', 'PUT', configuration);
      onSave();
      if (profileName.trim()) {
        const saved = await apiJson<SavedRoute>('/api/route/profiles', 'POST', { name: profileName.trim(), configuration });
        setSavedRoutes((current) => [...current.filter((item) => item.profile_id !== saved.profile_id), saved]);
        setProfileId(`route:${saved.profile_id}`);
      }
      setMessage('Configuración guardada para los casos nuevos.');
    } catch (error) {
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) return <div className="loading-panel" role="status">{message || 'Cargando configuración del recorrido…'}</div>;
  return (
    <form className="view-stack route-configuration-page" onSubmit={save} onInvalidCapture={(event) => { if (event.target instanceof HTMLElement) { const details = event.target.closest('details'); if (details) details.open = true; } }}>
      <section className="hero-row">
        <div><span className="section-label">Literas suspendidas</span><h2>Parámetros del recorrido</h2><p>Define el método y qué posiciones se calcularán por defecto en los casos nuevos.</p></div>

      </section>
      {message && <p className="inline-notice" role="status">{message}</p>}
      <section className="surface engine-configuration">
        <header className="surface-header"><div><h3>Método, ocupación y recursos de cálculo</h3><p>Las posiciones se evalúan en el sistema local de la carga.</p></div></header>
        <AirDoseMethodNote/>
        <div className="route-method-note"><span>Determinista · referencia en aire para calibración</span><button className="button secondary" type="button" onClick={onCalibration}>Abrir calibración</button></div>
        <div className="simulation-form-grid">
          <label className="field"><span>Método de cálculo</span><select value={settings.dose_tally} onChange={(event) => setSettings({ ...settings, dose_tally: event.target.value as RouteConfiguration['dose_tally'], variance_reduction: 'analog' })}>{Object.entries(methodLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          <label className="field"><span>Ocupación predeterminada</span><select value={settings.occupancy} onChange={(event) => setSettings({ ...settings, occupancy: event.target.value as RouteConfiguration['occupancy'] })}><option value="isolated">Una sola carga</option><option value="occupied">Ocupación completa · cargas vecinas</option></select></label>
          <label className="field"><span>Reducción de varianza</span><select value={settings.variance_reduction} onChange={(event) => setSettings({ ...settings, variance_reduction: event.target.value as RouteConfiguration['variance_reduction'] })}><VarianceOptions method={settings.dose_tally}/></select></label>
          <label className="field"><span>Error máximo de la suma del intervalo [%]</span><input required type="number" min={0.1} max={2} step="any" value={(settings.accumulated_error_target ?? .02) * 100} onChange={(event) => setSettings({ ...settings, accumulated_error_target: Number(event.target.value) / 100 })}/><small>Por defecto 2 % máximo en la acumulada. Se comprueban todos los vóxeles activos de la suma; cada intervalo y reparto de tiempos tiene su propio error.</small></label>
          <label className="field"><span>Presupuesto del intervalo [min]</span><input required type="number" min={1} max={1440} step={1} value={(settings.interval_time_limit_seconds ?? 18000) / 60} onChange={(event) => setSettings({ ...settings, interval_time_limit_seconds: Number(event.target.value) * 60 })}/><small>Es un máximo de tiempo. La estimación del caso se muestra en Resultados antes de calcular.</small></label>
          {settings.variance_reduction.startsWith('source_') && <label className="field"><span>Historias orientadas hacia la carga [%]</span><input required type="number" min={1} max={99} step={1} value={(settings.source_mixture_fraction ?? .5) * 100} onChange={(event) => setSettings({ ...settings, source_mixture_fraction: Number(event.target.value) / 100 })}/><small>El resto es isotrópico. Para el piloto de agua con aire se utilizó 50 %.</small></label>}
          <label className="field"><span>Objetivo por posición · modo independiente [%]</span><input required type="number" min={0.1} max={10} step="any" value={settings.relative_error_target * 100} onChange={(event) => setSettings({ ...settings, relative_error_target: Number(event.target.value) / 100 })}/><small>Menos del 10 % inicial y menos del 5 % recomendado. Se verifica el máximo entre los vóxeles activos.</small></label>
          <label className="field"><span>Radionúclido</span><select value={settings.radionuclide} onChange={(event) => setSettings({ ...settings, radionuclide: event.target.value })}>{nuclides.map((id) => <option key={id}>{id}</option>)}</select></label>
          <label className="field"><span>Límite de historias por posición para el intervalo</span><input required type="number" min={2} max={1e12} step={1} value={settings.interval_max_particles ?? 300_000_000} onChange={(event) => setSettings({ ...settings, interval_max_particles: Number(event.target.value) })}/><small>Es un límite, no el total a simular. Solo se utiliza si se habilita el refinamiento automático; actualmente se calcula con un presupuesto fijo.</small></label>
          <label className="field"><span>Historias por posición · modo fijo</span><input required type="number" min={settings.batches} max={1e12} value={settings.particles || ''} onChange={(event) => setSettings({ ...settings, particles: Number(event.target.value) })}/><small>Predeterminado general: 100 millones de historias por posición. Es un presupuesto inicial; no garantiza el 2 % en cualquier carga o intervalo.</small></label>
          <label className="field"><span>Lotes por posición</span><input required type="number" min={2} max={10000} value={settings.batches || ''} onChange={(event) => setSettings({ ...settings, batches: Number(event.target.value) })}/></label>
          <label className="field"><span>Tamaño de celda [cm]</span><input required type="number" min={0.001} max={100} step="any" value={settings.cell_size_cm || ''} onChange={(event) => setSettings({ ...settings, cell_size_cm: Number(event.target.value) })}/></label>
          <label className="field"><span>Núcleos de CPU</span><input required type="number" min={-1} value={settings.cpu_cores} onChange={(event) => setSettings({ ...settings, cpu_cores: Number(event.target.value) })}/><small>−1 usa los núcleos disponibles.</small></label>
        </div>
      </section>
      <details className="surface configuration-disclosure">
        <summary><span>Posiciones de cálculo</span><small>{selectionMode === 'all' ? 'Recorrido completo' : 'Posiciones seleccionadas'} · {selectionMode === 'all' ? settings.positions.length : selectedNames.length}</small></summary>
        <header className="surface-header"><div><h3>Posiciones de cálculo</h3><p>Las coordenadas y los giros Z se toman de Posiciones. Confirma allí los giros de las transiciones y de la transferencia al nivel superior.</p></div>
          <button type="button" className="button secondary" onClick={onPositions}>Abrir Posiciones</button>
        </header>
        <label className="field"><span>Alcance predeterminado</span><select value={selectionMode} onChange={(event) => setSelectionMode(event.target.value as typeof selectionMode)}>
          <option value="all">Recorrido completo · {settings.positions.length} posiciones</option>
          <option value="selected">Posiciones seleccionadas</option>
        </select></label>
        {selectionMode === 'selected' && <RoutePositionPicker positions={settings.positions} yawOffset={settings.cad_offset_degrees} selected={selectedNames} onChange={setSelectedNames}/>}
      </details>
      <details className="surface configuration-disclosure"><summary><span>Perfiles de simulación</span><small>Cargar o guardar una configuración reutilizable</small></summary>
        <header className="surface-header"><div><h3>Perfiles de simulación</h3><p>Aplica los parámetros de un perfil guardado a la tabla de posiciones actual.</p></div></header>
        <div className="simulation-form-grid">
          <label className="field"><span>Configuración guardada</span><select value={profileId} onChange={(event) => selectProfile(event.target.value)}><option value="">Configuración actual</option>
            {savedRoutes.length > 0 && <optgroup label="Perfiles del recorrido">{savedRoutes.map((item) => <option key={item.profile_id} value={`route:${item.profile_id}`}>{item.name}</option>)}</optgroup>}
            <optgroup label="Perfiles originales">{profiles.map((item) => <option key={item.profile_id} value={item.profile_id}>{item.name}</option>)}</optgroup>
          </select></label>
          <label className="field"><span>Nombre del perfil de recorrido</span><input maxLength={120} value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="Opcional"/><small>Completa este campo para guardar también un perfil reutilizable.</small></label>
          {profileId.startsWith('route:') && <button type="button" className="button secondary" disabled={busy} onClick={() => void deleteProfile()}>Eliminar perfil de recorrido</button>}
        </div>
      </details>
      <footer className="configuration-save-bar"><span>Guardar como configuración del irradiador para nuevos casos.</span><button className="button primary" disabled={busy}><Save size={14}/>{busy ? 'Guardando…' : 'Guardar configuración'}</button></footer>
    </form>
  );
}
