import assert from 'node:assert/strict';
import test from 'node:test';
import { doseFieldStatistics } from '../components/results/doseStatistics.ts';

test('isodose references use scored cells, including scored zeroes, and omit exterior cells', () => {
  const statistics = doseFieldStatistics({ values: [100000, 2, 4, 0, NaN], ghosts: [32, 0, 0, 0, 0] });
  assert.equal(statistics.meanDose, 2);
  assert.equal(statistics.minimumDose, 0);
  assert.equal(statistics.maximumDose, 4);
  assert.equal(statistics.referenceDose, 4);
});

test('adding exterior padding cannot change the dose reference', () => {
  const source = doseFieldStatistics({ values: [2, 4], ghosts: [0, 0] });
  const padded = doseFieldStatistics({ values: [0, 0, 2, 4, 0], ghosts: [32, 32, 0, 0, 32] });
  assert.deepEqual(padded, source);
});

test('an empty or entirely unscored field has no positive reference', () => {
  assert.throws(() => doseFieldStatistics({ values: [0, NaN], ghosts: [32, 0] }), /no contiene valores positivos/);
});
