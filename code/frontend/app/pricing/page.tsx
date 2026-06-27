"use client";

import Link from "next/link";
import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";
import {
  type BillingAccount,
  type CreditPackage,
  type CreditTransaction,
  type RechargeOrder,
  createRecharge,
  creditsToYuan,
  fetchAccount,
  fetchOrderStatus,
  fetchPackages,
  fetchTransactions,
  formatCredits,
  mockPay,
  packageDiscountLabel,
  txnTypeLabel,
} from "../lib/billing";

type WechatPayState = {
  order: RechargeOrder;
  pkg: CreditPackage;
  qrDataUrl: string;
  status: "pending" | "paid";
};

export default function PricingPage() {
  const [account, setAccount] = useState<BillingAccount | null>(null);
  const [packages, setPackages] = useState<CreditPackage[]>([]);
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [payingKey, setPayingKey] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [wechatPay, setWechatPay] = useState<WechatPayState | null>(null);
  const pollTimer = useRef<number | null>(null);

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

  // 微信扫码后轮询订单状态（后端含查单兜底），支付成功即刷新积分。
  useEffect(() => {
    if (!wechatPay || wechatPay.status !== "pending") return;
    const orderId = wechatPay.order.order_id;
    pollTimer.current = window.setInterval(async () => {
      const status = await fetchOrderStatus(orderId);
      if (status?.status === "paid") {
        if (pollTimer.current) window.clearInterval(pollTimer.current);
        setWechatPay((prev) => (prev ? { ...prev, status: "paid" } : prev));
        await refresh();
        showToast(`充值成功，到账 ${formatCredits(wechatPay.pkg.total_credits)} 积分`);
        window.setTimeout(() => setWechatPay(null), 1800);
      }
    }, 2500);
    return () => {
      if (pollTimer.current) window.clearInterval(pollTimer.current);
    };
  }, [wechatPay]);

  function showToast(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(""), 2800);
  }

  function closeWechatPay() {
    if (pollTimer.current) window.clearInterval(pollTimer.current);
    setWechatPay(null);
  }

  async function handleBuy(pkg: CreditPackage) {
    if (account?.is_anonymous) {
      showToast("请先返回首页登录后再充值");
      return;
    }
    setPayingKey(pkg.key);
    try {
      const order = await createRecharge(pkg.key);
      if (order.code_url) {
        // 微信 Native：把 code_url 渲染为二维码，弹窗展示并轮询到账。
        const qrDataUrl = await QRCode.toDataURL(order.code_url, { width: 240, margin: 1 });
        setWechatPay({ order, pkg, qrDataUrl, status: "pending" });
      } else if (order.pay_url) {
        // 开发期 mock 支付：建单后直接走 mock 回调入账。
        await mockPay(order.order_id);
        await refresh();
        showToast(`充值成功，到账 ${formatCredits(pkg.total_credits)} 积分`);
      } else {
        showToast("暂不可用的支付方式");
      }
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
        <p>支持微信扫码支付，1 元 = 100 积分，充值即时到账。</p>
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

      {wechatPay && (
        <div className="pay-modal-mask" onClick={closeWechatPay}>
          <div className="pay-modal" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="pay-modal-close" onClick={closeWechatPay} aria-label="关闭">
              ×
            </button>
            <h3>微信扫码支付</h3>
            <p className="pay-modal-pkg">
              {wechatPay.pkg.title} · ¥{wechatPay.pkg.amount_cny.toFixed(2)} · 到账{" "}
              {formatCredits(wechatPay.pkg.total_credits)} 积分
            </p>
            {wechatPay.status === "paid" ? (
              <div className="pay-modal-paid">
                <span className="pay-modal-check">✓</span>
                <p>支付成功，积分已到账</p>
              </div>
            ) : (
              <>
                <div className="pay-modal-qr">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={wechatPay.qrDataUrl} alt="微信支付二维码" width={240} height={240} />
                </div>
                <p className="pay-modal-tip">请使用微信「扫一扫」扫码支付，支付完成后页面会自动到账。</p>
              </>
            )}
          </div>
        </div>
      )}

      {toast && <div className="app-toast">{toast}</div>}
    </main>
  );
}

