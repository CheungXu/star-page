"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";

// ---------------- 类型（镜像后端 schemas/analytics.py） ----------------

type RealtimeEvent = { kind: string; title: string; detail: string | null; at: string };
type RealtimeStats = {
  online_now: number;
  running_tasks: number;
  pending_tasks: number;
  failed_tasks_1h: number;
  today_new_registered: number;
  today_new_anonymous: number;
  today_gen_succeeded: number;
  today_gen_failed: number;
  today_active_users: number;
  today_recharge_cny: number;
  today_page_views: number;
  recent_events: RealtimeEvent[];
};

type MetricPoint = { date: string; value: number };
type TrendSeries = { metric: string; points: MetricPoint[] };
type TrendsResponse = { days: number; series: TrendSeries[] };

type MetricCard = { key: string; label: string; value: number; unit: string; delta_pct: number | null; hint: string | null };
type OverviewResponse = {
  days: number;
  range_start: string;
  range_end: string;
  north_star_wagp: number;
  cards: MetricCard[];
};

type RetentionRow = { cohort_date: string; cohort_size: number; cells: Record<string, number | null> };
type RetentionResponse = { kind: string; periods: number[]; rows: RetentionRow[] };

type FunnelStep = { step: string; label: string; step_order: number; count: number; rate_from_prev: number | null; rate_from_top: number | null };
type FunnelResponse = { days: number; steps: FunnelStep[] };

type DistributionItem = { key: string; label: string; value: number; share: number | null };
type EngagementResponse = {
  days: number;
  total_batches: number;
  total_tasks: number;
  continuation_rate: number | null;
  file_upload_rate: number | null;
  avg_generations_per_creator: number | null;
  model_distribution: DistributionItem[];
  skill_distribution: DistributionItem[];
};

type FailureReason = { reason: string; count: number };
type QualityResponse = {
  days: number;
  total_tasks: number;
  succeeded: number;
  failed: number;
  success_rate: number | null;
  latency_p50_sec: number | null;
  latency_p90_sec: number | null;
  latency_p99_sec: number | null;
  model_success: DistributionItem[];
  failure_reasons: FailureReason[];
  latency_trend: MetricPoint[];
};

type HotPage = {
  page_id: string;
  conversation_id: string | null;
  title: string;
  owner_label: string | null;
  page_url: string | null;
  views: number;
  uv: number;
};
type ViralityResponse = {
  days: number;
  page_views_total: number;
  page_views_external: number;
  uv_external: number;
  pages_with_external_view: number;
  generated_pages: number;
  share_ratio: number | null;
  views_per_page: number | null;
  hot_pages: HotPage[];
};

type OpsUser = {
  user_id: string;
  phone: string | null;
  display_name: string;
  is_anonymous: boolean;
  created_at: string;
  last_login_at: string | null;
  generations: number;
  succeeded: number;
  paid_balance: number;
  gift_balance: number;
  total_recharged_credits: number;
  total_spent_credits: number;
};
type OpsUserConversation = { conversation_id: string; title: string; node_count: number; created_at: string; updated_at: string };
type OpsUserProfile = OpsUser & {
  failed: number;
  success_rate: number | null;
  conversations_count: number;
  pages_count: number;
  first_generation_at: string | null;
  last_generation_at: string | null;
  recent_conversations: OpsUserConversation[];
};

type OpsCase = {
  task_id: string;
  page_id: string | null;
  conversation_id: string | null;
  page_url: string | null;
  user_label: string | null;
  model_key: string | null;
  skill_key: string | null;
  status: string;
  user_prompt: string | null;
  file_names: string[];
  duration_sec: number | null;
  created_at: string;
};
type OpsCaseEvent = { sequence: number; event_type: string; payload: Record<string, unknown>; created_at: string };
type OpsCaseDetail = OpsCase & {
  model_name: string | null;
  error_message: string | null;
  retry_count: number;
  prompt: string | null;
  model_prompt: string | null;
  model_output_text: string | null;
  extracted_file_text: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  total_cost_cny: number | null;
  page_view_count: number;
  started_at: string | null;
  finished_at: string | null;
  events: OpsCaseEvent[];
};

type Tab = "realtime" | "growth" | "retention" | "engagement" | "quality" | "virality" | "monetization" | "users" | "cases";

const TAB_LABELS: Record<Tab, string> = {
  realtime: "实时看板",
  growth: "增长",
  retention: "留存",
  engagement: "参与",
  quality: "质量",
  virality: "传播",
  monetization: "商业化",
  users: "用户明细",
  cases: "Case 查看",
};

// ---------------- 格式化 ----------------

function fmtInt(n: number): string {
  return Math.round(n).toLocaleString("zh-CN");
}
function fmtNum(n: number, digits = 1): string {
  return n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}
function fmtValue(value: number, unit: string): string {
  if (unit === "cny") return `¥${fmtNum(value, 2)}`;
  if (unit === "pct") return fmtPct(value);
  if (unit === "sec") return `${fmtNum(value, 1)}s`;
  return fmtInt(value);
}
function fmtDuration(sec: number | null): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`;
}
function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN");
}
function fmtShortDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}秒前`;
  if (s < 3600) return `${Math.floor(s / 60)}分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)}小时前`;
  return `${Math.floor(s / 86400)}天前`;
}

// ---------------- 零依赖 SVG 图表 ----------------

function LineChart({ series, height = 220, unit = "" }: { series: { name: string; points: MetricPoint[] }[]; height?: number; unit?: string }) {
  const width = 760;
  const padL = 44;
  const padR = 16;
  const padT = 14;
  const padB = 26;
  const all = series.flatMap((s) => s.points);
  if (all.length === 0) return <p className="ops-empty">暂无数据</p>;
  const labels = series[0]?.points.map((p) => p.date) ?? [];
  const maxV = Math.max(1, ...all.map((p) => p.value));
  const n = Math.max(1, labels.length - 1);
  const colors = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#06b6d4"];
  const x = (i: number) => padL + (i / n) * (width - padL - padR);
  const y = (v: number) => padT + (1 - v / maxV) * (height - padT - padB);
  const ticks = 4;

  return (
    <div className="ops-chart">
      <svg viewBox={`0 0 ${width} ${height}`} className="ops-chart-svg" role="img">
        {Array.from({ length: ticks + 1 }).map((_, i) => {
          const v = (maxV / ticks) * i;
          const yy = y(v);
          return (
            <g key={i}>
              <line x1={padL} y1={yy} x2={width - padR} y2={yy} className="ops-grid" />
              <text x={padL - 8} y={yy + 4} className="ops-axis" textAnchor="end">
                {unit === "cny" ? `¥${fmtNum(v, 0)}` : fmtNum(v, 0)}
              </text>
            </g>
          );
        })}
        {labels.map((d, i) =>
          i % Math.ceil(labels.length / 8 || 1) === 0 ? (
            <text key={d} x={x(i)} y={height - 8} className="ops-axis" textAnchor="middle">
              {fmtShortDate(d)}
            </text>
          ) : null,
        )}
        {series.map((s, si) => {
          const path = s.points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.value)}`).join(" ");
          return <path key={s.name} d={path} fill="none" stroke={colors[si % colors.length]} strokeWidth={2} />;
        })}
      </svg>
      {series.length > 1 && (
        <div className="ops-legend">
          {series.map((s, si) => (
            <span key={s.name} className="ops-legend-item">
              <i style={{ background: colors[si % colors.length] }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function BarRow({ label, value, max, hint, share }: { label: string; value: number; max: number; hint?: string; share?: number | null }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="ops-bar-row">
      <span className="ops-bar-label" title={label}>{label}</span>
      <div className="ops-bar-track">
        <div className="ops-bar-fill" style={{ width: `${Math.max(2, pct)}%` }} />
      </div>
      <span className="ops-bar-value">
        {hint ?? fmtInt(value)}
        {share !== null && share !== undefined ? <em> · {fmtPct(share)}</em> : null}
      </span>
    </div>
  );
}

function heatColor(rate: number | null): string {
  if (rate === null || rate === undefined) return "transparent";
  const a = Math.min(1, Math.max(0.08, rate));
  return `rgba(99, 102, 241, ${a.toFixed(2)})`;
}

// ---------------- 主组件 ----------------

export default function OpsPage() {
  const [authState, setAuthState] = useState<"loading" | "forbidden" | "ok">("loading");
  const [tab, setTab] = useState<Tab>("realtime");

  useEffect(() => {
    void (async () => {
      const res = await apiFetch("/api/admin/analytics/realtime");
      if (res.ok) setAuthState("ok");
      else setAuthState("forbidden");
    })();
  }, []);

  if (authState === "loading") {
    return (
      <main className="admin-page">
        <p className="admin-loading">正在校验权限…</p>
      </main>
    );
  }
  if (authState === "forbidden") {
    return (
      <main className="admin-page">
        <div className="admin-forbidden">
          <h1>需要管理员权限</h1>
          <p>运营后台仅对管理员开放，请使用管理员手机号登录后访问。</p>
          <Link href="/">返回首页</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="admin-page">
      <header className="admin-header">
        <Link className="pricing-back" href="/">← 返回首页</Link>
        <h1>运营后台</h1>
        <Link className="ops-link-admin" href="/admin">财务后台 →</Link>
      </header>

      <nav className="admin-tabs">
        {(Object.keys(TAB_LABELS) as Tab[]).map((key) => (
          <button key={key} className={tab === key ? "is-active" : ""} onClick={() => setTab(key)} type="button">
            {TAB_LABELS[key]}
          </button>
        ))}
      </nav>

      {tab === "realtime" && <RealtimeView />}
      {tab === "growth" && <GrowthView />}
      {tab === "retention" && <RetentionView />}
      {tab === "engagement" && <EngagementView />}
      {tab === "quality" && <QualityView />}
      {tab === "virality" && <ViralityView />}
      {tab === "monetization" && <MonetizationView />}
      {tab === "users" && <UsersView />}
      {tab === "cases" && <CasesView />}
    </main>
  );
}

// ---------------- 实时看板 ----------------

const EVENT_DOT: Record<string, string> = {
  register: "ops-dot-blue",
  gen_success: "ops-dot-green",
  gen_failed: "ops-dot-red",
  recharge: "ops-dot-gold",
  page_view: "ops-dot-cyan",
};

function RealtimeView() {
  const [data, setData] = useState<RealtimeStats | null>(null);

  const load = useCallback(async () => {
    const r = await apiFetch("/api/admin/analytics/realtime");
    if (r.ok) setData((await r.json()) as RealtimeStats);
  }, []);

  useEffect(() => {
    // 拉取实时数据并启动轮询；fetch-on-mount 的 setState 在此为预期行为。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  if (!data) return <p className="admin-loading">加载实时数据…</p>;

  const liveCards = [
    { label: "当前在线", value: fmtInt(data.online_now), hint: "近 5 分钟活跃" },
    { label: "生成进行中", value: fmtInt(data.running_tasks), hint: `排队 ${fmtInt(data.pending_tasks)}` },
    { label: "近 1h 失败", value: fmtInt(data.failed_tasks_1h), hint: "需关注异常" },
    { label: "今日活跃用户", value: fmtInt(data.today_active_users), hint: "注册用户 DAU" },
  ];
  const todayCards = [
    { label: "今日新增注册", value: fmtInt(data.today_new_registered) },
    { label: "今日新增匿名", value: fmtInt(data.today_new_anonymous) },
    { label: "今日成功生成", value: fmtInt(data.today_gen_succeeded), hint: `失败 ${fmtInt(data.today_gen_failed)}` },
    { label: "今日充值", value: `¥${fmtNum(data.today_recharge_cny, 2)}` },
    { label: "今日页面访问", value: fmtInt(data.today_page_views) },
  ];

  return (
    <>
      <section className="admin-section-block">
        <h3 className="admin-section-title">实时动态<span>每 15 秒自动刷新</span></h3>
        <div className="ops-live-cards">
          {liveCards.map((c) => (
            <div className="ops-live-card" key={c.label}>
              <span className="ops-live-dot" />
              <span className="ops-live-label">{c.label}</span>
              <span className="ops-live-value">{c.value}</span>
              {c.hint && <span className="ops-live-hint">{c.hint}</span>}
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">今日累计<span>UTC+8 自然日</span></h3>
        <div className="admin-cards">
          {todayCards.map((c) => (
            <div className="admin-card" key={c.label}>
              <span className="admin-card-label">{c.label}</span>
              <span className="admin-card-value">{c.value}</span>
              {c.hint && <span className="admin-card-hint">{c.hint}</span>}
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">实时事件流<span>最近的关键动作</span></h3>
        <div className="ops-feed">
          {data.recent_events.length === 0 && <p className="ops-empty">暂无事件</p>}
          {data.recent_events.map((e, i) => (
            <div className="ops-feed-item" key={`${e.kind}-${e.at}-${i}`}>
              <span className={`ops-feed-dot ${EVENT_DOT[e.kind] ?? "ops-dot-blue"}`} />
              <span className="ops-feed-title">{e.title}</span>
              {e.detail && <span className="ops-feed-detail">{e.detail}</span>}
              <span className="ops-feed-time">{relTime(e.at)}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

// ---------------- 公共：周期选择 ----------------

function RangeSelector({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  const options = [7, 30, 90];
  return (
    <div className="ops-range">
      {options.map((d) => (
        <button key={d} type="button" className={days === d ? "is-active" : ""} onClick={() => onChange(d)}>
          {d} 天
        </button>
      ))}
    </div>
  );
}

function DeltaBadge({ delta }: { delta: number | null }) {
  if (delta === null || delta === undefined) return <span className="ops-delta ops-delta-flat">—</span>;
  const up = delta >= 0;
  return <span className={`ops-delta ${up ? "ops-delta-up" : "ops-delta-down"}`}>{up ? "▲" : "▼"} {fmtPct(Math.abs(delta))}</span>;
}

// ---------------- 增长 ----------------

function GrowthView() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [trends, setTrends] = useState<TrendsResponse | null>(null);

  useEffect(() => {
    void (async () => {
      const [o, t] = await Promise.all([
        apiFetch(`/api/admin/analytics/overview?days=${days}`),
        apiFetch(`/api/admin/analytics/trends?days=${days}&metrics=dau,wau,mau,new_registered_users,new_anonymous_visitors,cumulative_registered_users`),
      ]);
      if (o.ok) setOverview((await o.json()) as OverviewResponse);
      if (t.ok) setTrends((await t.json()) as TrendsResponse);
    })();
  }, [days]);

  const seriesByKey = (keys: string[], names: string[]) =>
    (trends?.series ?? [])
      .filter((s) => keys.includes(s.metric))
      .map((s) => ({ name: names[keys.indexOf(s.metric)] ?? s.metric, points: s.points }));

  return (
    <>
      <section className="admin-section-block">
        <div className="ops-section-head">
          <h3 className="admin-section-title">核心指标<span>北极星与增长 KPI（环比上一等长周期）</span></h3>
          <RangeSelector days={days} onChange={setDays} />
        </div>
        {overview && (
          <>
            <div className="ops-northstar">
              <div className="ops-northstar-label">北极星 · WAGP</div>
              <div className="ops-northstar-value">{fmtInt(overview.north_star_wagp)}</div>
              <div className="ops-northstar-hint">最近 7 天「成功生成且产生外部访问」的页面数 — 同时约束创作活跃、生成质量与分享传播</div>
            </div>
            <div className="admin-cards">
              {overview.cards.map((c) => (
                <div className="admin-card" key={c.key}>
                  <span className="admin-card-label">{c.label}</span>
                  <span className="admin-card-value">{fmtValue(c.value, c.unit)}</span>
                  <span className="admin-card-hint"><DeltaBadge delta={c.delta_pct} /> {c.hint ?? ""}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">活跃趋势<span>DAU / WAU / MAU</span></h3>
        <LineChart series={seriesByKey(["dau", "wau", "mau"], ["DAU", "WAU", "MAU"])} />
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">新增趋势<span>新增注册 vs 新增匿名访客</span></h3>
        <LineChart series={seriesByKey(["new_registered_users", "new_anonymous_visitors"], ["新增注册", "新增匿名"])} />
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">累计注册用户<span>规模增长</span></h3>
        <LineChart series={seriesByKey(["cumulative_registered_users"], ["累计注册"])} />
      </section>
    </>
  );
}

// ---------------- 留存 ----------------

function RetentionView() {
  const [kind, setKind] = useState<"login" | "create">("login");
  const [data, setData] = useState<RetentionResponse | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await apiFetch(`/api/admin/analytics/retention?kind=${kind}&weeks=6`);
      if (r.ok) setData((await r.json()) as RetentionResponse);
    })();
  }, [kind]);

  return (
    <section className="admin-section-block">
      <div className="ops-section-head">
        <h3 className="admin-section-title">
          留存矩阵
          <span>{kind === "login" ? "登录活跃留存（是否回访）" : "回访创作留存（是否再次生成）"}</span>
        </h3>
        <div className="ops-range">
          <button type="button" className={kind === "login" ? "is-active" : ""} onClick={() => setKind("login")}>登录留存</button>
          <button type="button" className={kind === "create" ? "is-active" : ""} onClick={() => setKind("create")}>创作留存</button>
        </div>
      </div>
      <p className="ops-tip">
        按注册日 cohort，第 N 天当天的留存率（day-N 口径）。
        {kind === "create" ? "「创作留存」统计用户是否再次发起生成，比登录留存更能反映真实复用价值。" : ""}
      </p>
      {!data ? (
        <p className="admin-loading">加载留存…</p>
      ) : data.rows.length === 0 ? (
        <p className="ops-empty">暂无足够 cohort 数据</p>
      ) : (
        <div className="ops-table-scroll">
          <table className="admin-table ops-retention">
            <thead>
              <tr>
                <th>注册日</th>
                <th>规模</th>
                {data.periods.map((p) => (
                  <th key={p}>D{p}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.cohort_date}>
                  <td>{fmtShortDate(row.cohort_date)}</td>
                  <td>{fmtInt(row.cohort_size)}</td>
                  {data.periods.map((p) => {
                    const rate = row.cells[String(p)] ?? row.cells[p as unknown as string];
                    return (
                      <td key={p} className="ops-heat-cell" style={{ background: heatColor(rate ?? null) }}>
                        {rate === null || rate === undefined ? "" : fmtPct(rate, 0)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------- 参与 ----------------

function EngagementView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<EngagementResponse | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await apiFetch(`/api/admin/analytics/engagement?days=${days}`);
      if (r.ok) setData((await r.json()) as EngagementResponse);
    })();
  }, [days]);

  if (!data) return <p className="admin-loading">加载参与度…</p>;
  const modelMax = Math.max(1, ...data.model_distribution.map((d) => d.value));
  const skillMax = Math.max(1, ...data.skill_distribution.map((d) => d.value));

  return (
    <>
      <section className="admin-section-block">
        <div className="ops-section-head">
          <h3 className="admin-section-title">参与概览<span>创作深度与质量信号</span></h3>
          <RangeSelector days={days} onChange={setDays} />
        </div>
        <div className="admin-cards">
          <div className="admin-card"><span className="admin-card-label">生成轮次（batch）</span><span className="admin-card-value">{fmtInt(data.total_batches)}</span></div>
          <div className="admin-card"><span className="admin-card-label">生成任务（task）</span><span className="admin-card-value">{fmtInt(data.total_tasks)}</span></div>
          <div className="admin-card">
            <span className="admin-card-label">续写率</span>
            <span className="admin-card-value">{fmtPct(data.continuation_rate)}</span>
            <span className="admin-card-hint">高续写率＝用户在认真打磨，高价值信号</span>
          </div>
          <div className="admin-card">
            <span className="admin-card-label">文件上传使用率</span>
            <span className="admin-card-value">{fmtPct(data.file_upload_rate)}</span>
          </div>
          <div className="admin-card">
            <span className="admin-card-label">人均生成次数</span>
            <span className="admin-card-value">{data.avg_generations_per_creator ? fmtNum(data.avg_generations_per_creator, 1) : "—"}</span>
          </div>
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">模型使用分布<span>各模型生成任务占比</span></h3>
        <div className="ops-bars">
          {data.model_distribution.length === 0 && <p className="ops-empty">暂无数据</p>}
          {data.model_distribution.map((d) => (
            <BarRow key={d.key} label={d.label} value={d.value} max={modelMax} share={d.share} />
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">技能场景命中分布<span>指导场景化运营</span></h3>
        <div className="ops-bars">
          {data.skill_distribution.length === 0 && <p className="ops-empty">暂无数据</p>}
          {data.skill_distribution.map((d) => (
            <BarRow key={d.key} label={d.label} value={d.value} max={skillMax} share={d.share} />
          ))}
        </div>
      </section>
    </>
  );
}

// ---------------- 质量 ----------------

function QualityView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<QualityResponse | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await apiFetch(`/api/admin/analytics/quality?days=${days}`);
      if (r.ok) setData((await r.json()) as QualityResponse);
    })();
  }, [days]);

  if (!data) return <p className="admin-loading">加载质量数据…</p>;
  const reasonMax = Math.max(1, ...data.failure_reasons.map((r) => r.count));

  return (
    <>
      <section className="admin-section-block">
        <div className="ops-section-head">
          <h3 className="admin-section-title">生成质量<span>成功率与耗时分位</span></h3>
          <RangeSelector days={days} onChange={setDays} />
        </div>
        <div className="admin-cards">
          <div className="admin-card"><span className="admin-card-label">总任务</span><span className="admin-card-value">{fmtInt(data.total_tasks)}</span></div>
          <div className="admin-card"><span className="admin-card-label">成功率</span><span className="admin-card-value">{fmtPct(data.success_rate)}</span><span className="admin-card-hint">成功 {fmtInt(data.succeeded)} / 失败 {fmtInt(data.failed)}</span></div>
          <div className="admin-card"><span className="admin-card-label">耗时 P50</span><span className="admin-card-value">{data.latency_p50_sec ? `${fmtNum(data.latency_p50_sec, 1)}s` : "—"}</span></div>
          <div className="admin-card"><span className="admin-card-label">耗时 P90</span><span className="admin-card-value">{data.latency_p90_sec ? `${fmtNum(data.latency_p90_sec, 1)}s` : "—"}</span></div>
          <div className="admin-card"><span className="admin-card-label">耗时 P99</span><span className="admin-card-value">{data.latency_p99_sec ? `${fmtNum(data.latency_p99_sec, 1)}s` : "—"}</span></div>
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">各模型成功率<span>横向对比稳定性</span></h3>
        <div className="ops-bars">
          {data.model_success.length === 0 && <p className="ops-empty">暂无数据</p>}
          {data.model_success.map((d) => (
            <BarRow key={d.key} label={d.label} value={d.value} max={1} hint={fmtPct(d.value)} share={null} />
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">P90 耗时趋势<span>性能波动</span></h3>
        <LineChart series={[{ name: "P90 (s)", points: data.latency_trend }]} unit="sec" height={180} />
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">失败原因 TOP<span>定位生成失败根因</span></h3>
        <div className="ops-bars">
          {data.failure_reasons.length === 0 && <p className="ops-empty">该周期无失败任务</p>}
          {data.failure_reasons.map((r, i) => (
            <BarRow key={i} label={r.reason} value={r.count} max={reasonMax} share={null} />
          ))}
        </div>
      </section>
    </>
  );
}

// ---------------- 传播 ----------------

function ViralityView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ViralityResponse | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await apiFetch(`/api/admin/analytics/virality?days=${days}`);
      if (r.ok) setData((await r.json()) as ViralityResponse);
    })();
  }, [days]);

  if (!data) return <p className="admin-loading">加载传播数据…</p>;

  return (
    <>
      <section className="admin-section-block">
        <div className="ops-section-head">
          <h3 className="admin-section-title">传播力<span>生成页是本产品的天然增长引擎</span></h3>
          <RangeSelector days={days} onChange={setDays} />
        </div>
        <div className="admin-cards">
          <div className="admin-card"><span className="admin-card-label">页面访问 PV</span><span className="admin-card-value">{fmtInt(data.page_views_total)}</span><span className="admin-card-hint">外部访问 {fmtInt(data.page_views_external)}</span></div>
          <div className="admin-card"><span className="admin-card-label">外部访客 UV</span><span className="admin-card-value">{fmtInt(data.uv_external)}</span><span className="admin-card-hint">去重 IP（哈希）</span></div>
          <div className="admin-card">
            <span className="admin-card-label">分享传播比</span>
            <span className="admin-card-value">{fmtPct(data.share_ratio)}</span>
            <span className="admin-card-hint">被外部访问页面 / 生成页面</span>
          </div>
          <div className="admin-card"><span className="admin-card-label">单页均访问</span><span className="admin-card-value">{data.views_per_page ? fmtNum(data.views_per_page, 1) : "—"}</span></div>
          <div className="admin-card"><span className="admin-card-label">生成页面数</span><span className="admin-card-value">{fmtInt(data.generated_pages)}</span></div>
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">热门页 TOP<span>可沉淀为精选案例做内容运营</span></h3>
        <div className="ops-table-scroll">
          <table className="admin-table">
            <thead>
              <tr><th>标题</th><th>作者</th><th>外部访问</th><th>UV</th><th>链接</th></tr>
            </thead>
            <tbody>
              {data.hot_pages.length === 0 && (
                <tr><td colSpan={5} className="ops-empty">暂无外部访问页面</td></tr>
              )}
              {data.hot_pages.map((p) => (
                <tr key={p.page_id}>
                  <td className="ops-ellipsis" title={p.title}>{p.title}</td>
                  <td>{p.owner_label ?? "—"}</td>
                  <td>{fmtInt(p.views)}</td>
                  <td>{fmtInt(p.uv)}</td>
                  <td>{p.page_url ? <a href={p.page_url} target="_blank" rel="noreferrer">打开</a> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

// ---------------- 商业化 ----------------

function MonetizationView() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [trends, setTrends] = useState<TrendsResponse | null>(null);

  useEffect(() => {
    void (async () => {
      const [o, t] = await Promise.all([
        apiFetch(`/api/admin/analytics/overview?days=${days}`),
        apiFetch(`/api/admin/analytics/trends?days=${days}&metrics=recharge_amount_cny,new_paying_users,consume_credits,recharge_orders_paid`),
      ]);
      if (o.ok) setOverview((await o.json()) as OverviewResponse);
      if (t.ok) setTrends((await t.json()) as TrendsResponse);
    })();
  }, [days]);

  const series = (key: string, name: string) => {
    const s = trends?.series.find((x) => x.metric === key);
    return s ? [{ name, points: s.points }] : [];
  };
  const moneyCards = (overview?.cards ?? []).filter((c) =>
    ["recharge_amount_cny", "new_paying_users"].includes(c.key),
  );

  return (
    <>
      <section className="admin-section-block">
        <div className="ops-section-head">
          <h3 className="admin-section-title">商业化<span>运营视角，收入/成本以财务后台为准</span></h3>
          <RangeSelector days={days} onChange={setDays} />
        </div>
        <div className="admin-cards">
          {moneyCards.map((c) => (
            <div className="admin-card" key={c.key}>
              <span className="admin-card-label">{c.label}</span>
              <span className="admin-card-value">{fmtValue(c.value, c.unit)}</span>
              <span className="admin-card-hint"><DeltaBadge delta={c.delta_pct} /> {c.hint ?? ""}</span>
            </div>
          ))}
        </div>
        <p className="ops-tip">完整的收入、成本、毛利、记账与对账请前往 <Link href="/admin">财务后台</Link>。</p>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">充值金额趋势<span>每日实收（元）</span></h3>
        <LineChart series={series("recharge_amount_cny", "充值金额")} unit="cny" />
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">新增付费用户<span>首次付费转化</span></h3>
        <LineChart series={series("new_paying_users", "新增付费用户")} />
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">积分消耗趋势<span>需求活跃度</span></h3>
        <LineChart series={series("consume_credits", "消耗积分")} />
      </section>
    </>
  );
}

// ---------------- 用户明细 ----------------

function UsersView() {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"recent" | "generations" | "recharge">("recent");
  const [includeAnon, setIncludeAnon] = useState(false);
  const [rows, setRows] = useState<OpsUser[] | null>(null);
  const [profile, setProfile] = useState<OpsUserProfile | null>(null);

  const load = useCallback(async () => {
    setRows(null);
    const params = new URLSearchParams({ sort, include_anon: String(includeAnon), limit: "150" });
    if (q.trim()) params.set("q", q.trim());
    const r = await apiFetch(`/api/admin/analytics/users?${params.toString()}`);
    if (r.ok) setRows((await r.json()) as OpsUser[]);
    else setRows([]);
  }, [q, sort, includeAnon]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort, includeAnon]);

  const openProfile = async (id: string) => {
    setProfile(null);
    const r = await apiFetch(`/api/admin/analytics/users/${id}`);
    if (r.ok) setProfile((await r.json()) as OpsUserProfile);
  };

  return (
    <section className="admin-section-block">
      <div className="ops-section-head">
        <h3 className="admin-section-title">用户明细<span>搜索、画像与会话</span></h3>
        <div className="ops-filters">
          <input
            type="text"
            placeholder="搜手机号 / 昵称"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void load()}
          />
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
            <option value="recent">最近活跃</option>
            <option value="generations">生成最多</option>
            <option value="recharge">充值最多</option>
          </select>
          <label className="ops-check">
            <input type="checkbox" checked={includeAnon} onChange={(e) => setIncludeAnon(e.target.checked)} /> 含匿名
          </label>
          <button type="button" className="admin-save-btn" onClick={() => void load()}>查询</button>
        </div>
      </div>

      {rows === null ? (
        <p className="admin-loading">加载用户…</p>
      ) : (
        <div className="ops-table-scroll">
          <table className="admin-table">
            <thead>
              <tr>
                <th>用户</th><th>注册时间</th><th>最近活跃</th><th>生成/成功</th><th>充值积分</th><th>消费积分</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={7} className="ops-empty">无匹配用户</td></tr>}
              {rows.map((u) => (
                <tr key={u.user_id}>
                  <td>{u.phone ?? u.display_name}{u.is_anonymous && <span className="ops-tag">匿名</span>}</td>
                  <td>{fmtShortDate(u.created_at)}</td>
                  <td>{u.last_login_at ? relTime(u.last_login_at) : "—"}</td>
                  <td>{fmtInt(u.generations)} / {fmtInt(u.succeeded)}</td>
                  <td>{fmtInt(u.total_recharged_credits)}</td>
                  <td>{fmtInt(u.total_spent_credits)}</td>
                  <td><button type="button" className="ops-link-btn" onClick={() => void openProfile(u.user_id)}>画像</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {profile && <UserProfilePanel profile={profile} onClose={() => setProfile(null)} />}
    </section>
  );
}

function UserProfilePanel({ profile, onClose }: { profile: OpsUserProfile; onClose: () => void }) {
  return (
    <div className="ops-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="ops-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="ops-modal-head">
          <h3>{profile.phone ?? profile.display_name} {profile.is_anonymous && <span className="ops-tag">匿名</span>}</h3>
          <button type="button" className="ops-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="admin-cards">
          <div className="admin-card"><span className="admin-card-label">生成 / 成功率</span><span className="admin-card-value">{fmtInt(profile.generations)}</span><span className="admin-card-hint">成功率 {fmtPct(profile.success_rate)}</span></div>
          <div className="admin-card"><span className="admin-card-label">会话 / 页面</span><span className="admin-card-value">{fmtInt(profile.conversations_count)}</span><span className="admin-card-hint">页面 {fmtInt(profile.pages_count)}</span></div>
          <div className="admin-card"><span className="admin-card-label">余额（充值/赠送）</span><span className="admin-card-value">{fmtInt(profile.paid_balance)}</span><span className="admin-card-hint">赠送 {fmtInt(profile.gift_balance)}</span></div>
          <div className="admin-card"><span className="admin-card-label">累计充值 / 消费</span><span className="admin-card-value">{fmtInt(profile.total_recharged_credits)}</span><span className="admin-card-hint">消费 {fmtInt(profile.total_spent_credits)}</span></div>
        </div>
        <p className="ops-tip">
          注册 {fmtTime(profile.created_at)} · 最近登录 {fmtTime(profile.last_login_at)} ·
          首次生成 {fmtTime(profile.first_generation_at)} · 最近生成 {fmtTime(profile.last_generation_at)}
        </p>
        <h4 className="ops-modal-subtitle">最近会话</h4>
        <div className="ops-table-scroll">
          <table className="admin-table">
            <thead><tr><th>标题</th><th>节点</th><th>更新时间</th></tr></thead>
            <tbody>
              {profile.recent_conversations.length === 0 && <tr><td colSpan={3} className="ops-empty">暂无会话</td></tr>}
              {profile.recent_conversations.map((c) => (
                <tr key={c.conversation_id}>
                  <td className="ops-ellipsis" title={c.title}>{c.title}</td>
                  <td>{fmtInt(c.node_count)}</td>
                  <td>{fmtTime(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------- Case 查看 ----------------

const CASE_STATUS_LABELS: Record<string, string> = {
  pending: "排队",
  running: "进行中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
};

function CasesView() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<OpsCase[] | null>(null);
  const [detail, setDetail] = useState<OpsCaseDetail | null>(null);

  const load = useCallback(async () => {
    setRows(null);
    const params = new URLSearchParams({ limit: "100" });
    if (status) params.set("status", status);
    if (q.trim()) params.set("q", q.trim());
    const r = await apiFetch(`/api/admin/analytics/cases?${params.toString()}`);
    if (r.ok) setRows((await r.json()) as OpsCase[]);
    else setRows([]);
  }, [status, q]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const openDetail = async (id: string) => {
    setDetail(null);
    const r = await apiFetch(`/api/admin/analytics/cases/${id}`);
    if (r.ok) setDetail((await r.json()) as OpsCaseDetail);
  };

  return (
    <section className="admin-section-block">
      <div className="ops-section-head">
        <h3 className="admin-section-title">生成 Case<span>逐条排查与精选</span></h3>
        <div className="ops-filters">
          <input
            type="text"
            placeholder="搜 prompt 关键词"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void load()}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="succeeded">成功</option>
            <option value="failed">失败</option>
            <option value="running">进行中</option>
            <option value="pending">排队</option>
            <option value="cancelled">已取消</option>
          </select>
          <button type="button" className="admin-save-btn" onClick={() => void load()}>查询</button>
        </div>
      </div>

      {rows === null ? (
        <p className="admin-loading">加载 Case…</p>
      ) : (
        <div className="ops-table-scroll">
          <table className="admin-table">
            <thead>
              <tr><th>时间</th><th>用户</th><th>模型</th><th>状态</th><th>耗时</th><th>需求</th><th></th></tr>
            </thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={7} className="ops-empty">无匹配 Case</td></tr>}
              {rows.map((c) => (
                <tr key={c.task_id}>
                  <td>{fmtTime(c.created_at)}</td>
                  <td>{c.user_label ?? "匿名"}</td>
                  <td>{c.model_key ?? "—"}</td>
                  <td><span className={`ops-status ops-status-${c.status}`}>{CASE_STATUS_LABELS[c.status] ?? c.status}</span></td>
                  <td>{fmtDuration(c.duration_sec)}</td>
                  <td className="ops-ellipsis" title={c.user_prompt ?? ""}>{c.user_prompt ?? "—"}</td>
                  <td><button type="button" className="ops-link-btn" onClick={() => void openDetail(c.task_id)}>详情</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && <CaseDetailPanel detail={detail} onClose={() => setDetail(null)} />}
    </section>
  );
}

function CaseDetailPanel({ detail, onClose }: { detail: OpsCaseDetail; onClose: () => void }) {
  return (
    <div className="ops-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="ops-modal ops-modal-wide" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="ops-modal-head">
          <h3>
            生成 Case · <span className={`ops-status ops-status-${detail.status}`}>{CASE_STATUS_LABELS[detail.status] ?? detail.status}</span>
          </h3>
          <button type="button" className="ops-modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="admin-cards">
          <div className="admin-card"><span className="admin-card-label">模型</span><span className="admin-card-value ops-card-sm">{detail.model_key ?? "—"}</span><span className="admin-card-hint">{detail.model_name ?? ""}</span></div>
          <div className="admin-card"><span className="admin-card-label">技能</span><span className="admin-card-value ops-card-sm">{detail.skill_key ?? "无"}</span></div>
          <div className="admin-card"><span className="admin-card-label">耗时 / 重试</span><span className="admin-card-value ops-card-sm">{fmtDuration(detail.duration_sec)}</span><span className="admin-card-hint">重试 {detail.retry_count}</span></div>
          <div className="admin-card"><span className="admin-card-label">Token / 成本</span><span className="admin-card-value ops-card-sm">{detail.total_tokens ?? "—"}</span><span className="admin-card-hint">{detail.total_cost_cny !== null ? `¥${fmtNum(detail.total_cost_cny, 4)}` : "—"}</span></div>
          <div className="admin-card"><span className="admin-card-label">页面访问</span><span className="admin-card-value ops-card-sm">{fmtInt(detail.page_view_count)}</span><span className="admin-card-hint">{detail.user_label ?? "匿名"}</span></div>
        </div>

        {detail.error_message && (
          <div className="ops-error-box">失败原因：{detail.error_message}</div>
        )}

        {detail.page_url && (
          <p className="ops-tip">页面链接：<a href={detail.page_url} target="_blank" rel="noreferrer">{detail.page_url}</a></p>
        )}

        <CaseField label="用户原始需求" value={detail.user_prompt} />
        {detail.file_names.length > 0 && <CaseField label="上传文件" value={detail.file_names.join("、")} />}
        <CaseField label="最终输入模型的 Prompt" value={detail.model_prompt ?? detail.prompt} collapsed />
        <CaseField label="模型原始输出" value={detail.model_output_text} collapsed />

        <h4 className="ops-modal-subtitle">生成事件流（SSE）</h4>
        <div className="ops-events">
          {detail.events.length === 0 && <p className="ops-empty">无事件记录</p>}
          {detail.events.map((e) => (
            <div className="ops-event-row" key={e.sequence}>
              <span className="ops-event-seq">#{e.sequence}</span>
              <span className="ops-event-type">{e.event_type}</span>
              <span className="ops-event-time">{fmtTime(e.created_at)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CaseField({ label, value, collapsed = false }: { label: string; value: string | null; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  if (!value) return null;
  return (
    <div className="ops-field">
      <button type="button" className="ops-field-head" onClick={() => setOpen((v) => !v)}>
        <span>{label}</span>
        <span className="ops-field-toggle">{open ? "收起" : "展开"}</span>
      </button>
      {open && <pre className="ops-field-body">{value}</pre>}
    </div>
  );
}
