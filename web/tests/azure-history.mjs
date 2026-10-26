// Real isolated Azure only. No API data replacement; fault cases abort network calls.
import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const manifest = JSON.parse(await readFile(path.join(root, '.secrets/history-test/deployment.json'), 'utf8'));
assert.equal(manifest.site, 'resource-simulator-history-test');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, hasTouch: true });
const errors = [], records = [], timing = [], pending = new Set();
page.on('pageerror', e => errors.push(e.message));
page.on('response', response => {
  if (!response.url().includes('/api/')) return;
  assert.ok(!/[?&](sig|sv|se|sp)=/.test(response.url()));
  const promise = (async () => {
    const body = await response.body().catch(() => Buffer.alloc(0));
    const t = response.request().timing();
    records.push({ at: Date.now(), path: new URL(response.url()).pathname,
      manifest: new URL(response.url()).searchParams.get('mode') === 'manifest', status: response.status(),
      bytes: body.length, elapsed: t.responseEnd });
  })();
  pending.add(promise); promise.finally(() => pending.delete(promise));
});
const output = path.join(root, '.cache/history-azure');
await mkdir(output, { recursive: true });
try {
  const began = Date.now();
  await page.goto(manifest.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const cards = [page.getByTestId('card-resource'), page.getByTestId('card-beta')];
  for (const card of cards) await card.locator('.candle').first().waitFor({ timeout: 180000 });
  const waitReady = async () => {
    for (const card of cards) {
      await expect(card.locator('.history-status')).toContainText('1分足', { timeout: 180000 });
      await card.locator('.candle').first().waitFor({ timeout: 180000 });
    }
    await Promise.all([...pending]);
  };
  await waitReady();
  timing.push({ period: '当日', render_ms: Date.now() - began, history_requests: records.filter(r => r.path.endsWith('/ohlc')).length,
    bytes: records.filter(r => r.path.endsWith('/ohlc')).reduce((n, r) => n + r.bytes, 0) });
  console.log(JSON.stringify(timing.at(-1)));
  for (const label of ['1週間', '1か月', '3か月']) {
    const before = records.length, at = Date.now();
    for (const card of cards) await card.getByRole('button', { name: label, exact: true }).click();
    // Requests start immediately; wait for selected period's refresh rather than prior text.
    await expect.poll(() => records.length, { timeout: 180000 }).toBeGreaterThan(before);
    await waitReady();
    const set = records.slice(before).filter(r => r.path.endsWith('/ohlc'));
    timing.push({ period: label, render_ms: Date.now() - at, history_requests: set.length,
      bytes: set.reduce((n, r) => n + r.bytes, 0), http_failures: set.filter(r => r.status !== 200).length });
    await writeFile(path.join(output, 'browser-progress.json'), JSON.stringify(timing, null, 2));
    console.log(JSON.stringify(timing.at(-1)));
  }
  const steps = [];
  const beforeRefresh = records.length;
  for (let i = 0; i < 65; i++) {
    steps.push({ at: Date.now(), value: await page.getByTestId('step').textContent() });
    await page.waitForTimeout(1000);
  }
  await waitReady();
  const refresh = records.slice(beforeRefresh).filter(r => r.path.endsWith('/ohlc'));
  timing.push({ period: '3か月60秒後', history_requests: refresh.length,
    raw_day_requests: refresh.filter(r => !r.manifest).length,
    bytes: refresh.reduce((n, r) => n + r.bytes, 0) });
  assert.ok(refresh.filter(r => !r.manifest).length <= 6, 'unchanged quarter must not reload every minute');
  assert.ok(new Set(steps.map(s => s.value)).size >= 40, 'latest should keep progressing during history load');
  for (const width of [1280, 320, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const card of cards) await card.getByRole('button', { name: '当日', exact: true }).click();
    await waitReady();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const a = await cards[0].boundingBox(), b = await cards[1].boundingBox();
    if (width >= 768) assert.ok(Math.abs(a.y - b.y) < 1 && Math.abs(a.width - b.width) < 1);
    else assert.ok(b.y >= a.y + a.height);
    await cards[0].locator('.candlestick').tap({ position: { x: 130, y: 100 } });
    await expect(cards[0].locator('.candle-detail')).toContainText('サンプル数');
    await page.screenshot({ path: path.join(output, `azure-history-${width}.png`), fullPage: true });
  }
  // Abort only history: latest keeps increasing, cached graph must be explicitly stale.
  await page.route('**/api/ohlc?*', route => route.abort('failed'));
  await page.evaluate(() => dispatchEvent(new Event('online')));
  await expect(cards[0].locator('.history-status')).toContainText('古いグラフ');
  const kept = await page.getByTestId('step').textContent();
  await page.waitForTimeout(4000);
  assert.notEqual(await page.getByTestId('step').textContent(), kept);
  await page.unroute('**/api/ohlc?*');
  await page.evaluate(() => dispatchEvent(new Event('online')));
  await waitReady();
  await page.route('**/api/current', route => route.abort('failed'));
  await expect(page.getByRole('status')).toContainText('取得失敗', { timeout: 10000 });
  assert.ok(await cards[0].locator('.candle').count() > 0);
  await page.unroute('**/api/current');
  await page.evaluate(() => dispatchEvent(new Event('online')));
  await expect(page.getByRole('status')).toContainText('公開状態を受信中', { timeout: 15000 });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Page.setWebLifecycleState', { state: 'frozen' });
  await new Promise(r => setTimeout(r, 2000));
  await cdp.send('Page.setWebLifecycleState', { state: 'active' });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await waitReady();
  assert.deepEqual(errors, []);
  const result = { url: manifest.url, timing, steps, errors, records, status: 'passed',
    checks: ['actual Azure data', 'independent current/history recovery', 'touch detail', 'PC/320/390', 'tab resume', 'quarter refresh bounded'] };
  await writeFile(path.join(output, 'azure-browser.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ timing, checks: result.checks }));
} finally { await browser.close(); }
