import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPoller } from './poller.js';
import { lowerBoundApplied } from './model.js';
import './style.css';

type Snapshot = { run_id: string; run_generation: number; step: number; resource: number; parameter: number; actual_delta: number; requested_delta: number; calculated_at: string; published_at: string; api_time: string; stale: boolean; model: { coefficient: number; parameter_min: number; parameter_max: number; interval_seconds: number }; build_commit: string };
const number = (value: number, digits = 2) => (Object.is(value, -0) || Math.abs(value) < .5 * 10 ** -digits ? 0 : value).toLocaleString('ja-JP', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const date = (value: string) => new Date(value).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', hour12: false });

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState('');
  const [clock, setClock] = useState(Date.now());
  const [receivedAt, setReceivedAt] = useState(0);
  useEffect(() => {
    const poller = createPoller({
      request: async (signal: AbortSignal) => {
        const response = await fetch('/api/current', { signal, cache: 'no-store' });
        if (!response.ok) throw new Error(response.status === 404 ? 'empty' : 'unavailable');
        return response.json();
      },
      render: (value: Snapshot) => { setSnapshot(value); setReceivedAt(Date.now()); setError(''); },
      failed: (cause: unknown) => setError(cause instanceof Error && cause.message === 'empty' ? '公開データ未作成' : '取得失敗・再接続待ち'),
      visible: () => document.visibilityState === 'visible',
    });
    const visibility = () => document.visibilityState === 'visible' ? poller.resume() : poller.pause();
    const online = () => poller.resume();
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('online', online);
    poller.resume();
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => { poller.pause(); clearInterval(timer); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('online', online); };
  }, []);
  // Age with API time, then advance locally, so phone clock skew does not hide a stall.
  const age = snapshot ? (Date.parse(snapshot.api_time) - Math.min(Date.parse(snapshot.calculated_at), Date.parse(snapshot.published_at)) + Math.max(0, clock - receivedAt)) / 1000 : 0;
  const stale = snapshot && (snapshot.stale || age >= 10);
  return <main>
    <header><div className="mark">R<span>↗</span></div><div><p className="eyebrow">RESOURCE OBSERVATORY · LIVE EXPERIMENT</p><h1>変化を、眺める。</h1></div></header>
    <div className="status" role="status"><span className={error || stale ? 'dot waiting' : 'dot'} />{error || (stale ? '更新停滞・通信待ち' : snapshot ? '公開状態を受信中' : '初回データを取得中…')}<small>約1秒ごとに更新</small></div>
    {!snapshot && <section className="empty"><h2>公開値を待っています</h2><p>{error === '公開データ未作成' ? 'PCからの初回同期を待っています。公開後は自動で表示します。' : error ? '取得に失敗しました。接続が戻ると自動で再取得します。' : 'PCから同期された最新の保存状態を読み取っています。'}</p></section>}
    {snapshot && <><section className="hero"><p className="eyebrow">RESOURCE / リソース量</p><div className="resource" data-testid="resource">{number(snapshot.resource)}</div><div className="delta">実増減量 <strong>{snapshot.actual_delta >= 0 ? '+' : '−'}{number(Math.abs(snapshot.actual_delta))}</strong><span> / step</span></div><p className="note">{lowerBoundApplied(snapshot) ? `要求増減量 ${number(snapshot.requested_delta)}。下限0により減少を制限しました。` : 'ローカルで保存が完了した、同じステップの数値です。'}</p></section>
    <section className="metrics"><article><p>PARAMETER / パラメータ</p><strong>{number(snapshot.parameter, 3)}</strong></article><article><p>STEP / 計算経過</p><strong data-testid="step">{snapshot.step.toLocaleString('ja-JP')}<small>秒</small></strong></article></section>
    <section className="details"><h2>更新情報</h2><dl><dt>計算日時</dt><dd>{date(snapshot.calculated_at)} JST</dd><dt>クラウド公開日時</dt><dd>{date(snapshot.published_at)} JST</dd><dt>更新状態</dt><dd>{stale ? '更新が止まっています。PC停止・通信障害の原因は断定できません。' : '最新の公開値を表示しています。'}</dd></dl></section>
    <section className="model"><h2>この実験のルール</h2><p>1ステップは{snapshot.model.interval_seconds}秒。パラメータPを{snapshot.model.parameter_min}以上{snapshot.model.parameter_max}未満の一様乱数で更新し、係数{snapshot.model.coefficient} × Pをリソース量へ加えます。リソースの下限は0です。</p><p>PC停止中の時間は計算しません。停止後も最終公開値を表示します。この画面は閲覧専用です。</p></section></>}
    <section className="history"><span>COMING LATER</span><h2>履歴グラフは未実装です</h2><p>今回の画面は最新値のみ。架空の履歴やグラフは表示していません。</p></section>
    <footer>計算は自宅PC / 表示時刻は日本時間{snapshot && <span>build {snapshot.build_commit?.slice(0, 7)}</span>}</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App />);
