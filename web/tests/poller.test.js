import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPoller, newer } from '../src/poller.js';

const state = (step, generation = 1) => ({ step, run_generation: generation, run_id: `run-${generation}` });
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
function fixture(request) {
  let visible = true, failures = 0, id = 0;
  const rendered = [], timers = new Map();
  const poller = createPoller({ request, render: x => rendered.push(x), failed: () => failures++,
    visible: () => visible, schedule: (fn, delay) => { timers.set(++id, { fn, delay }); return id; },
    cancel: key => timers.delete(key) });
  return { poller, rendered, timers, get failures() { return failures; },
    hide() { visible = false; poller.pause(); }, show() { visible = true; poller.resume(); },
    tick(delay) { const found = [...timers].find(([, x]) => x.delay === delay); assert.ok(found); timers.delete(found[0]); found[1].fn(); } };
}
test('first access is immediate; subsequent polling is automatic', async () => {
  let step = 0; const f = fixture(async () => state(++step));
  f.poller.resume(); await flush(); assert.equal(f.rendered[0].step, 1);
  f.tick(1000); await flush(); assert.equal(f.rendered[1].step, 2); f.poller.pause();
});
test('a slow request never overlaps the next scheduled poll', async () => {
  let complete, calls = 0; const f = fixture(() => { calls++; return new Promise(resolve => { complete = resolve; }); });
  f.poller.resume(); assert.equal(calls, 1);
  assert.equal([...f.timers.values()].some(x => x.delay === 1000), false);
  complete(state(1)); await flush(); f.tick(1000); assert.equal(calls, 2); f.poller.pause();
});
test('tab return fetches immediately and rejects old responses', async () => {
  const pending = []; const f = fixture(() => new Promise(resolve => pending.push(resolve)));
  f.poller.resume(); f.hide(); f.show();
  assert.equal(pending.length, 2); pending[1](state(0, 2)); await flush();
  pending[0](state(99)); await flush(); assert.deepEqual(f.rendered, [state(0, 2)]); f.poller.pause();
});
test('API failure retries automatically and recovers', async () => {
  let calls = 0; const f = fixture(async () => { if (++calls === 1) throw Error('503'); return state(2); });
  f.poller.resume(); await flush(); assert.equal(f.failures, 1);
  f.tick(1000); await flush(); assert.equal(f.rendered[0].step, 2); f.poller.pause();
});
test('step, generation and UUID guard against rollbacks', () => {
  assert.equal(newer(state(1), state(2)), false);
  assert.equal(newer(state(100), state(0, 2)), false);
  assert.equal(newer(state(0, 2), state(100)), true);
  assert.equal(newer({ ...state(50), run_id: 'another' }, state(1)), false);
});
