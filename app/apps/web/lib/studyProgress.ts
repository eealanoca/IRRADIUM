export type StudyProgressInput = {
  completed: number; total: number; target?: number | null;
  maximum?: number | null; stage?: string; state?: string;
  preparation?: { completed: number; total: number; workers: number; grid: string; cache_hits?: number };
  transportThreads?: number;
};

export function requestedPositionCount(study?: { position_names?: string[] | null; start?: number; end?: number }) {
  if (study?.position_names?.length) return study.position_names.length;
  return study?.start != null && study.end != null && study.end >= study.start ? study.end - study.start + 1 : undefined;
}

export function preferredActiveJobIndex(states: string[]) {
  const running = states.findIndex((state) => ['running', 'pausing', 'cancelling'].includes(state));
  return running >= 0 ? running : states.findIndex((state) => state === 'queued');
}

/** Coverage and statistical convergence are separate milestones, not one percentage. */
export function studyPhase(input: StudyProgressInput) {
  const covered = input.total > 0 && input.completed >= input.total;
  const converged = input.target != null && input.maximum != null && input.maximum <= input.target;
  const phase = input.stage === 'finalizing-study' || (covered && (!input.target || converged))
    ? 'saving' : covered ? 'refining' : 'positions';
  const coverage = input.total > 0 ? Math.max(0, Math.min(100, input.completed * 100 / input.total)) : 0;
  return { phase, coverage, indeterminate: phase !== 'positions' || input.total === 0 } as const;
}
