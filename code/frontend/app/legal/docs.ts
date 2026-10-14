/** 协议版本：与后端 consent_service.POLICY_VERSION 一致，登录勾选时回传，协议有实质修改时一起更新。 */
export const LEGAL_POLICY_VERSION = "2026-10-13";

export const LEGAL_CONTACT_EMAIL = "starspage@xzcqtech.cn";

/** 协议页元信息；正文见 content/legal/{key}.md */
export const LEGAL_DOCS = {
  terms: { href: "/terms", title: "星页 StarPage 用户服务协议", shortTitle: "用户服务协议" },
  privacy: { href: "/privacy", title: "星页 StarPage 隐私政策", shortTitle: "隐私政策" },
  "recharge-terms": { href: "/recharge-terms", title: "星页 StarPage 积分充值服务协议", shortTitle: "积分充值服务协议" },
} as const;

export type LegalDocKey = keyof typeof LEGAL_DOCS;
