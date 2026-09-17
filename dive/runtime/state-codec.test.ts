import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeState, encodeState } from './state-codec.ts';

const roundtrip = (bag: Record<string, unknown>) => decodeState('#' + encodeState(bag));

test('identifiers with percent signs survive a roundtrip', () => {
  const bag = { wh: ['WH%PROD', 'ETL%2F', '100%'], db: 'DB%20X' };
  assert.deepEqual(roundtrip(bag), bag);
});

test('plus signs, spaces, quotes and unicode survive', () => {
  const bag = { wh: ['A+B', 'has space', 'q"uote', 'Café → ≥'], range: { start: '2026-01-01', end: '2026-02-01' }, sys: true };
  assert.deepEqual(roundtrip(bag), bag);
});

test('empty bag encodes to an empty hash and decodes back', () => {
  assert.equal(encodeState({}), '');
  assert.deepEqual(decodeState(''), {});
  assert.deepEqual(decodeState('#'), {});
});

test('malformed or foreign hashes decode to an empty bag instead of throwing', () => {
  assert.deepEqual(decodeState('#s=%E0%A4%A'), {});
  assert.deepEqual(decodeState('#s=not-json'), {});
  assert.deepEqual(decodeState('#s=%5B1%2C2%5D'), {}); // an array is not a state bag
  assert.deepEqual(decodeState('#other=1'), {});
});
