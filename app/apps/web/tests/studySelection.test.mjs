import assert from 'node:assert/strict';
import test from 'node:test';
import { availableStudyInterval, selectStudyCase, selectStudyRun, studyCoverage } from '../lib/studySelection.ts';

const studies = [{ run_id: 'new-run' }, { run_id: 'old-run' }];

test('follows the current execution instead of retaining the previous run', () => {
  assert.equal(selectStudyRun(studies, undefined, undefined, 'new-run'), 'new-run');
});

test('keeps a historical execution only when explicitly selected', () => {
  assert.equal(selectStudyRun(studies, 'old-run', undefined, 'new-run'), 'old-run');
});

test('does not show the old study while a submitted execution has no results', () => {
  assert.equal(selectStudyRun(studies, 'old-run', 'pending-run', 'old-run'), '');
});

test('switches to the submitted execution as soon as it publishes results', () => {
  assert.equal(selectStudyRun(studies, 'old-run', 'new-run', 'new-run'), 'new-run');
});

test('an externally started execution also waits for its own first result', () => {
  assert.equal(selectStudyRun(studies, undefined, undefined, 'pending-run'), '');
});

test('falls back to the newest saved study when no execution is identified', () => {
  assert.equal(selectStudyRun(studies), 'new-run');
  assert.equal(selectStudyRun([]), '');
});

test('a missing historical selection does not leak results from another case', () => {
  assert.equal(selectStudyRun(studies, 'other-case-run', undefined, 'new-run'), 'new-run');
});

const cases = [{ case_id: 'live-case' }, { case_id: 'historical-case' }];

test('reloading reconnects to the active case despite a previous CAD selection', () => {
  assert.equal(selectStudyCase(cases, ['live-case'], true, 'historical-case'), 'live-case');
});

test('an explicitly chosen historical case remains available during a calculation', () => {
  assert.equal(selectStudyCase(cases, ['live-case'], false, 'historical-case', 'historical-case'), 'historical-case');
});

test('finishing a live study keeps its results selected', () => {
  assert.equal(selectStudyCase(cases, [], true, 'historical-case', 'live-case'), 'live-case');
});

test('automatic integration bounds grow with newly published positions', () => {
  assert.deepEqual(availableStudyInterval(1), { start: 0, end: 0 });
  assert.deepEqual(availableStudyInterval(9), { start: 0, end: 8 });
  assert.deepEqual(availableStudyInterval(56), { start: 0, end: 55 });
});

test('manual integration bounds are preserved and stay within available results', () => {
  assert.deepEqual(availableStudyInterval(20, 3, 8), { start: 3, end: 8 });
  assert.deepEqual(availableStudyInterval(5, 3, 8), { start: 3, end: 4 });
  assert.deepEqual(availableStudyInterval(5, 18, 19), { start: 4, end: 4 });
});

test('published coverage stays complete when a refinement returns to position one', () => {
  assert.equal(studyCoverage(5, 56), 100 * 5 / 56);
  assert.equal(studyCoverage(56, 56), 100);
  assert.equal(studyCoverage(0, 0), 0);
});
