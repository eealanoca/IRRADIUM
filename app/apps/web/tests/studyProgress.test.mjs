import assert from 'node:assert/strict';
import test from 'node:test';
import { preferredActiveJobIndex, requestedPositionCount, studyPhase } from '../lib/studyProgress.ts';

test('the ribbon follows transport before a newer queued calibration', () => {
  assert.equal(preferredActiveJobIndex(['queued', 'completed', 'running']), 2);
  assert.equal(preferredActiveJobIndex(['queued', 'completed']), 0);
  assert.equal(preferredActiveJobIndex(['completed', 'failed']), -1);
});

test('queued sparse selection counts selected stops instead of the bounding interval', () => {
  assert.equal(requestedPositionCount({ start: 6, end: 28, position_names: ['I7', 'I8', 'S1'] }), 3);
  assert.equal(requestedPositionCount({ start: 0, end: 55 }), 56);
  assert.equal(requestedPositionCount(), undefined);
});

test('56 published positions do not mean convergence when uncertainty exceeds the goal', () => {
  const result = studyPhase({ completed: 56, total: 56, target: .005, maximum: .034 });
  assert.equal(result.phase, 'refining');
  assert.equal(result.coverage, 100);
  assert.equal(result.indeterminate, true);
});

test('partial coverage reports the generation milestone without inventing convergence', () => {
  const result = studyPhase({ completed: 28, total: 56, target: .005 });
  assert.equal(result.phase, 'positions');
  assert.equal(result.coverage, 50);
  assert.equal(result.indeterminate, false);
});

test('reaching the uncertainty goal enters saving, not another refinement', () => {
  assert.equal(studyPhase({ completed: 56, total: 56, target: .005, maximum: .0049 }).phase, 'saving');
});

test('an explicit finalization stage takes precedence over an intermediate snapshot', () => {
  assert.equal(studyPhase({ completed: 55, total: 56, target: .005, stage: 'finalizing-study' }).phase, 'saving');
});

test('unknown coverage remains indeterminate instead of presenting a completion percentage', () => {
  assert.equal(studyPhase({ completed: 0, total: 0 }).indeterminate, true);
});
