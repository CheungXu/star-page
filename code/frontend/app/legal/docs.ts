/** 协议页元信息；正文见 content/legal/{key}.md */
export const LEGAL_DOCS = {
  terms: { href: "/terms", title: "星页 StarPage 用户服务协议", shortTitle: "用户服务协议" },
  privacy: { href: "/privacy", title: "星页 StarPage 隐私政策", shortTitle: "隐私政策" },
  "recharge-terms": { href: "/recharge-terms", title: "星页 StarPage 积分充值服务协议", shortTitle: "积分充值服务协议" },
} as const;

export type LegalDocKey = keyof typeof LEGAL_DOCS;
