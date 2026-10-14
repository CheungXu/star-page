"use client";

import { useEffect, useState } from "react";
import { LEGAL_DOCS } from "../legal/docs";
import { apiFetch, readErrorMessage } from "../lib/api";

type PrivacyPreferences = {
  allow_training: boolean;
  is_anonymous: boolean;
  training_discount_rate: number;
};

const CN_DIGITS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"];

function discountLabel(rate: number): string {
  const tenths = Math.round(rate * 100) / 10;
  return Number.isInteger(tenths) ? `${CN_DIGITS[tenths]}折` : `${tenths}折`;
}

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

  const discount = prefs ? discountLabel(prefs.training_discount_rate) : "";

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
            <small>
              开启时，每次生成的页面经去标识化后用于改进模型，并享受生成{discount || "授权"}优惠
              {prefs?.is_anonymous ? "（登录后生效）" : ""}。
              关闭后按标准价计费，你之后的数据不再用于训练。
            </small>
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
          开关只影响之后的生成。已在授权状态下生成的页面，会在生成时提取一份去掉全部文字、图片和链接的匿名结构样本，关闭后不撤回；
          包含个人信息的原文在关闭后停止用于训练。详见
          <a href={LEGAL_DOCS.privacy.href} target="_blank" rel="noopener noreferrer">《隐私政策》</a>。
        </p>
        {error && <div className="auth-error">{error}</div>}
      </section>
    </div>
  );
}
