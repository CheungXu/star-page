"use client";

import { LEGAL_DOCS } from "../legal/docs";

/** 老用户的一次性协议更新告知：不拦截操作，点「知道了」或继续使用都记为已告知。 */
export function PolicyNoticeBar({ onAcknowledge, onOpenSettings }: { onAcknowledge: () => void; onOpenSettings: () => void }) {
  return (
    <div className="policy-notice-bar" role="status">
      <p>
        我们更新了<a href={LEGAL_DOCS.terms.href} target="_blank" rel="noopener noreferrer">《用户服务协议》</a>和
        <a href={LEGAL_DOCS.privacy.href} target="_blank" rel="noopener noreferrer">《隐私政策》</a>
        ：生成内容将在去标识化后用于改进模型，授权用户生成享受九折。你可以随时在
        <button type="button" className="policy-notice-link" onClick={onOpenSettings}>设置</button>
        中关闭。
      </p>
      <button type="button" className="policy-notice-ok" onClick={onAcknowledge}>
        知道了
      </button>
    </div>
  );
}
