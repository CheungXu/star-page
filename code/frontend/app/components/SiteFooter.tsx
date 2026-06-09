import { SITE_ICP_FILING } from "../site-config";

/** 首页底部备案号，仅 idle 首屏展示 */
export function SiteFooter() {
  return (
    <footer className="site-footer" aria-label="网站备案信息">
      <a href={SITE_ICP_FILING.url} target="_blank" rel="noopener noreferrer">
        {SITE_ICP_FILING.number}
      </a>
    </footer>
  );
}
