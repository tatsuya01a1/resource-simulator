// Local build only: never starts/stops a real Worker or connects to Azure.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const python = process.env.TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const fixture = JSON.parse(execFileSync(python, ['-m', 'tests.public_fixture'], { cwd: root, encoding: 'utf8' }));
const server = createServer(async (request, response) => {
  try {
    const name = request.url === '/' ? '/index.html' : request.url;
    if (!/^\/(?:index\.html|assets\/[\w.-]+)$/.test(name)) { response.writeHead(404).end(); return; }
    const body = await readFile(path.join(root, 'web/dist', name));
    response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
    response.end(body);
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let failure = false;
let payload = { ...fixture, schema_version: 1, step: 1 };
delete payload.resources;
const checks = [];
try {
  await page.route('**/api/current', route => route.fulfill({
    status: failure ? 503 : 200, contentType: 'application/json',
    body: JSON.stringify(failure ? { error: 'temporarily_unavailable' } : payload),
  }));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByTestId('resource').waitFor();
  const alpha = page.getByTestId('card-resource'), beta = page.getByTestId('card-beta');
  assert.match(await alpha.textContent(), /Alpha/);
  assert.match(await beta.textContent(), /Beta.*未提供/);
  assert.equal(await page.getByTestId('beta-resource').count(), 0);
  checks.push('v1 keeps Alpha and displays unavailable Beta without 0');
  payload = structuredClone(fixture);
  await page.getByTestId('beta-resource').waitFor();
  assert.equal((await page.getByTestId('step').textContent()).replace(/\D/g, ''), String(fixture.step));
  assert.match(await alpha.textContent(), /実増減量 \+/);
  assert.match(await beta.textContent(), /適用倍率/);
  checks.push('SQLite -> publisher -> Table double -> read service -> two React cards');
  payload = structuredClone(fixture);
  payload.step++;
  payload.resources.forEach(resource => {
    resource.resource = 1.7976931348623157e308;
    resource.requested_delta = -1.2345678901234568e200;
    resource.actual_delta = -1.2345678901234568e200;
  });
  await page.waitForFunction(() => document.querySelector('[data-testid="resource"]').textContent.length > 100);
  for (const width of [320, 390, 767, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    const a = await alpha.boundingBox(), b = await beta.boundingBox();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `horizontal overflow at ${width}`);
    for (const card of [alpha, beta]) {
      assert.ok(await card.evaluate(element => element.scrollWidth <= element.clientWidth), `card overflow at ${width}`);
    }
    if (width >= 768) {
      assert.ok(Math.abs(a.y - b.y) < 1 && b.x > a.x, `two columns at ${width}`);
      assert.ok(Math.abs(a.width - b.width) < 1, `equal widths at ${width}`);
    } else assert.ok(b.y >= a.y + a.height && Math.abs(a.x - b.x) < 1, `one column at ${width}`);
  }
  checks.push('320/390/767: one column, 768/1280: equal two columns, maximum finite values without overflow');
  payload = structuredClone(fixture);
  payload.step += 2;
  payload.stale = true;
  payload.resources[1].schedule.acquisition = 'missing';
  payload.resources[1].schedule.mode = 'night';
  payload.resources[1].schedule.multiplier = 5;
  await page.getByRole('status').filter({ hasText: '更新停滞' }).waitFor();
  await page.getByText('基準値未確定（代替倍率5）').waitFor();
  const retained = await page.getByTestId('beta-resource').textContent();
  failure = true;
  await page.getByRole('status').filter({ hasText: '取得失敗' }).waitFor();
  assert.equal(await page.getByTestId('beta-resource').textContent(), retained);
  failure = false;
  payload = { ...structuredClone(fixture), step: fixture.step + 3,
    calculated_at: new Date().toISOString(), published_at: new Date().toISOString(), api_time: new Date().toISOString() };
  await page.getByRole('status').filter({ hasText: '公開状態を受信中' }).waitFor();
  checks.push('stall, pending baseline, failed fetch retains both values, automatic recovery');
  assert.equal(await page.getByRole('button').count(), 0);
  await mkdir(path.join(root, '.cache/validation'), { recursive: true });
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: path.join(root, `.cache/validation/alpha-beta-${width}.png`), fullPage: true });
  }
  assert.deepEqual(errors, []);
  await writeFile(path.join(root, '.cache/validation/alpha-beta-browser.json'), JSON.stringify(checks, null, 2));
  console.log(JSON.stringify(checks, null, 2));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
