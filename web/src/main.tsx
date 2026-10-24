import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPoller } from './poller.js';
import { lowerBoundApplied, resourceList } from './model.js';
import './style.css';
import './history.css';
import { HistoryChart } from './HistoryChart';

type Resource = { id: string; name: string; available: boolean; resource: number; actual_delta: number; requested_delta: number; parameter?: number; schedule?: { mode: string; multiplier: number; acquisition: string; target_date: string; morning_value: number | null; evening_value: number | null } };
type Snapshot = { schema_version: number; run_id: string; run_generation: number; step: number; resources: Resource[]; calculated_at: string; published_at: string; api_time: string; stale: boolean; model: { coefficient: number; parameter_min: number; parameter_max: number; interval_seconds: number }; build_commit: string };
const number = (value: number, digits = 2) => (Object.is(value, -0) || Math.abs(value) < .5 * 10 ** -digits ? 0 : value).toLocaleString('ja-JP', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const date = (value: string) => new Date(value).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', hour12: false });

const signed = (value: number) => `${value <= -.005 ? '−' : '+'}${number(Math.abs(value))}`;

function ResourceCard({ resource, snapshot }: { resource: Resource; snapshot: Snapshot }) {
  return <article className={`resource-card ${resource.id === 'resource' ? 'alpha' : 'beta'}`} data-testid={`card-${resource.id}`}>
    <div className="card-top"><h2>{resource.name}</h2>
    {resource.available && <><div><span className="eyebrow">現在量</span><div className="resource" data-testid={resource.id === 'resource' ? 'resource' : 'beta-resource'}>{number(resource.resource)}</div></div>
    <div className="delta">実増減量 <strong>{signed(resource.actual_delta)}</strong><span> / step</span></div></>}
    </div>
    {!resource.available ? <div className="unavailable"><strong>未提供</strong><p>この公開データには{resource.name}が含まれていません。</p></div> : <>
      <HistoryChart run={snapshot.run_id} resource={resource.id} apiTime={snapshot.api_time} />
      <p className="note">同じステップで保存が完了した数値です。</p>
      {lowerBoundApplied(resource) && <p className="note">要求増減量 {signed(resource.requested_delta)}。下限0により減少を制限しました。</p>}
      {resource.parameter !== undefined && <p className="note">パラメータP：{number(resource.parameter, 3)}</p>}
      {resource.schedule && <div className="beta-schedule">
        <p>{resource.schedule.mode === 'day' ? '昼間' : '夜間'}モード · 適用倍率 <strong>{number(resource.schedule.multiplier)}</strong></p>
        <p>{resource.schedule.acquisition === 'acquired' ? '基準値取得済み' : resource.schedule.acquisition === 'missing' ? '基準値未確定（代替倍率5）' : '基準値取得中（昼間倍率5）'}</p>
        <p>{resource.schedule.target_date} JST<br />04時：{resource.schedule.morning_value === null ? '未取得' : number(resource.schedule.morning_value)} / 18時：{resource.schedule.evening_value === null ? '未取得' : number(resource.schedule.evening_value)}</p>
      </div>}
    </>}
  </article>;
}

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
        const value = await response.json();
        return { ...value, resources: resourceList(value) };
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
    {snapshot && <><section className="resource-grid" aria-label="リソース一覧">{snapshot.resources.map(resource => <ResourceCard key={`${snapshot.run_id}-${resource.id}`} resource={resource} snapshot={snapshot} />)}</section>
    <section className="step-summary">共通 STEP / 計算経過 <strong data-testid="step">{snapshot.step.toLocaleString('ja-JP')}<small>秒</small></strong></section>
    <section className="details"><h2>更新情報</h2><dl><dt>計算日時</dt><dd>{date(snapshot.calculated_at)} JST</dd><dt>クラウド公開日時</dt><dd>{date(snapshot.published_at)} JST</dd><dt>更新状態</dt><dd>{stale ? '更新が止まっています。PC停止・通信障害の原因は断定できません。' : '最新の公開値を表示しています。'}</dd></dl></section>
    <section className="model"><h2>この実験のルール</h2><p>1ステップは{snapshot.model.interval_seconds}秒。パラメータPを{snapshot.model.parameter_min}以上{snapshot.model.parameter_max}未満の一様乱数で更新し、係数{snapshot.model.coefficient} × PをAlphaへ加えます。Betaは同じステップのAlphaの実増減量に連動します。両リソースの下限は0です。</p><p>Betaは日本時間04時から18時まで倍率5。夜は同じ日の18時と04時のBeta基準値の差の絶対値を倍率とし、翌04時まで固定します。基準値が揃わない夜は倍率5です。</p><p>PC停止中の時間は計算しません。停止後も最終公開値を表示します。この画面は閲覧専用です。</p></section></>}
    <footer>計算は自宅PC / 表示時刻は日本時間{snapshot && <span>build {snapshot.build_commit?.slice(0, 7)}</span>}</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App />);
