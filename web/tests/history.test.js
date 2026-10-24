import { test } from 'node:test';
import assert from 'node:assert/strict';
import { aggregate, bounds, loadHistory, loadCachedHistory, monthsBefore } from '../src/history.js';

const row = (at, values = {}) => ({ minute_start: new Date(at).toISOString(), open: 5, high: 9, low: 2, close: 6, sample_count: 60, ...values });
test('JST day, rolling week, clamped calendar months and leap day', () => {
  const now = Date.parse('2026-05-31T15:30:42Z');
  assert.equal(new Date(bounds('today', now)[0]).toISOString(), '2026-05-31T15:00:00.000Z');
  assert.equal(new Date(bounds('week', now)[0]).toISOString(), '2026-05-24T15:30:00.000Z');
  assert.equal(new Date(bounds('quarter', now)[0]).toISOString(), '2026-02-28T15:30:00.000Z');
  assert.equal(new Date(monthsBefore(Date.parse('2024-05-31T00:00:00Z'), 3)).toISOString(), '2024-02-29T00:00:00.000Z');
});
test('aggregate OHLC/count in order, split every missing minute, preserve originals', () => {
  const rows = [row(0), row(60000, { high: 15, low: 1, close: 10, sample_count: 17 }), row(180000)];
  const before = structuredClone(rows);
  const result = aggregate(rows, 0, 3600000, 1);
  assert.equal(result.length, 2);
  assert.deepEqual([result[0].open, result[0].high, result[0].low, result[0].close, result[0].sample_count], [5, 15, 1, 10, 77]);
  assert.equal(result[0].partial, true);
  assert.deepEqual(rows, before);
});
test('calendar quarter is split into <=7-day queries, pages scoped and deduplicated', async () => {
  const now = Date.parse('2026-10-23T03:00:00Z'), [start, end] = bounds('quarter', now);
  const calls = [];
  const rows = await loadHistory({ run: 'run', resource: 'beta', start, end, request: async url => {
    const p = new URL(url, 'https://unit').searchParams;
    calls.push(p);
    const from = Date.parse(p.get('from')), to = Date.parse(p.get('to'));
    assert.ok(to - from <= 7 * 86400000);
    assert.equal(p.get('run_id'), 'run'); assert.equal(p.get('resource_id'), 'beta');
    return { ok: true, json: async () => ({ run_id: 'run', resource_id: 'beta', candles: [row(from)], cursor: p.has('cursor') ? null : 'next' }) };
  }});
  assert.equal(calls.length, Math.ceil((end - start) / (7 * 86400000)) * 2);
  assert.equal(rows.length, calls.length / 2);
  assert.ok(Number(calls[0].get('resolution_minutes')) > 1);
});
test('failure, outside window, cursor loops and mixed runs are rejected', async () => {
  const base = { run: 'run', resource: 'beta', start: 0, end: 60000 };
  for (const response of [
    { ok: false, status: 503 }, { ok: false, status: 416 },
    { ok: true, json: async () => ({ run_id: 'other', resource_id: 'beta', candles: [] }) },
    { ok: true, json: async () => ({ run_id: 'run', resource_id: 'beta', candles: [], cursor: 'loop' }) },
  ]) await assert.rejects(loadHistory({ ...base, request: async () => response }));
});
test('server projection pages join without losing extrema or sample counts', () => {
  const rows = [row(0, { minutes: 3, end: 180000, sample_count: 180, partial: false }), row(180000, { minutes: 2, end: 300000, sample_count: 120, high: 20, close: 8, partial: false })];
  const [combined] = aggregate(rows, 0, 3600000, 1);
  assert.equal(combined.minutes, 5); assert.equal(combined.sample_count, 300);
  assert.equal(combined.high, 20); assert.equal(combined.close, 8); assert.equal(combined.partial, false);
});

test('day revision refresh avoids quarter reload and catches late historical upload', async () => {
  const end = Date.parse('2026-10-24T12:00:00Z'), start = bounds('quarter', end)[0];
  const cache = new Map(), revisions = {};
  for (let at = Math.floor(start / 86400000) * 86400000; at < end; at += 86400000) revisions[new Date(at).toISOString().slice(0, 10)] = 'v1';
  let reads = 0;
  const request = async url => {
    const p = new URL(url, 'https://unit').searchParams;
    const common = { run_id: 'run', resource_id: 'beta' };
    if (p.get('mode') === 'manifest') return { ok: true, json: async () => ({ ...common, revisions }) };
    reads++;
    return { ok: true, json: async () => ({ ...common, candles: [row(Date.parse(p.get('from')))], cursor: null }) };
  };
  await loadCachedHistory({ run: 'run', resource: 'beta', start, end, request }, cache);
  assert.ok(reads > 90);
  reads = 0;
  await loadCachedHistory({ run: 'run', resource: 'beta', start: start + 60000, end: end + 60000, request }, cache);
  assert.equal(reads, 0, 'unchanged days reused as range advances');
  revisions['2026-08-20'] = 'late-backfill'; revisions['2026-10-24'] = 'new-minute';
  await loadCachedHistory({ run: 'run', resource: 'beta', start, end, request }, cache);
  assert.equal(reads, 2, 'only late changed day and live day fetched');
});

test('failed refresh does not commit partial day revisions into cache', async () => {
  const start = Date.parse('2026-10-24T00:00:00Z'), end = start + 60000, cache = new Map();
  const request = async url => {
    const p = new URL(url, 'https://unit').searchParams;
    return p.get('mode') === 'manifest' ? { ok: true, json: async () => ({ run_id: 'run', resource_id: 'beta', revisions: { '2026-10-24': 'v2' } }) } : { ok: false, status: 503 };
  };
  await assert.rejects(loadCachedHistory({ run: 'run', resource: 'beta', start, end, request }, cache));
  assert.equal(cache.size, 0);
});
