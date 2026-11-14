export type IrradiatorCategory = 'I' | 'II' | 'III' | 'IV';
export type IrradiatorType = {
  id: IrradiatorCategory;
  name_en: string;
  name_es: string;
  source_storage: 'dry' | 'wet';
  geometry: 'self-contained' | 'panoramic';
  enabled: boolean;
  workflow: string | null;
  transport?: string;
};

export type AppSection = 'cases' | 'load' | 'sources' | 'simulation' | 'materials' | 'sections' | 'results' | 'positions';

export type GammaSource = {
  id: string;
  index: number;
  radionuclide: string;
  initial_activity_bq: number;
  current_activity_bq: number;
  initial_activity_kci: number;
  current_activity_kci: number;
  remaining_fraction: number;
  initial_reading_date: string;
  lower_point_mm: [number, number, number];
  upper_point_mm: [number, number, number];
  center_point_mm: [number, number, number];
  active_length_mm: number;
  diameter_mm?: number;
  status: string;
};

export type SourcesResponse = {
  reference_date: string;
  radionuclide: string;
  half_life_days: number;
  count: number;
  total_initial_activity_bq: number;
  total_current_activity_bq: number;
  total_initial_activity_kci: number;
  total_current_activity_kci: number;
  sources: GammaSource[];
};

export type Material = {
  name: string;
  transport_medium: string;
  dosimeter_medium: string;
  coefficient_quantity: string;
  coefficient_units: string;
  coefficient_source: string;
  elements: Record<string, number>;
  density: number;
  fraction_type?: 'atomic' | 'weight';
  mu_en_over_rho: Record<string, number>;
};

export type StepMaterial = {
  name: string;
  elements: Record<string, number>;
  density: number;
  fraction_type: 'atomic' | 'weight';
};

export type WorkSectionsResponse = { positions: Record<string, [number, number, number]> };

export type DeterministicCaseParameters = {
  work_section: string;
  num_points: number;
  cores: number;
  longitudinal_positions_mm: number[];
};

export type SimulationParameters = {
  radionuclide: string;
  movement: {
    default_work_section: string;
    machine_angle_start_degrees: number;
    machine_angle_stop_degrees: number;
    machine_angle_step_degrees: number;
    angular_positions_range: [number, number];
    cad_rotation_degrees: number;
    length_complete_lower_range_mm: [number, number];
    length_complete_upper_range_mm: [number, number];
    resolution_length_mm: number;
  };
  deterministic: { num_points: number };
  compute: { cpu_cores: number };
  monte_carlo: {
    cell_size_cm: number;
    particles: number;
    batches: number;
    relative_error_target: number;
    dose_tally: 'flux' | 'heating' | 'alanine' | 'phits';
  };
};

export type SimulationProfile = {
  profile_id: string;
  name: string;
  parameters: SimulationParameters;
};

export type SimulationBackendsResponse = {
  monte_carlo: { id: string; execution_enabled: boolean }[];
  active_backend: string;
  phits: { available: boolean; parallel_mode?: string; message?: string };
};

export type IrradiationCase = {
  workflow?: string;
  analysis_method?: 'deterministic_air' | 'monte_carlo_air';
  calibration_case_id?: string;
  irradiator_type: IrradiatorCategory;
  case_id: string;
  name: string;
  description: string;
  created_at: string;
  state: string;
  current_step: string;
  input_snapshot: string[];
  step_input?: {
    kind: 'STEP';
    original_name: string;
    stored_name: string;
    size_bytes: number;
  } | null;
  stl_input?: {
    kind: 'STL';
    original_name: string;
    stored_name: string;
    size_bytes: number;
  } | null;
  step_material_tags?: string[];
  step_materials?: StepMaterial[];
  deterministic_parameters?: DeterministicCaseParameters;
  simulation_parameters?: SimulationParameters;
  simulation_profile?: { profile_id: string; name: string };
  monte_carlo_execution_enabled: boolean;
};
