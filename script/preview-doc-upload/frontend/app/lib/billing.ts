// 计费相关的前端类型、数据获取与展示工具。

import { apiFetch, readErrorMessage } from "./api";

export type BillingAccount = {
  is_anonymous: boolean;
  paid_balance: number;
  gift_balance: number;
  total_balance: number;
  free_generations_used: number;
  free_generations_limit: number;
  free_generations_remaining: number;
  signup_bonus_granted: boolean;
};

export type CreditPackage = {
  key: string;
  title: string;
  amount_cny: number;
  base_credits: number;
  bonus_credits: number;
  total_credits: number;
};

export type CreditTransaction = {
  id: string;
  type: string;
  credits_delta: number;
  balance_after: number;
  model_key: string | null;
  revenue_cny: number | null;
  raw_cost_cny: number | null;
  memo: string | null;
  created_at: string;
};

export type RechargeOrder = {
  order_id: string;
  package_key: string;
  amount_cny: number;
  base_credits: number;
  bonus_credits: number;
  status: string;
  payment_provider: string;
  pay_url: string | null;
  code_url: string | null;
};

export type RechargeStatus = {
  order_id: string;
  status: string;
  payment_provider: string;
  amount_cny: number;
  total_credits: number;
};

export async function fetchAccount(): Promise<BillingAccount | null> {
  const res = await apiFetch("/api/billing/account");
  if (!res.ok) return null;
  return (await res.json()) as BillingAccount;
}

export async function fetchPackages(): Promise<CreditPackage[]> {
  const res = await apiFetch("/api/billing/packages");
  if (!res.ok) return [];
  return (await res.json()) as CreditPackage[];
}

export async function fetchTransactions(): Promise<CreditTransaction[]> {
  const res = await apiFetch("/api/billing/transactions");
  if (!res.ok) return [];
  return (await res.json()) as CreditTransaction[];
}

export async function createRecharge(packageKey: string, provider?: string): Promise<RechargeOrder> {
  const res = await apiFetch("/api/billing/recharge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ package_key: packageKey, provider: provider ?? null }),
  });
  if (!res.ok) throw new Error(await readErrorMessage(res, "创建订单失败"));
  return (await res.json()) as RechargeOrder;
}

export async function mockPay(orderId: string): Promise<void> {
  const res = await apiFetch(`/api/billing/recharge/${orderId}/mock-pay`, { method: "POST" });
  if (!res.ok) throw new Error(await readErrorMessage(res, "支付失败"));
}

// 查询充值订单状态（后端会对微信单做查单兜底）。
export async function fetchOrderStatus(orderId: string): Promise<RechargeStatus | null> {
  const res = await apiFetch(`/api/billing/recharge/${orderId}`);
  if (!res.ok) return null;
  return (await res.json()) as RechargeStatus;
}

// 积分换算为人民币（元）展示，1 元 = 100 积分。
export function creditsToYuan(credits: number): string {
  return (credits / 100).toFixed(2);
}

export function formatCredits(credits: number): string {
  return credits.toLocaleString("zh-CN");
}

// 积分流水类型的中文标签，供购买页与管理后台共用。
export function txnTypeLabel(type: string): string {
  const map: Record<string, string> = {
    recharge: "充值",
    gift: "赠送",
    consume: "消费",
    refund: "退款",
    adjust: "调整",
    expire: "过期",
  };
  return map[type] ?? type;
}

// 折扣：相对“1 元=100 积分”的基准，套餐赠送越多越优惠。
export function packageDiscountLabel(pkg: CreditPackage): string | null {
  const baseValueCredits = Math.round(pkg.amount_cny * 100);
  if (pkg.total_credits <= baseValueCredits) return null;
  const ratio = baseValueCredits / pkg.total_credits; // 实付/到账
  const discount = Math.round(ratio * 100) / 10; // 折（如 8.7 折）
  return `≈${discount.toFixed(1)}折`;
}
