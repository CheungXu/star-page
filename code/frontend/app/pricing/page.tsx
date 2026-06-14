"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  type BillingAccount,
  type CreditPackage,
  type CreditTransaction,
  createRecharge,
  creditsToYuan,
  fetchAccount,
  fetchPackages,
  fetchTransactions,
  formatCredits,
  mockPay,
  packageDiscountLabel,
  txnTypeLabel,
} from "../lib/billing";

export default function PricingPage() {
  const [account, setAccount] = useState<BillingAccount | null>(null);
  const [packages, setPackages] = useState<CreditPackage[]>([]);
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [payingKey, setPayingKey] = useState<string | null>(null);
  const [toast, setToast] = useState("");

  async function refresh() {
    const [acc, pkgs, txns] = await Promise.all([fetchAccount(), fetchPackages(), fetchTransactions()]);
    setAccount(acc);
    setPackages(pkgs);
    setTransactions(txns);
  }

  useEffect(() => {
    void (async () => {
      await refresh();
      setLoading(false);
    })();
  }, []);

  function showToast(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(""), 2800);
  }

  async function handleBuy(pkg: CreditPackage) {
    if (account?.is_anonymous) {
      showToast("请先返回首页登录后再充值");
      return;
    }
    setPayingKey(pkg.key);
    try {
      const order = await createRecharge(pkg.key);
      // 当前为 mock 支付：建单后直接走 mock 回调入账；接入微信/支付宝后改为跳转支付。
      await mockPay(order.order_id);
      await refresh();
      showToast(`充值成功，到账 ${formatCredits(pkg.total_credits)} 积分`);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "充值失败");
    } finally {
      setPayingKey(null);
    }
  }

  const isAnon = !account || account.is_anonymous;

  return (
    <main className="pricing-page">
      <header className="pricing-header">
        <Link className="pricing-back" href="/">← 返回首页</Link>
        <div className="pricing-balance">
          {isAnon ? (
            <span>未登录 · 登录后赠送 1000 积分</span>
          ) : (
            <span>
              当前积分 <strong>{formatCredits(account!.total_balance)}</strong>（≈¥
              {creditsToYuan(account!.total_balance)}）
            </span>
          )}
        </div>
      </header>

      <section className="pricing-hero">
        <h1>积分充值</h1>
        <p>1 元 = 100 积分，按每次生成的实际消耗扣费。充值越多，赠送越多。</p>
        {isAnon && <p className="pricing-tip">充值需要登录账户，请先回到首页完成登录 / 注册。</p>}
      </section>

      {loading ? (
        <p className="pricing-loading">正在加载套餐…</p>
      ) : (
        <section className="pricing-grid">
          {packages.map((pkg, index) => {
            const discount = packageDiscountLabel(pkg);
            const recommended = index === 2;
            return (
              <article key={pkg.key} className={`pricing-card ${recommended ? "is-recommended" : ""}`}>
                {recommended && <span className="pricing-badge">热门</span>}
                <h2>{pkg.title}</h2>
                <div className="pricing-amount">
                  <span className="pricing-currency">¥</span>
                  <span className="pricing-value">{pkg.amount_cny.toFixed(0)}</span>
                </div>
                <ul className="pricing-detail">
                  <li>
                    基础积分 <strong>{formatCredits(pkg.base_credits)}</strong>
                  </li>
                  {pkg.bonus_credits > 0 && (
                    <li className="pricing-bonus">
                      额外赠送 <strong>{formatCredits(pkg.bonus_credits)}</strong>
                    </li>
                  )}
                  <li className="pricing-total">
                    共计 <strong>{formatCredits(pkg.total_credits)}</strong> 积分
                  </li>
                </ul>
                {discount && <span className="pricing-discount">{discount}</span>}
                <button
                  type="button"
                  className="pricing-buy"
                  disabled={payingKey !== null || isAnon}
                  onClick={() => void handleBuy(pkg)}
                >
                  {payingKey === pkg.key ? "处理中…" : isAnon ? "登录后充值" : "立即充值"}
                </button>
              </article>
            );
          })}
        </section>
      )}

      <section className="pricing-note">
        <p>当前支付为开发期 Mock 流程，正式上线后将接入微信支付与支付宝。</p>
      </section>

      {!isAnon && transactions.length > 0 && (
        <section className="pricing-history">
          <h3>最近积分流水</h3>
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>类型</th>
                <th>变动</th>
                <th>余额</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {transactions.slice(0, 20).map((txn) => (
                <tr key={txn.id}>
                  <td>{new Date(txn.created_at).toLocaleString("zh-CN")}</td>
                  <td>{txnTypeLabel(txn.type)}</td>
                  <td className={txn.credits_delta >= 0 ? "pos" : "neg"}>
                    {txn.credits_delta >= 0 ? "+" : ""}
                    {formatCredits(txn.credits_delta)}
                  </td>
                  <td>{formatCredits(txn.balance_after)}</td>
                  <td>{txn.memo ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {toast && <div className="app-toast">{toast}</div>}
    </main>
  );
}

