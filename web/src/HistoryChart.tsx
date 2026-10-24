import React, { useEffect, useMemo, useRef, useState } from 'react';
import { aggregate, bounds, loadCachedHistory, periodLabels } from './history.js';

const date = (time: number | string) => new Date(time).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', hour12: false });
const number = (n: number) => n.toLocaleString('ja-JP', { maximumFractionDigits: 2 });
const axis = (n: number) => Math.abs(n) >= 1e6 ? n.toExponential(1) : number(n);
type Candle = { minute_start: string; open: number; high: number; low: number; close: number; sample_count: number; minutes?: number; partial?: boolean; end?: number };

export function HistoryChart({ run, resource, apiTime }: { run: string; resource: string; apiTime: string }) {
  const [period, setPeriod] = useState('today');
  const [data, setData] = useState<{ rows: Candle[]; start: number; end: number } | null>(null);
  const [status, setStatus] = useState('loading');
  const [selected, setSelected] = useState<number | null>(null);
  const [width, setWidth] = useState(500);
  const container = useRef<HTMLDivElement>(null);
  const cache = useRef(new Map());
  const time = useRef({ value: apiTime, received: Date.now() });
  if (time.current.value !== apiTime) time.current = { value: apiTime, received: Date.now() };
  useEffect(() => {
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let disposed = false, active = false, revision = 0;
    let controller = new AbortController();
    setData(null); setSelected(null); setStatus('loading');
    const refresh = async (force = false) => {
      if ((active && !force) || document.visibilityState !== 'visible') return;
      if (force) controller.abort();
      const ticket = ++revision;
      active = true;
      controller = new AbortController();
      const requestController = controller;
      const [start, end] = bounds(period, Date.parse(time.current.value) + Math.max(0, Date.now() - time.current.received));
      const timeout = window.setTimeout(() => requestController.abort(), period === 'quarter' || period === 'month' ? 180000 : 55000);
      try {
        const rows = await loadCachedHistory({ run, resource, start, end, signal: controller.signal }, cache.current);
        if (!disposed && ticket === revision) { setData({ rows, start, end }); setSelected(null); setStatus('ready'); }
      } catch (error) {
        requestController.abort();
        if (!disposed && ticket === revision) setStatus(error instanceof Error && error.message === 'outside' ? 'outside' : 'failed');
      } finally { clearTimeout(timeout); if (ticket === revision) active = false; }
    };
    const resume = () => { if (document.visibilityState === 'visible') void refresh(true); else { revision++; controller.abort(); active = false; } };
    void refresh();
    const timer = window.setInterval(refresh, 60000);
    document.addEventListener('visibilitychange', resume); window.addEventListener('online', resume);
    return () => { disposed = true; controller.abort(); clearInterval(timer); document.removeEventListener('visibilitychange', resume); window.removeEventListener('online', resume); };
  }, [run, resource, period]);
  const bars: Candle[] = useMemo(() => data ? aggregate(data.rows, data.start, data.end, Math.max(30, Math.floor(width / 5))) : [], [data, width]);
  const low = bars.length ? Math.min(...bars.map(b => b.low)) : 0;
  const high = bars.length ? Math.max(...bars.map(b => b.high)) : 1;
  const span = high - low || Math.max(Math.abs(high) * .01, 1);
  const canvas = Math.max(240, width), plot = canvas - 76;
  const y = (value: number) => 22 + (.08 + (high === low ? .5 : (high - value) / span)) / 1.16 * 224;
  const x = (value: number) => 60 + (value - (data?.start || 0)) / Math.max(1, (data?.end || 1) - (data?.start || 0)) * plot;
  const choose = (clientX: number, svg: SVGSVGElement) => {
    if (!data || !bars.length) return;
    const rect = svg.getBoundingClientRect();
    const at = data.start + ((clientX - rect.left) / rect.width * canvas - 60) / plot * (data.end - data.start);
    let best = 0;
    bars.forEach((b, i) => { if (Math.abs(Date.parse(b.minute_start) - at) < Math.abs(Date.parse(bars[best].minute_start) - at)) best = i; });
    setSelected(best);
  };
  const picked = selected === null ? null : bars[selected];
  const hasGap = data && data.rows.some((r, i) => i && Date.parse(r.minute_start) > (data.rows[i - 1].end || Date.parse(data.rows[i - 1].minute_start) + 60000));
  return <div className="chart-section" ref={container}>
    <div className="chart-toolbar"><span>確定OHLC · JST</span><div className="periods" aria-label="履歴期間">{Object.entries(periodLabels).map(([key, label]) => <button key={key} aria-pressed={period === key} onClick={() => setPeriod(key)}>{String(label)}</button>)}</div></div>
    <p className={`history-status ${status === 'failed' ? 'failure' : ''}`} aria-live="polite">{status === 'loading' ? '履歴を取得中…' : status === 'outside' ? '対象期間外 · 期間を選び直してください' : status === 'failed' ? `履歴取得失敗${data ? ' · 取得済みの古いグラフを表示中' : ' · 再接続待ち'}` : !bars.length ? '確定履歴未作成 · 未同期／Beta追加前の期間を含む場合があります' : `${hasGap ? '欠測区間あり · ' : ''}1分足${data?.rows.reduce((sum, r) => sum + (r.minutes || 1), 0).toLocaleString()}件${bars.some(b => (b.minutes || 1) > 1) ? ' · 表示密度に合わせて集約' : ''}`}</p>
    <svg className="candlestick" viewBox={`0 0 ${canvas} 290`} style={{ height: 290 }} role="img" aria-label="確定OHLCローソク足。横軸は日本時間、縦軸はリソース量。クリックまたは左右キーで足を選択。" tabIndex={bars.length ? 0 : -1}
      onPointerDown={event => choose(event.clientX, event.currentTarget)}
      onKeyDown={event => { if (bars.length && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); setSelected(i => event.key === 'Home' ? 0 : event.key === 'End' ? bars.length - 1 : Math.max(0, Math.min(bars.length - 1, (i ?? 0) + (event.key === 'ArrowRight' ? 1 : -1)))); } }}>
      <rect x="60" y="15" width={plot} height="240" fill="white" fillOpacity=".65" rx="8" />
      {(high === low ? [.5] : [0, .5, 1]).map(f => { const value = low + (high - low) * f; return <g key={f}><line x1="60" x2={canvas - 16} y1={y(value)} y2={y(value)} className="gridline" /><text x="54" y={y(value) + 4} textAnchor="end">{axis(value)}</text></g>; })}
      {bars.map((bar, i) => { const at = Date.parse(bar.minute_start), left = x(at), bw = Math.max(.7, Math.min(12, x(bar.end || at + 60000) - left - .7)); return <g key={bar.minute_start} className={bar.close >= bar.open ? 'candle up' : 'candle down'} opacity={selected === null || selected === i ? 1 : .55}><line x1={left + bw / 2} x2={left + bw / 2} y1={y(bar.high)} y2={y(bar.low)} /><rect x={left} width={bw} y={Math.min(y(bar.open), y(bar.close))} height={Math.max(1, Math.abs(y(bar.open) - y(bar.close)))} fill={bar.close >= bar.open ? 'white' : 'currentColor'} />{bar.partial && <circle cx={left + bw / 2} cy="251" r="1.6" />}</g>; })}
      {data && (width < 400 ? [0, 1] : [0, .5, 1]).map(fraction => <text key={fraction} x={60 + fraction * plot} y="277" textAnchor={fraction === 0 ? 'start' : fraction === 1 ? 'end' : 'middle'}>{new Date(data.start + (data.end - data.start) * fraction).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}</text>)}
      {!bars.length && <text x={canvas / 2 + 15} y="140" textAnchor="middle">{status === 'loading' ? '読み込み中' : '確定足はありません'}</text>}
    </svg>
    <div className="candle-detail" aria-live="polite">{picked ? <><strong>{date(picked.minute_start)} JST{(picked.minutes || 1) > 1 ? ` ～ ${date(picked.end!)}（終端を含まない・${picked.minutes}分集約）` : '（1分足）'}</strong><span>始 {number(picked.open)} / 高 {number(picked.high)} / 安 {number(picked.low)} / 終 {number(picked.close)}</span><span>サンプル数 {picked.sample_count.toLocaleString()}{picked.partial ? ' · 60サンプル未満の分を含む' : ''}</span></> : <span>足をタップ／クリックして日時・OHLC・サンプル数を確認<br />左右キーでも選択できます。空白は観測なし、下の点は60サンプル未満の分です。</span>}</div>
  </div>;
}
