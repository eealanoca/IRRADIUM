import type { DoseImage } from './doseViewFrame';

/** Statistical references must come from scored cells, before display smoothing. */
export function doseFieldStatistics(dataset: DoseImage) {
  const active: number[] = [];
  let sum = 0;
  for (let index = 0; index < dataset.values.length; index++) {
    if (dataset.ghosts && (dataset.ghosts[index] & 32)) continue;
    const value = dataset.values[index];
    if (!Number.isFinite(value) || value < 0) continue;
    active.push(value);
    sum += value;
  }
  active.sort((left, right) => left - right);
  const percentile = (probability: number) => active[Math.round((active.length - 1) * probability)] ?? 0;
  const referenceDose = percentile(.99);
  if (!(referenceDose > 0)) throw new Error('La malla no contiene valores positivos de dosis para construir isodosis.');
  return {
    referenceDose, meanDose: sum / active.length,
    minimumDose: active[0], maximumDose: active.at(-1)!,
    quantiles: { p05: percentile(.05), p10: percentile(.10), p25: percentile(.25),
      p50: percentile(.50), p75: percentile(.75), p90: percentile(.90), p95: percentile(.95) },
  };
}
