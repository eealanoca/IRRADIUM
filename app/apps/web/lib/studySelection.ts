export function selectStudyRun(
  studies: { run_id: string }[],
  manualRun?: string,
  pendingRun?: string,
  currentRun?: string,
): string {
  const available = new Set(studies.map((study) => study.run_id));
  // A newly submitted run has not published its first position yet. Do not
  // silently present the previous run as the new calculation's results.
  if (pendingRun) return available.has(pendingRun) ? pendingRun : '';
  for (const candidate of [manualRun, currentRun]) {
    if (candidate && available.has(candidate)) return candidate;
  }
  if (!manualRun && currentRun) return '';
  return studies[0]?.run_id ?? '';
}

export function selectStudyCase(
  cases: { case_id: string }[],
  activeCases: string[],
  followActive: boolean,
  preferredCase = '',
  currentCase = '',
): string {
  const available = new Set(cases.map((item) => item.case_id));
  if (followActive) {
    if (activeCases.includes(currentCase) && available.has(currentCase)) return currentCase;
    const running = activeCases.find((id) => available.has(id));
    if (running) return running;
  }
  const preference = followActive ? [currentCase, preferredCase] : [preferredCase, currentCase];
  return preference.find((id) => available.has(id)) ?? cases[0]?.case_id ?? '';
}

export function availableStudyInterval(count: number, selectedStart = 0, selectedEnd?: number) {
  const last = Math.max(0, count - 1);
  const start = Math.max(0, Math.min(last, selectedStart));
  return { start, end: Math.max(start, Math.min(last, selectedEnd ?? last)) };
}

export function studyCoverage(completed: number, requested: number) {
  return requested > 0 ? Math.max(0, Math.min(100, completed * 100 / requested)) : 0;
}
