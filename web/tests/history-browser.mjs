// Isolated real SQLite history, cloud Table double, real read service, built React.
import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const fixture = JSON.parse(execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-m', 'tests.history_fixture'], { cwd: root, encoding: 'utf8' }));
const server = createServer(async (req, res) => {
  try {
    const file = req.url === '/' ? 'index.html' : req.url.slice(1);
    if (!/^(index\.html|assets\/[\w.-]+)$/.test(file)) return res.writeHead(404).end();
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(await readFile(path.join(root, 'web/dist', file)));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, hasTouch: true });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let payload = structuredClone(fixture.current), failed = false, empty = false, outside = false, slow = false;
let historyCalls = 0, pending = [];
const checks = [];
try {
  await page.clock.install();
  await page.route('**/api/current', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) }));
  await page.route('**/api/ohlc?*', async route => {
    historyCalls++;
    const params = new URL(route.request().url()).searchParams;
    if (params.get('mode') === 'manifest') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({run_id: params.get('run_id'), resource_id: params.get('resource_id')}) });
      return;
    }
    const from = Date.parse(params.get('from')), to = Date.parse(params.get('to'));
    assert.ok(to - from <= 7 * 86400000);
    const resource = params.get('resource_id');
    const history = structuredClone(fixture.history[resource]);
    history.run_id = params.get('run_id');
    history.candles = empty ? [] : history.candles.filter(r => Date.parse(r.minute_start) >= from && Date.parse(r.minute_start) < to);
    const answer = () => route.fulfill({ status: outside ? 416 : failed ? 503 : 200, contentType: 'application/json', body: JSON.stringify(failed || outside ? { error: 'test failure' } : history) }).catch(() => {});
    if (slow) pending.push(answer); else await answer();
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const alpha = page.getByTestId('card-resource'), beta = page.getByTestId('card-beta');
  await alpha.locator('.candle').first().waitFor();
  await beta.locator('.candle').first().waitFor();
  assert.match(await alpha.textContent(), /欠測区間あり/);
  assert.equal(await alpha.getByRole('button', { name: '当日', exact: true }).getAttribute('aria-pressed'), 'true');
  for (const width of [1280, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(100);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `overflow ${width}`);
    const a = await alpha.boundingBox(), b = await beta.boundingBox();
    if (width >= 768) { assert.ok(Math.abs(a.y - b.y) < 1); assert.ok(Math.abs(a.width - b.width) < 1); }
    else assert.ok(b.y >= a.y + a.height);
    for (const card of [alpha, beta]) {
      const top = await card.locator('.card-top').boundingBox(), chart = await card.locator('.candlestick').boundingBox();
      assert.ok(chart.height > top.height, `graph dominates ${width}`);
      assert.ok(await card.evaluate(e => e.scrollWidth <= e.clientWidth));
    }
    if (width !== 768) {
      await mkdir(path.join(root, '.cache/validation'), { recursive: true });
      await page.screenshot({ path: path.join(root, `.cache/validation/history-${width}.png`), fullPage: true });
    }
  }
  checks.push('PC 1280/768 equal two columns; 390/320 one column, no overflow, chart taller than header; screenshots');
  await alpha.locator('.candlestick').tap({ position: { x: 130, y: 100 } });
  assert.match(await alpha.locator('.candle-detail').textContent(), /JST.*始.*高.*安.*終.*サンプル数/s);
  await alpha.locator('.candlestick').focus(); await page.keyboard.press('End');
  assert.match(await alpha.locator('.candle-detail').textContent(), /JST/);
  checks.push('tap and keyboard selection reveal date/OHLC/sample count');
  await alpha.getByRole('button', { name: '3か月', exact: true }).click();
  await alpha.locator('.history-status').filter({ hasText: '集約' }).waitFor();
  await expect.poll(() => historyCalls).toBeGreaterThanOrEqual(16);
  await expect(alpha.locator('.history-status')).toContainText('1分足');
  checks.push('calendar quarter split into <=7-day requests and aggregated');
  // History refresh and failure independent of current polling, using browser clock.
  failed = true;
  const count = await alpha.locator('.candle').count();
  payload.step += 1;
  await page.clock.fastForward(60001);
  await alpha.locator('.history-status').filter({ hasText: '古いグラフ' }).waitFor();
  assert.equal(await alpha.locator('.candle').count(), count);
  await page.waitForFunction(step => document.querySelector('[data-testid=step]').textContent.replace(/\D/g, '') === String(step), payload.step);
  failed = false;
  await page.clock.fastForward(60001);
  await alpha.locator('.history-status').filter({ hasText: '集約' }).waitFor();
  checks.push('60-second history failure retains stale graph; latest step progresses; recovery refreshes automatically');
  empty = true;
  await beta.getByRole('button', { name: '1週間', exact: true }).click();
  await beta.locator('.history-status').filter({ hasText: '未作成' }).waitFor();
  outside = true;
  await beta.getByRole('button', { name: '1か月', exact: true }).click();
  await beta.locator('.history-status').filter({ hasText: '対象期間外' }).waitFor();
  outside = false; empty = false;
  slow = true;
  await alpha.getByRole('button', { name: '1週間', exact: true }).click();
  payload = { ...payload, run_generation: payload.run_generation + 1, run_id: '00000000-0000-0000-0000-000000000002', step: 1 };
  await page.clock.fastForward(1001);
  await page.waitForFunction(() => document.querySelector('[data-testid=step]').textContent.replace(/\D/g, '') === '1');
  assert.equal(await page.locator('.candle').count(), 0);
  slow = false;
  for (const answer of pending) await answer();
  await page.clock.fastForward(60001);
  await alpha.locator('.candle').first().waitFor();
  checks.push('empty, outside-window distinguished; run change clears old graph and ignores delayed response');
  assert.deepEqual(errors, []);
  await writeFile(path.join(root, '.cache/validation/history-browser.json'), JSON.stringify(checks, null, 2));
  console.log(JSON.stringify(checks, null, 2));
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
