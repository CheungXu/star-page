"use client";

import { useEffect, useState } from "react";
import { LEGAL_DOCS } from "../legal/docs";
import { apiFetch, readErrorMessage } from "../lib/api";

type PrivacyPreferences = {
  allow_training: boolean;
  is_anonymous: boolean;
};

/** 设置 → 数据与隐私：训练授权开关。登录用户和匿名访客共用，开关只影响之后的生成。 */
export function DataPrivacyModal({ onClose, onChanged }: { onClose: () => void; onChanged?: (allow: boolean) => void }) {
  const [prefs, setPrefs] = useState<PrivacyPreferences | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      const response = await apiFetch("/api/privacy/preferences");
      if (response.ok) setPrefs((await response.json()) as PrivacyPreferences);
      else setError("设置读取失败，请稍后再试");
    })();
  }, []);

  async function toggle(next: boolean) {
    setSaving(true);
    setError("");
    try {
      const response = await apiFetch("/api/privacy/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow_training: next }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "保存失败"));
      const data = (await response.json()) as PrivacyPreferences;
      setPrefs(data);
      onChanged?.(data.allow_training);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="auth-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="auth-modal privacy-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="privacy-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button className="auth-modal-close" type="button" onClick={onClose} aria-label="关闭设置">
          ×
        </button>
        <h2 id="privacy-modal-title">设置</h2>
        <h3 className="privacy-section-title">数据与隐私</h3>

        <label className="privacy-row">
          <span className="privacy-row-text">
            <strong>允许用我的内容改进模型</strong>
            <small>开启时享有生成积分优惠；关闭后按标准价计费，后续内容不再用于训练。</small>
          </span>
          <input
            className="privacy-switch"
            type="checkbox"
            role="switch"
            checked={prefs?.allow_training ?? true}
            disabled={!prefs || saving}
            onChange={(event) => void toggle(event.target.checked)}
          />
        </label>

        <p className="privacy-note">
          仅影响后续生成。详见
          <a href={LEGAL_DOCS.privacy.href} target="_blank" rel="noopener noreferrer">《隐私政策》</a>。
        </p>
        {error && <div className="auth-error">{error}</div>}
      </section>
    </div>
  );
}
