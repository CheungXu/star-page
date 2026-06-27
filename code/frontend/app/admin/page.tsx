"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import { formatCredits, txnTypeLabel } from "../lib/billing";

type LedgerAccountStat = {
  code: string;
  name: string;
  type: string;
  debit: number;
  credit: number;
  net: number;
};

type Overview = {
  // 第一行：付费业务（不含赠送）
  total_recharge_cny: number;
  paid_revenue_cny: number;
  paid_cogs_cny: number;
  paid_gross_profit_cny: number;
  paid_gross_margin: number | null;
  // 第二行：赠送台账
  gift_granted_cny: number;
  gift_unused_cny: number;
  gift_revenue_cny: number;
  gift_cogs_cny: number;
  trial_cogs_cny: number;
  // 第三行：含赠送合计
  total_revenue_cny: number;
  total_cogs_cny: number;
  total_gross_profit_cny: number;
  total_gross_margin: number | null;
  // 期间费用与营业利润
  infra_cost_cny: number;
  payment_fee_cny: number;
  operating_profit_cny: number;
  // 其他
  deferred_revenue_cny: number;
  receivable_third_party_cny: number;
  prepaid_cloud_balance_cny: number;
  prepaid_cloud_topup_cny: number;
  total_paid_balance_credits: number;
  total_gift_balance_credits: number;
  user_count: number;
  accounts: LedgerAccountStat[];
};

type ModelMarkup = {
  key: string;
  label: string;
  provider: string;
  available: boolean;
  markup: number;
  is_custom: boolean;
  pricing_summary: string | null;
};

type MarkupConfig = {
  default_markup: number;
  models: ModelMarkup[];
};

type AdminTxn = {
  id: string;
  phone: string | null;
  display_name: string | null;
  type: string;
  credits_delta: number;
  balance_after: number;
  model_key: string | null;
  raw_cost_cny: number | null;
  revenue_cny: number | null;
  memo: string | null;
  created_at: string;
};

type LedgerLine = { account_code: string; account_name: string | null; debit: number; credit: number };
type LedgerEntry = {
  id: string;
  event_type: string;
  event_ref: string | null;
  memo: string | null;
  posted_at: string;
  lines: LedgerLine[];
};

type AdminUser = {
  user_id: string;
  phone: string | null;
  display_name: string;
  is_anonymous: boolean;
  paid_balance: number;
  gift_balance: number;
  total_recharged_credits: number;
  total_spent_credits: number;
};

type Tab = "overview" | "transactions" | "ledger" | "users" | "markups" | "bill" | "wechat";

export default function AdminPage() {
  const [authState, setAuthState] = useState<"loading" | "forbidden" | "ok">("loading");
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [txns, setTxns] = useState<AdminTxn[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [markups, setMarkups] = useState<MarkupConfig | null>(null);

  useEffect(() => {
    void (async () => {
      const res = await apiFetch("/api/admin/billing/overview");
      if (res.status === 401 || res.status === 403) {
        setAuthState("forbidden");
        return;
      }
      if (res.ok) {
        setOverview((await res.json()) as Overview);
        setAuthState("ok");
      } else {
        setAuthState("forbidden");
      }
    })();
  }, []);

  useEffect(() => {
    if (authState !== "ok") return;
    void (async () => {
      if (tab === "transactions" && txns.length === 0) {
        const r = await apiFetch("/api/admin/billing/transactions");
        if (r.ok) setTxns((await r.json()) as AdminTxn[]);
      } else if (tab === "ledger" && ledger.length === 0) {
        const r = await apiFetch("/api/admin/billing/ledger");
        if (r.ok) setLedger((await r.json()) as LedgerEntry[]);
      } else if (tab === "users" && users.length === 0) {
        const r = await apiFetch("/api/admin/billing/users");
        if (r.ok) setUsers((await r.json()) as AdminUser[]);
      } else if (tab === "markups" && markups === null) {
        const r = await apiFetch("/api/admin/billing/model-markups");
        if (r.ok) setMarkups((await r.json()) as MarkupConfig);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, authState]);

  if (authState === "loading") {
    return <main className="admin-page"><p className="admin-loading">正在校验权限…</p></main>;
  }
  if (authState === "forbidden") {
    return (
      <main className="admin-page">
        <div className="admin-forbidden">
          <h1>需要管理员权限</h1>
          <p>该页面仅对财务管理员开放。请使用管理员手机号登录后访问。</p>
          <Link href="/">返回首页</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="admin-page">
      <header className="admin-header">
        <Link className="pricing-back" href="/">← 返回首页</Link>
        <h1>财务后台</h1>
      </header>

      <nav className="admin-tabs">
        {(["overview", "transactions", "ledger", "users", "markups", "bill", "wechat"] as Tab[]).map((key) => (
          <button key={key} className={tab === key ? "is-active" : ""} onClick={() => setTab(key)} type="button">
            {tabLabel(key)}
          </button>
        ))}
      </nav>

      {tab === "overview" && overview && (
        <OverviewView
          data={overview}
          onRefresh={async () => {
            const r = await apiFetch("/api/admin/billing/overview");
            if (r.ok) setOverview((await r.json()) as Overview);
          }}
        />
      )}
      {tab === "transactions" && <TransactionsView rows={txns} />}
      {tab === "ledger" && <LedgerView rows={ledger} />}
      {tab === "users" && <UsersView rows={users} />}
      {tab === "markups" && <MarkupView config={markups} onSaved={setMarkups} />}
      {tab === "bill" && <BillView />}
      {tab === "wechat" && <WechatView />}
    </main>
  );
}

function money(value: number): string {
  return `¥${value.toFixed(2)}`;
}

type SupplierBalance = {
  vendor: string;
  label: string;
  configured: boolean;
  available_amount: number | null;
  available_cash_amount: number | null;
  currency: string | null;
  fetched_at: string | null;
  error: string | null;
  note: string | null;
};

function SupplierBalances() {
  const [rows, setRows] = useState<SupplierBalance[] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (refresh: boolean) => {
    setBusy(true);
    const r = await apiFetch(`/api/admin/billing/supplier-balances${refresh ? "?refresh=true" : ""}`);
    if (r.ok) setRows((await r.json()) as SupplierBalance[]);
    setBusy(false);
  };

  useEffect(() => {
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section className="admin-section-block">
      <h3 className="admin-section-title">
        供应商真实余额对账<span>直连各云厂商财务接口，与账面预付余额核对</span>
      </h3>
      <div className="admin-balance-list">
        {rows === null && <p className="admin-loading">加载中…</p>}
        {rows?.map((b) => (
          <div className={`admin-balance-card${b.configured ? "" : " is-unconfigured"}`} key={b.vendor}>
            <div className="admin-balance-head">
              <strong>{b.label}</strong>
              {b.currency && <span className="admin-balance-cur">{b.currency}</span>}
            </div>
            {b.configured && b.available_amount !== null && b.error === null ? (
              <>
                <span className="admin-balance-amount">
                  {b.currency === "CNY" || b.currency === null ? "¥" : `${b.currency} `}
                  {b.available_amount.toFixed(2)}
                </span>
                <span className="admin-balance-hint">
                  可用现金 {b.available_cash_amount !== null ? b.available_cash_amount.toFixed(2) : "—"}
                  {b.fetched_at ? ` · ${new Date(b.fetched_at).toLocaleString("zh-CN")}` : ""}
                </span>
              </>
            ) : (
              <span className="admin-balance-note">{b.error ?? b.note ?? "暂无数据"}</span>
            )}
          </div>
        ))}
      </div>
      <div className="admin-balance-actions">
        <button type="button" className="admin-save-btn" onClick={() => void load(true)} disabled={busy}>
          {busy ? "刷新中…" : "刷新真实余额"}
        </button>
      </div>
    </section>
  );
}

function SupplierTopupForm({ onDone }: { onDone: () => Promise<void> }) {
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const submit = async () => {
    const val = Number(amount);
    if (!Number.isFinite(val) || val <= 0) {
      setMsg({ kind: "err", text: "请输入大于 0 的充值金额（元）" });
      return;
    }
    setBusy(true);
    setMsg(null);
    const res = await apiFetch("/api/admin/billing/supplier-topup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount_cny: val, memo: memo || null }),
    });
    setBusy(false);
    if (res.ok) {
      setAmount("");
      setMemo("");
      setMsg({ kind: "ok", text: "已记账，预付云资源余额已更新" });
      await onDone();
    } else {
      let detail = "记账失败";
      try {
        const body = (await res.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // ignore
      }
      setMsg({ kind: "err", text: detail });
    }
  };

  return (
    <div className="admin-topup">
      <div className="admin-topup-row">
        <label>记一笔云账户充值</label>
        <input
          type="number"
          step="0.01"
          min="0.01"
          placeholder="金额（元）"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        <input
          type="text"
          placeholder="备注（如：阿里云百炼 6 月充值）"
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
        />
        <button type="button" className="admin-save-btn" onClick={() => void submit()} disabled={busy}>
          {busy ? "记账中…" : "记账"}
        </button>
      </div>
      {msg && <span className={msg.kind === "ok" ? "admin-markup-ok" : "admin-markup-err"}>{msg.text}</span>}
      <p className="admin-topup-tip">
        借「预付账款-云/LLM供应商」、贷「现金/在途」。每次模型调用按实际成本冲减该预付资产。
      </p>
    </div>
  );
}

function OverviewView({ data, onRefresh }: { data: Overview; onRefresh: () => Promise<void> }) {
  const paidRow = [
    { label: "累计充值现金", value: money(data.total_recharge_cny), hint: "用户真实付费" },
    { label: "付费确认收入", value: money(data.paid_revenue_cny), hint: "充值积分消费确认" },
    { label: "付费算力成本", value: money(data.paid_cogs_cny), hint: "付费消费对应 LLM 成本" },
    {
      label: "付费毛利",
      value: money(data.paid_gross_profit_cny),
      hint: data.paid_gross_margin !== null ? `毛利率 ${(data.paid_gross_margin * 100).toFixed(1)}%` : "—",
    },
  ];
  const giftRow = [
    { label: "赠送已发放", value: money(data.gift_granted_cny), hint: "计入推广费用" },
    { label: "赠送未用负债", value: money(data.gift_unused_cny), hint: "尚未核销的赠送" },
    { label: "赠送已核销收入", value: money(data.gift_revenue_cny), hint: "用赠送积分消费确认" },
    { label: "赠送/试用成本", value: money(data.gift_cogs_cny + data.trial_cogs_cny), hint: `含匿名试用 ${money(data.trial_cogs_cny)}` },
  ];
  const totalRow = [
    { label: "综合确认收入", value: money(data.total_revenue_cny), hint: "付费 + 赠送核销" },
    { label: "综合算力成本", value: money(data.total_cogs_cny), hint: "全部 LLM 成本" },
    {
      label: "综合毛利",
      value: money(data.total_gross_profit_cny),
      hint: data.total_gross_margin !== null ? `毛利率 ${(data.total_gross_margin * 100).toFixed(1)}%` : "—",
    },
  ];
  return (
    <>
      <section className="admin-section-block">
        <h3 className="admin-section-title">付费业务（不含赠送）<span>真实付费现金业务的财务状况</span></h3>
        <div className="admin-cards">
          {paidRow.map((c) => (
            <div className="admin-card" key={c.label}>
              <span className="admin-card-label">{c.label}</span>
              <span className="admin-card-value">{c.value}</span>
              <span className="admin-card-hint">{c.hint}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">赠送台账<span>新用户赠送额度的发放、负债与核销</span></h3>
        <div className="admin-cards">
          {giftRow.map((c) => (
            <div className="admin-card is-gift" key={c.label}>
              <span className="admin-card-label">{c.label}</span>
              <span className="admin-card-value">{c.value}</span>
              <span className="admin-card-hint">{c.hint}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">含赠送合计<span>把赠送确认收入与全部成本一并计入</span></h3>
        <div className="admin-cards">
          {totalRow.map((c) => (
            <div className="admin-card is-total" key={c.label}>
              <span className="admin-card-label">{c.label}</span>
              <span className="admin-card-value">{c.value}</span>
              <span className="admin-card-hint">{c.hint}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">营业利润<span>含赠送毛利再扣除基础设施与支付手续费</span></h3>
        <div className="admin-cards">
          <div className="admin-card is-gift" key="infra">
            <span className="admin-card-label">基础设施成本（服务器等）</span>
            <span className="admin-card-value">{money(data.infra_cost_cny)}</span>
            <span className="admin-card-hint">阿里云账单非百炼部分，期间费用</span>
          </div>
          <div className="admin-card is-gift" key="fee">
            <span className="admin-card-label">支付手续费</span>
            <span className="admin-card-value">{money(data.payment_fee_cny)}</span>
            <span className="admin-card-hint">微信等渠道结算手续费（6603）</span>
          </div>
          <div className="admin-card is-total" key="op">
            <span className="admin-card-label">营业利润</span>
            <span className="admin-card-value">{money(data.operating_profit_cny)}</span>
            <span className="admin-card-hint">综合毛利 − 基础设施 − 手续费</span>
          </div>
        </div>
      </section>

      <section className="admin-section-block">
        <h3 className="admin-section-title">资产负债与在途<span>预收、预付云资源等科目</span></h3>
        <div className="admin-cards">
          <div className="admin-card" key="deferred">
            <span className="admin-card-label">预收账款（未消费充值）</span>
            <span className="admin-card-value">{money(data.deferred_revenue_cny)}</span>
            <span className="admin-card-hint">递延收入（负债）</span>
          </div>
          <div className="admin-card" key="receivable">
            <span className="admin-card-label">应收第三方支付（待结算）</span>
            <span className="admin-card-value">{money(data.receivable_third_party_cny)}</span>
            <span className="admin-card-hint">微信已收款、尚未结算到银行</span>
          </div>
          <div className="admin-card" key="prepaid-balance">
            <span className="admin-card-label">预付云资源余额</span>
            <span className="admin-card-value">{money(data.prepaid_cloud_balance_cny)}</span>
            <span className="admin-card-hint">云账户剩余可用（资产）</span>
          </div>
          <div className="admin-card" key="prepaid-topup">
            <span className="admin-card-label">累计云账户充值</span>
            <span className="admin-card-value">{money(data.prepaid_cloud_topup_cny)}</span>
            <span className="admin-card-hint">已耗用 {money(data.prepaid_cloud_topup_cny - data.prepaid_cloud_balance_cny)}</span>
          </div>
        </div>
        <SupplierTopupForm onDone={onRefresh} />
      </section>

      <SupplierBalances />

      <section className="admin-subgrid">
        <div className="admin-mini">
          <span>注册用户数</span>
          <strong>{data.user_count}</strong>
        </div>
        <div className="admin-mini">
          <span>充值积分余额（全站）</span>
          <strong>{formatCredits(data.total_paid_balance_credits)}</strong>
        </div>
        <div className="admin-mini">
          <span>赠送积分余额（全站）</span>
          <strong>{formatCredits(data.total_gift_balance_credits)}</strong>
        </div>
      </section>

      <section className="admin-table-wrap">
        <h3>科目余额（复式总账）</h3>
        <table className="admin-table">
          <thead>
            <tr>
              <th>科目</th>
              <th>名称</th>
              <th>类型</th>
              <th>借方</th>
              <th>贷方</th>
              <th>净额</th>
            </tr>
          </thead>
          <tbody>
            {data.accounts.map((a) => (
              <tr key={a.code}>
                <td>{a.code}</td>
                <td>{a.name}</td>
                <td>{accountTypeLabel(a.type)}</td>
                <td>{a.debit.toFixed(2)}</td>
                <td>{a.credit.toFixed(2)}</td>
                <td>{a.net.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function TransactionsView({ rows }: { rows: AdminTxn[] }) {
  return (
    <section className="admin-table-wrap">
      <h3>积分流水</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>用户</th>
            <th>类型</th>
            <th>变动</th>
            <th>余额</th>
            <th>模型</th>
            <th>成本¥</th>
            <th>收入¥</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.created_at).toLocaleString("zh-CN")}</td>
              <td>{r.phone ?? r.display_name ?? "-"}</td>
              <td>{txnTypeLabel(r.type)}</td>
              <td className={r.credits_delta >= 0 ? "pos" : "neg"}>{r.credits_delta}</td>
              <td>{r.balance_after}</td>
              <td>{r.model_key ?? "-"}</td>
              <td>{r.raw_cost_cny !== null ? r.raw_cost_cny.toFixed(4) : "-"}</td>
              <td>{r.revenue_cny !== null ? r.revenue_cny.toFixed(2) : "-"}</td>
              <td>{r.memo ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function LedgerView({ rows }: { rows: LedgerEntry[] }) {
  return (
    <section className="admin-table-wrap">
      <h3>记账凭证</h3>
      {rows.map((entry) => (
        <div className="admin-ledger-entry" key={entry.id}>
          <div className="admin-ledger-head">
            <strong>{entry.event_type}</strong>
            <span>{new Date(entry.posted_at).toLocaleString("zh-CN")}</span>
            <span className="admin-ledger-memo">{entry.memo}</span>
          </div>
          <table className="admin-table admin-ledger-lines">
            <tbody>
              {entry.lines.map((line, i) => (
                <tr key={i}>
                  <td>{line.account_code}</td>
                  <td>{line.account_name ?? ""}</td>
                  <td className="pos">{line.debit > 0 ? `借 ${line.debit.toFixed(2)}` : ""}</td>
                  <td className="neg">{line.credit > 0 ? `贷 ${line.credit.toFixed(2)}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}

function UsersView({ rows }: { rows: AdminUser[] }) {
  return (
    <section className="admin-table-wrap">
      <h3>用户对账</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>用户</th>
            <th>充值余额</th>
            <th>赠送余额</th>
            <th>累计充值</th>
            <th>累计消费</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((u) => (
            <tr key={u.user_id}>
              <td>{u.phone ?? u.display_name}</td>
              <td>{formatCredits(u.paid_balance)}</td>
              <td>{formatCredits(u.gift_balance)}</td>
              <td>{formatCredits(u.total_recharged_credits)}</td>
              <td>{formatCredits(u.total_spent_credits)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function MarkupView({ config, onSaved }: { config: MarkupConfig | null; onSaved: (c: MarkupConfig) => void }) {
  const [defaultMarkup, setDefaultMarkup] = useState<string>("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    if (!config) return;
    setDefaultMarkup(String(config.default_markup));
    const next: Record<string, string> = {};
    config.models.forEach((m) => {
      next[m.key] = String(m.markup);
    });
    setEdits(next);
  }, [config]);

  if (!config) {
    return <section className="admin-table-wrap"><p className="admin-loading">正在加载模型倍率…</p></section>;
  }

  const save = async () => {
    setSaving(true);
    setMsg(null);
    const defVal = Number(defaultMarkup);
    if (!Number.isFinite(defVal) || defVal <= 0) {
      setMsg({ kind: "err", text: "默认倍率必须为大于 0 的数字" });
      setSaving(false);
      return;
    }
    const modelMarkups: Record<string, number> = {};
    for (const m of config.models) {
      const raw = edits[m.key];
      const val = Number(raw);
      if (!Number.isFinite(val) || val <= 0) {
        setMsg({ kind: "err", text: `模型「${m.label}」的倍率必须为大于 0 的数字` });
        setSaving(false);
        return;
      }
      modelMarkups[m.key] = val;
    }
    const res = await apiFetch("/api/admin/billing/model-markups", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_markup: defVal, model_markups: modelMarkups }),
    });
    setSaving(false);
    if (res.ok) {
      const updated = (await res.json()) as MarkupConfig;
      onSaved(updated);
      setMsg({ kind: "ok", text: "已保存，配置即时生效" });
    } else {
      let detail = "保存失败";
      try {
        const body = (await res.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // ignore
      }
      setMsg({ kind: "err", text: detail });
    }
  };

  return (
    <section className="admin-table-wrap">
      <div className="admin-markup-head">
        <h3>模型倍率配置</h3>
        <p className="admin-markup-tip">
          扣费 = 模型实际成本 × 倍率，向上取整、至少 1 积分。倍率小于 1 即对该模型让利促销。
          留空模型沿用默认倍率。
        </p>
        <div className="admin-markup-default">
          <label>默认倍率</label>
          <input
            type="number"
            step="0.05"
            min="0.01"
            value={defaultMarkup}
            onChange={(e) => setDefaultMarkup(e.target.value)}
          />
        </div>
      </div>
      <table className="admin-table">
        <thead>
          <tr>
            <th>模型</th>
            <th>厂商</th>
            <th>状态</th>
            <th>成本基准</th>
            <th>倍率</th>
            <th>来源</th>
          </tr>
        </thead>
        <tbody>
          {config.models.map((m) => (
            <tr key={m.key}>
              <td>{m.label}<div className="admin-markup-key">{m.key}</div></td>
              <td>{m.provider}</td>
              <td>{m.available ? <span className="pos">可用</span> : <span className="neg">未配置密钥</span>}</td>
              <td className="admin-markup-pricing">{m.pricing_summary ?? "—"}</td>
              <td>
                <input
                  className="admin-markup-input"
                  type="number"
                  step="0.05"
                  min="0.01"
                  value={edits[m.key] ?? ""}
                  onChange={(e) => setEdits((prev) => ({ ...prev, [m.key]: e.target.value }))}
                />
              </td>
              <td>{m.is_custom ? "自定义" : <span className="admin-markup-default-tag">默认</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="admin-markup-actions">
        {msg && <span className={msg.kind === "ok" ? "admin-markup-ok" : "admin-markup-err"}>{msg.text}</span>}
        <button type="button" className="admin-save-btn" onClick={() => void save()} disabled={saving}>
          {saving ? "保存中…" : "保存倍率"}
        </button>
      </div>
    </section>
  );
}

type BillProduct = { product_name: string; product_code: string; amount: number; is_llm: boolean };
type BillOverview = {
  configured: boolean;
  billing_cycle: string | null;
  items: BillProduct[];
  llm_total: number;
  infra_total: number;
  total: number;
  currency: string | null;
  gross_total: number;
  coupon_deducted: number;
  prepaid_card_deducted: number;
  payment_total: number;
  posted: boolean;
  posted_infra_cny: number;
  estimated_llm_cogs_cny: number;
  llm_actual_cny: number;
  llm_deviation_cny: number;
  llm_deviation_pct: number | null;
  error: string | null;
  note: string | null;
};

function defaultCycle(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function BillView() {
  const [cycle, setCycle] = useState(defaultCycle());
  const [data, setData] = useState<BillOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [posting, setPosting] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = async () => {
    setBusy(true);
    setMsg(null);
    const r = await apiFetch(`/api/admin/billing/aliyun-bill?cycle=${encodeURIComponent(cycle)}`);
    if (r.ok) setData((await r.json()) as BillOverview);
    else setMsg({ kind: "err", text: "拉取账单失败" });
    setBusy(false);
  };

  const post = async () => {
    setPosting(true);
    setMsg(null);
    const r = await apiFetch("/api/admin/billing/aliyun-bill/post", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ billing_cycle: cycle, vendor: "aliyun" }),
    });
    setPosting(false);
    if (r.ok) {
      const body = (await r.json()) as { message: string };
      setMsg({ kind: "ok", text: body.message });
      await load();
    } else {
      let detail = "入账失败";
      try {
        const body = (await r.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // ignore
      }
      setMsg({ kind: "err", text: detail });
    }
  };

  return (
    <section className="admin-table-wrap">
      <div className="admin-markup-head">
        <h3>成本账单（阿里云）</h3>
        <p className="admin-markup-tip">
          按账期拉取阿里云账单总览。百炼等 LLM 已按次计入算力成本（COGS），此处仅把<strong>服务器等基础设施</strong>部分作为期间费用入账（借 6002 / 贷 1102），避免重复计算。
        </p>
        <div className="admin-topup-row">
          <label>账期</label>
          <input type="text" placeholder="YYYY-MM" value={cycle} onChange={(e) => setCycle(e.target.value)} />
          <button type="button" className="admin-save-btn" onClick={() => void load()} disabled={busy}>
            {busy ? "拉取中…" : "拉取账单"}
          </button>
        </div>
        {msg && <p className={msg.kind === "ok" ? "admin-markup-ok" : "admin-markup-err"}>{msg.text}</p>}
      </div>

      {data && !data.configured && <p className="admin-balance-note">{data.note ?? "未配置阿里云费用 AccessKey"}</p>}
      {data && data.error && <p className="admin-markup-err">{data.error}</p>}

      {data && data.configured && !data.error && (
        <>
          <div className="admin-cards" style={{ marginBottom: 14 }}>
            <div className="admin-card" key="b-llm">
              <span className="admin-card-label">百炼 LLM（已按次计入）</span>
              <span className="admin-card-value">{money(data.llm_total)}</span>
              <span className="admin-card-hint">不重复入账</span>
            </div>
            <div className="admin-card is-gift" key="b-infra">
              <span className="admin-card-label">基础设施（服务器等）</span>
              <span className="admin-card-value">{money(data.infra_total)}</span>
              <span className="admin-card-hint">{data.posted ? `已入账 ${money(data.posted_infra_cny)}` : "待入账"}</span>
            </div>
            <div className="admin-card is-total" key="b-total">
              <span className="admin-card-label">账单合计</span>
              <span className="admin-card-value">{money(data.total)}</span>
              <span className="admin-card-hint">{data.billing_cycle ?? cycle}</span>
            </div>
          </div>

          <div className="admin-recon">
            <h4 className="admin-recon-title">百炼成本偏差对账</h4>
            <div className="admin-recon-row">
              <div>
                <span>我们按次估算（COGS）</span>
                <strong>{money(data.estimated_llm_cogs_cny)}</strong>
              </div>
              <div>
                <span>阿里云账单实际（百炼）</span>
                <strong>{money(data.llm_actual_cny)}</strong>
              </div>
              <div>
                <span>偏差（实际 − 估算）</span>
                <strong className={data.llm_deviation_cny >= 0 ? "neg" : "pos"}>
                  {data.llm_deviation_cny >= 0 ? "+" : ""}
                  {money(data.llm_deviation_cny)}
                  {data.llm_deviation_pct !== null ? ` (${(data.llm_deviation_pct * 100).toFixed(1)}%)` : ""}
                </strong>
              </div>
            </div>
            <p className="admin-recon-tip">
              偏差为正＝实际比估算贵（需上调倍率成本基准或核对计价）；为负＝估算偏高或百炼走了免费额度/资源包。账期按 UTC+8 自然月匹配，月初月末可能有少量跨期误差。
            </p>
          </div>

          <div className="admin-recon admin-recon-pay">
            <h4 className="admin-recon-title">付款拆解（{data.billing_cycle ?? cycle}）</h4>
            <div className="admin-recon-row">
              <div><span>原价</span><strong>{money(data.gross_total)}</strong></div>
              <div><span>折扣后应付（成本口径）</span><strong>{money(data.total)}</strong></div>
              <div><span>代金券补贴</span><strong>{money(data.coupon_deducted)}</strong></div>
              <div><span>储值卡抵扣（预付）</span><strong>{money(data.prepaid_card_deducted)}</strong></div>
              <div><span>现金支付（账单期再掏）</span><strong>{money(data.payment_total)}</strong></div>
            </div>
            <p className="admin-recon-tip">
              预付费模式下现金在「云账户充值」时已支付，账单期现金支付通常≈0、主要由储值卡抵扣，因此成本取「折扣后应付」。代金券补贴部分未花你的钱。
            </p>
          </div>

          <table className="admin-table">
            <thead>
              <tr>
                <th>产品</th>
                <th>产品码</th>
                <th>分类</th>
                <th>金额¥</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => (
                <tr key={it.product_code + it.product_name}>
                  <td>{it.product_name}</td>
                  <td>{it.product_code || "-"}</td>
                  <td>{it.is_llm ? <span className="admin-markup-default-tag">LLM</span> : <span className="pos">基础设施</span>}</td>
                  <td>{it.amount.toFixed(2)}</td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr><td colSpan={4} className="admin-balance-note">该账期暂无账单数据</td></tr>
              )}
            </tbody>
          </table>

          <div className="admin-markup-actions">
            <button
              type="button"
              className="admin-save-btn"
              onClick={() => void post()}
              disabled={posting || data.infra_total <= 0}
            >
              {posting ? "入账中…" : data.posted ? "重新入账（幂等）" : "入账基础设施成本"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

type Fundflow = {
  configured: boolean;
  bill_date: string;
  settlement_cny: number;
  fee_cny: number;
  income_cny: number;
  row_count: number;
  unknown_types: string[];
  posted: boolean;
  error: string | null;
  note: string | null;
};

function yesterday(): string {
  const d = new Date(Date.now() - 24 * 3600 * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function WechatView() {
  const [date, setDate] = useState(yesterday());
  const [data, setData] = useState<Fundflow | null>(null);
  const [busy, setBusy] = useState(false);
  const [posting, setPosting] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // 手动结算
  const [settleAmount, setSettleAmount] = useState("");
  const [settleFee, setSettleFee] = useState("");
  const [settleMemo, setSettleMemo] = useState("");
  const [settleBusy, setSettleBusy] = useState(false);
  const [settleMsg, setSettleMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = async () => {
    setBusy(true);
    setMsg(null);
    const r = await apiFetch(`/api/admin/billing/wechat-fundflow?date=${encodeURIComponent(date)}`);
    if (r.ok) setData((await r.json()) as Fundflow);
    else setMsg({ kind: "err", text: "拉取资金账单失败" });
    setBusy(false);
  };

  const post = async () => {
    setPosting(true);
    setMsg(null);
    const r = await apiFetch("/api/admin/billing/wechat-fundflow/post", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bill_date: date }),
    });
    setPosting(false);
    if (r.ok) {
      const body = (await r.json()) as { message: string };
      setMsg({ kind: "ok", text: body.message });
      await load();
    } else {
      let detail = "入账失败";
      try {
        const body = (await r.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // ignore
      }
      setMsg({ kind: "err", text: detail });
    }
  };

  const submitSettle = async () => {
    const settlement = Number(settleAmount || 0);
    const fee = Number(settleFee || 0);
    if (!(settlement > 0) && !(fee > 0)) {
      setSettleMsg({ kind: "err", text: "结算金额与手续费不能都为 0" });
      return;
    }
    setSettleBusy(true);
    setSettleMsg(null);
    const r = await apiFetch("/api/admin/billing/wechat-settlement", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settlement_cny: settlement, fee_cny: fee, memo: settleMemo || null }),
    });
    setSettleBusy(false);
    if (r.ok) {
      setSettleAmount("");
      setSettleFee("");
      setSettleMemo("");
      setSettleMsg({ kind: "ok", text: "已记账，应收第三方支付已冲减" });
    } else {
      let detail = "记账失败";
      try {
        const body = (await r.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // ignore
      }
      setSettleMsg({ kind: "err", text: detail });
    }
  };

  return (
    <section className="admin-table-wrap">
      <div className="admin-markup-head">
        <h3>微信资金对账</h3>
        <p className="admin-markup-tip">
          微信支付收款先记<strong>应收第三方支付（1002）</strong>，平台周期结算到银行后再冲减。下方按<strong>资金账单（自然日）</strong>
          拉取结算与手续费，确认后入账：借 现金(1001) + 手续费(6603) / 贷 应收第三方(1002)。当日账单一般次日才可下载。
        </p>
        <div className="admin-topup-row">
          <label>账单日期</label>
          <input type="text" placeholder="YYYY-MM-DD" value={date} onChange={(e) => setDate(e.target.value)} />
          <button type="button" className="admin-save-btn" onClick={() => void load()} disabled={busy}>
            {busy ? "拉取中…" : "拉取账单"}
          </button>
        </div>
        {msg && <p className={msg.kind === "ok" ? "admin-markup-ok" : "admin-markup-err"}>{msg.text}</p>}
      </div>

      {data && !data.configured && <p className="admin-balance-note">{data.note ?? "微信支付未配置"}</p>}
      {data && data.error && <p className="admin-markup-err">{data.error}</p>}

      {data && data.configured && !data.error && (
        <>
          <div className="admin-cards" style={{ marginBottom: 14 }}>
            <div className="admin-card" key="w-settle">
              <span className="admin-card-label">结算到银行</span>
              <span className="admin-card-value">{money(data.settlement_cny)}</span>
              <span className="admin-card-hint">借 1001 / 贷 1002</span>
            </div>
            <div className="admin-card is-gift" key="w-fee">
              <span className="admin-card-label">手续费</span>
              <span className="admin-card-value">{money(data.fee_cny)}</span>
              <span className="admin-card-hint">借 6603 / 贷 1002</span>
            </div>
            <div className="admin-card" key="w-income">
              <span className="admin-card-label">当日交易收入</span>
              <span className="admin-card-value">{money(data.income_cny)}</span>
              <span className="admin-card-hint">仅供参考核对</span>
            </div>
            <div className="admin-card is-total" key="w-status">
              <span className="admin-card-label">入账状态</span>
              <span className="admin-card-value">{data.posted ? "已入账" : "待入账"}</span>
              <span className="admin-card-hint">{data.row_count} 条流水</span>
            </div>
          </div>

          {data.unknown_types.length > 0 && (
            <p className="admin-recon-tip">
              未归类业务类型：{data.unknown_types.join("、")}（不影响结算/手续费入账，如涉及资金请人工核对）。
            </p>
          )}

          <div className="admin-markup-actions">
            <button
              type="button"
              className="admin-save-btn"
              onClick={() => void post()}
              disabled={posting || (data.settlement_cny <= 0 && data.fee_cny <= 0)}
            >
              {posting ? "入账中…" : data.posted ? "重新入账（幂等）" : "按账单入账结算/手续费"}
            </button>
          </div>
        </>
      )}

      <div className="admin-recon admin-recon-pay" style={{ marginTop: 18 }}>
        <h4 className="admin-recon-title">手动结算入账（兜底）</h4>
        <div className="admin-topup-row">
          <input
            type="number"
            step="0.01"
            min="0"
            placeholder="结算到账金额（元）"
            value={settleAmount}
            onChange={(e) => setSettleAmount(e.target.value)}
          />
          <input
            type="number"
            step="0.01"
            min="0"
            placeholder="手续费（元，可空）"
            value={settleFee}
            onChange={(e) => setSettleFee(e.target.value)}
          />
          <input
            type="text"
            placeholder="备注（如：6/18 微信结算）"
            value={settleMemo}
            onChange={(e) => setSettleMemo(e.target.value)}
          />
          <button type="button" className="admin-save-btn" onClick={() => void submitSettle()} disabled={settleBusy}>
            {settleBusy ? "记账中…" : "记账"}
          </button>
        </div>
        {settleMsg && (
          <span className={settleMsg.kind === "ok" ? "admin-markup-ok" : "admin-markup-err"}>{settleMsg.text}</span>
        )}
        <p className="admin-recon-tip">
          当无法自动拉取账单时手动登记一笔结算到账。借 现金(1001)+手续费(6603) / 贷 应收第三方(1002)。每次为独立记录，请勿与自动入账重复。
        </p>
      </div>
    </section>
  );
}

function tabLabel(tab: Tab): string {
  return {
    overview: "财务总览",
    transactions: "积分流水",
    ledger: "记账凭证",
    users: "用户对账",
    markups: "模型倍率",
    bill: "成本账单",
    wechat: "微信资金",
  }[tab];
}

function accountTypeLabel(type: string): string {
  return (
    { asset: "资产", liability: "负债", equity: "权益", revenue: "收入", expense: "成本费用" }[type] ?? type
  );
}
