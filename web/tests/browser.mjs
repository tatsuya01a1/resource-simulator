import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const url = process.argv[2];
if (!url?.startsWith('https://')) throw Error('Supply the deployed HTTPS URL');
const root = path.resolve(import.meta.dirname, '../..');
const python = path.join(root, '.venv/Scripts/python.exe');
const control = command => execFileSync(python, ['scripts/worker_control.py', command], { cwd: root, windowsHide: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const checks = {};
try {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('resource').waitFor();
  checks.first_access = true;
  const first = Number((await page.getByTestId('step').textContent()).replace(/[^0-9]/g, ''));
  await page.waitForFunction(previous => Number(document.querySelector('[data-testid="step"]').textContent.replace(/[^0-9]/g, '')) > previous, first, { timeout: 15000 });
  checks.auto_update = true;
  checks.no_horizontal_scroll = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  assert.ok(checks.no_horizontal_scroll);
  assert.equal(await page.getByRole('button').count(), 0);
  assert.ok(await page.getByText('履歴グラフは未実装です').isVisible());
  await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForTimeout(1500);
  const resumed = page.waitForRequest('**/api/current', { timeout: 1500 });
  await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
  await resumed;
  checks.visibility_event_recovery = true;
  await page.route('**/api/current', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"temporarily_unavailable"}' }));
  await page.getByRole('status').filter({ hasText: '取得失敗' }).waitFor({ timeout: 10000 });
  assert.ok(await page.getByTestId('resource').isVisible());
  await page.unroute('**/api/current');
  await page.getByRole('status').filter({ hasText: '公開状態を受信中' }).waitFor({ timeout: 15000 });
  checks.api_failure_recovery = true;
  control('stop');
  await page.waitForTimeout(2500);
  const response = await page.request.get(url + '/api/current');
  assert.equal(response.status(), 200);
  assert.match(response.headers()['cache-control'], /no-store/);
  const snapshot = await response.json();
  const numeric = value => Number(value.replace(/,/g, ''));
  assert.equal(numeric(await page.getByTestId('step').textContent().then(x => x.replace('秒', ''))), snapshot.step);
  assert.equal(numeric(await page.getByTestId('resource').textContent()), Number(snapshot.resource.toFixed(2)));
  const repeated = await (await page.request.get(url + '/api/current')).json();
  assert.equal(repeated.step, snapshot.step);
  assert.equal(repeated.published_at, snapshot.published_at);
  checks.stop_retains_final_value = true;
  checks.pc_storage_api = JSON.parse(execFileSync(python, ['scripts/verify_azure.py', '--compare-only'],
    { cwd: root, windowsHide: true, encoding: 'utf8' }));
  await page.getByRole('status').filter({ hasText: '更新停滞' }).waitFor({ timeout: 18000 });
  checks.stale_display = true;
  await mkdir(path.join(root, '.cache/validation'), { recursive: true });
  await page.screenshot({ path: path.join(root, '.cache/validation/mobile-stopped.png'), fullPage: true });
  checks.stopped_snapshot = { step: snapshot.step, run_id: snapshot.run_id, resource: snapshot.resource,
    parameter: snapshot.parameter, actual_delta: snapshot.actual_delta,
    calculated_at: snapshot.calculated_at, published_at: snapshot.published_at,
    build_commit: snapshot.build_commit };
  assert.deepEqual(errors, []);
} finally {
  control('start');
  await browser.close();
}
console.log(JSON.stringify(checks, null, 2));
