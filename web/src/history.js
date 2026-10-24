const MINUTE = 60000;
export const periodLabels = { today: '当日', week: '1週間', month: '1か月', quarter: '3か月' };
export function monthsBefore(time, count) {
  const local = new Date(time + 9 * 3600000);
  const day = local.getUTCDate();
  local.setUTCDate(1);
  local.setUTCMonth(local.getUTCMonth() - count);
  const last = new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth() + 1, 0)).getUTCDate();
  local.setUTCDate(Math.min(day, last));
  return local.getTime() - 9 * 3600000;
}
export function bounds(period, now) {
  const local = new Date(now + 9 * 3600000);
  const start = period === 'today' ? Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate()) - 9 * 3600000
    : period === 'week' ? now - 7 * 86400000 : monthsBefore(now, period === 'month' ? 1 : 3);
  return [Math.floor(start / MINUTE) * MINUTE, now];
}

// Day revisions invalidate only affected days, including delayed historical uploads.
// Cache is scoped by run/resource/resolution and committed only after a complete fetch.
export async function loadCachedHistory(options, cache) {
  const { run, resource, start, end, signal, request = fetch } = options;
  const params = new URLSearchParams({ mode: 'manifest', run_id: run, resource_id: resource,
    from: new Date(start).toISOString(), to: new Date(end).toISOString(), as_of: new Date(end).toISOString() });
  const response = await request(`/api/ohlc?${params}`, { signal, cache: 'no-store' });
  if (!response.ok) throw new Error(response.status === 416 ? 'outside' : 'unavailable');
  const manifest = await response.json();
  if (manifest.run_id !== run || manifest.resource_id !== resource) throw new Error('invalid manifest');
  // Compatible with an API deployed before revision metadata support.
  if (!manifest.revisions) return loadHistory(options);
  const resolution = Math.max(1, Math.min(1440, Math.ceil((end - start) / MINUTE / 160)));
  const day = 86400000, first = Math.floor(start / day) * day;
  const slices = [];
  for (let at = first; at < end; at += day) {
    const date = new Date(at).toISOString().slice(0, 10), revision = manifest.revisions[date] || 'empty';
    const from = Math.max(start, at), to = Math.min(end, at + day);
    // Keep the partial first day as original minutes so a moving lower edge can be trimmed.
    const size = at === first ? 1 : resolution;
    const key = `${run}|${resource}|${date}|${size}`;
    slices.push({ key, revision, from, to, size });
  }
  const staged = new Map();
  let next = 0;
  const worker = async () => { while (next < slices.length) {
    const slice = slices[next++], existing = cache.get(slice.key);
    if (existing && existing.revision === slice.revision && existing.from <= slice.from) {
      staged.set(slice.key, existing); continue;
    }
    const rows = slice.revision === 'empty' ? [] : await loadHistory({ ...options,
      start: slice.from, end: slice.to, asOf: end,
      resolutionMinutes: slice.size });
    staged.set(slice.key, { revision: slice.revision, from: slice.from, rows });
  }};
  await Promise.all([worker(), worker()]);
  if (signal?.aborted) throw new Error('aborted');
  cache.clear();
  for (const [key, value] of staged) cache.set(key, value);
  return [...staged.values()].flatMap(value => value.rows)
    .filter(row => Date.parse(row.minute_start) >= start && Date.parse(row.minute_start) < end)
    .sort((a, b) => Date.parse(a.minute_start) - Date.parse(b.minute_start));
}

export async function loadHistory({ run, resource, start, end, signal, request = fetch, maxBars = 160, asOf = end, resolutionMinutes }) {
  const rows = new Map();
  const resolution = resolutionMinutes ?? Math.max(1, Math.min(1440, Math.ceil((end - start) / MINUTE / maxBars)));
  const slices = [];
  for (let from = start; from < end; from += 7 * 86400000) {
    slices.push([from, Math.min(end, from + 7 * 86400000)]);
  }
  let next = 0, failed = false;
  const worker = async () => { while (next < slices.length && !failed) {
    const [from, to] = slices[next++];
    let cursor = '';
    const seen = new Set();
    do {
      const params = new URLSearchParams({ run_id: run, resource_id: resource,
        from: new Date(from).toISOString(), to: new Date(to).toISOString(), as_of: new Date(asOf).toISOString(), resolution_minutes: String(resolution) });
      if (cursor) params.set('cursor', cursor);
      const response = await request(`/api/ohlc?${params}`, { signal, cache: 'no-store' });
      if (!response.ok) throw new Error(response.status === 416 ? 'outside' : 'unavailable');
      const page = await response.json();
      if (page.run_id !== run || page.resource_id !== resource || !Array.isArray(page.candles)) throw new Error('invalid history');
      for (const row of page.candles) {
        const time = Date.parse(row.minute_start);
        if (!Number.isFinite(time) || time < from || time >= to ||
            !['open', 'high', 'low', 'close'].every(k => Number.isFinite(row[k])) ||
            !Number.isInteger(row.sample_count) || row.sample_count < 1) throw new Error('invalid candle');
        rows.set(time, { ...row, end: row.end ? Date.parse(row.end) : time + MINUTE });
      }
      cursor = page.cursor;
      if (cursor && seen.has(cursor)) throw new Error('repeated cursor');
      seen.add(cursor);
    } while (cursor);
  }};
  try { await Promise.all([worker(), worker()]); } catch (error) { failed = true; throw error; }
  return [...rows.values()].sort((a, b) => Date.parse(a.minute_start) - Date.parse(b.minute_start));
}

// A gap always ends a bucket. UTC-aligned density buckets never alter the source.
export function aggregate(rows, start, end, maxBars = 160) {
  const minutes = Math.max(1, Math.ceil((end - start) / MINUTE / maxBars));
  const width = minutes * MINUTE;
  const result = [];
  let previous = -Infinity;
  for (const row of rows) {
    const time = Date.parse(row.minute_start), bucket = Math.floor(time / width);
    const last = result.at(-1);
    if (!last || last.bucket !== bucket || time !== previous) {
      result.push({ ...row, bucket, end: row.end || time + MINUTE, minutes: row.minutes || 1, partial: row.partial ?? row.sample_count < 60 });
    } else {
      last.high = Math.max(last.high, row.high); last.low = Math.min(last.low, row.low);
      last.close = row.close; last.sample_count += row.sample_count;
      last.last_observed_at = row.last_observed_at; last.end = row.end || time + MINUTE;
      last.minutes += row.minutes || 1; last.partial ||= row.partial ?? row.sample_count < 60;
    }
    previous = row.end || time + MINUTE;
  }
  return result;
}
