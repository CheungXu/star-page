import Link from "next/link";
import { LEGAL_CONTACT_EMAIL, LEGAL_DOCS } from "../legal/docs";
import { SITE_ICP_FILING } from "../site-config";

const REPORT_MAILTO = `mailto:${LEGAL_CONTACT_EMAIL}?subject=${encodeURIComponent("星页违规内容举报")}`;

/** 首页底部：使用即同意提示（覆盖未登录试用用户）+ 举报 + 协议链接 + 备案号，仅 idle 首屏展示 */
export function SiteFooter() {
  return (
    <footer className="site-footer" aria-label="网站协议与备案信息">
      <p className="site-footer-consent">
        使用星页即表示你同意<Link href={LEGAL_DOCS.terms.href}>《用户服务协议》</Link>和
        <Link href={LEGAL_DOCS.privacy.href}>《隐私政策》</Link>。
      </p>
      <p className="site-footer-links">
        <Link href={LEGAL_DOCS.terms.href}>用户服务协议</Link>
        <span aria-hidden="true">·</span>
        <Link href={LEGAL_DOCS.privacy.href}>隐私政策</Link>
        <span aria-hidden="true">·</span>
        <Link href={LEGAL_DOCS["recharge-terms"].href}>积分充值服务协议</Link>
        <span aria-hidden="true">·</span>
        <a href={REPORT_MAILTO}>违法和不良信息举报</a>
        <span aria-hidden="true">·</span>
        <a href={SITE_ICP_FILING.url} target="_blank" rel="noopener noreferrer">
          {SITE_ICP_FILING.number}
        </a>
      </p>
    </footer>
  );
}
