'use client';
import { numberFormatter } from '../../lib/i18n/number';

import { studyPhase, type StudyProgressInput } from '../../lib/studyProgress';

export default function StudyProgressBar({ compact = false, ...input }: StudyProgressInput & { compact?: boolean }) {
  const { phase, coverage, indeterminate } = studyPhase(input);
  const suspended = input.state != null && ['paused', 'disk-limited', 'memory-limited', 'time-limited', 'interrupted', 'failed', 'cancelled'].includes(input.state);
  const queued = input.state === 'queued' || input.stage === 'waiting-for-resources';
  const finished = input.state === 'completed';
  const stageLabels: Record<string, string> = {
    'preparing-step-and-dagmc': 'Preparando geometría CAD',
    'building-openmc-model': 'Preparando modelo de OpenMC',
    'preparing-material-occupancy': 'Calculando material por celda',
    'running-openmc-transport': 'Transportando partículas en OpenMC',
    'postprocessing-statepoint': 'Generando mapa de dosis',
    'converting-step-to-phits-geometry': 'Convirtiendo el STEP a geometría PHITS',
    'checking-phits-runtime-and-geometry': 'Validando geometría y tetraedros en PHITS',
    'training-phits-photon-weight-windows': 'Preparando ventanas de peso en PHITS',
    'processing-phits-dose-statistics': 'Generando mapas y errores de PHITS',
  };
  const stageLabel = input.stage?.startsWith('running-phits-t-deposit-')
    ? 'Transportando partículas en PHITS' : input.stage ? stageLabels[input.stage] : undefined;
  const preparing = input.stage === 'preparing-material-occupancy' ? input.preparation : undefined;
  const label = finished ? 'Estudio finalizado' : input.state === 'paused' ? 'Estudio pausado · Avance guardado' : suspended ? 'Avance guardado' : queued ? 'En espera de recursos · Otro estudio está en ejecución' : input.state === 'pausing' ? 'Pausa solicitada · Terminando la posición actual' : phase === 'positions' ? stageLabel ?? 'Fase 1 · Generando mapas de posiciones'
    : phase === 'refining' ? 'Fase 2 · Refinando la precisión' : 'Fase 3 · Guardando resultados';
  const goal = input.target == null ? '' : numberFormatter({ maximumFractionDigits: 3 }).format(input.target * 100);
  return <div className={`study-phase-progress${compact ? ' compact' : ''}`}>
    <div className="study-phase-label"><strong>{label}</strong><span>{input.completed}/{input.total} mapas disponibles</span></div>
    <progress aria-label={label} value={indeterminate && !suspended && !finished ? undefined : coverage} max={100}/>
    {!compact && <p>{finished ? 'Los mapas están guardados y disponibles para analizar. Revisa la cobertura y la precisión del intervalo elegido.' : input.state === 'paused' ? 'El estudio está pausado. Los mapas guardados se conservan para continuar.' : suspended ? 'El estudio está detenido. Los mapas publicados permanecen disponibles para continuar y analizar los resultados.'
      : queued ? 'El cálculo comenzará cuando termine o se pause el estudio activo. Esta espera no consume el presupuesto de tiempo.'
      : input.state === 'pausing' ? 'La posición actual terminará y se guardará antes de liberar los recursos. Después podrás continuar sin repetir las posiciones completas.' : phase === 'refining'
      ? `Las ${input.total} posiciones ya tienen una estimación. Se añaden historias para reducir el error acumulado hasta ≤ ${goal} %, o hasta alcanzar el límite de recursos.`
      : phase === 'saving' ? 'Se están verificando y guardando los resultados del estudio.'
        : preparing ? `${preparing.completed}/${preparing.total} cuerpos procesados en la malla ${preparing.grid === 'evaluation' ? 'de evaluación' : 'de dosis'} · Hasta ${preparing.workers} en paralelo${preparing.cache_hits ? ` · ${preparing.cache_hits} recuperados de caché` : ''}.`
        : input.stage === 'running-openmc-transport' && input.transportThreads ? `OpenMC usa ${input.transportThreads} hilos para esta posición.`
        : 'Cada mapa se incorpora a Resultados cuando termina el cálculo de su posición.'}</p>}
  </div>;
}
