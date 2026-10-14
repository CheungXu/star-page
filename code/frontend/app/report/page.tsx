"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { LEGAL_CONTACT_EMAIL } from "../legal/docs";
import { apiFetch, readErrorMessage } from "../lib/api";

type ReportTarget = {
  page_id: string;
  title: string;
  page_url: string;
  categories: Record<string, string>;
};

/** 公开页面浮标「举报」的落点。生成页 CSP 禁止直接请求接口，所以在平台页提交。 */
function ReportForm() {
  const params = useSearchParams();
  const pageId = params.get("page") ?? "";
  const slug = params.get("slug") ?? "";
  const [target, setTarget] = useState<ReportTarget | null>(null);
  const [fetchError, setFetchError] = useState("");
  const loadError = pageId ? fetchError : "缺少被举报的页面信息，请从页面右下角的「举报」进入。";
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [contact, setContact] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!pageId) return;
    void (async () => {
      const response = await apiFetch(`/api/reports/target?page=${encodeURIComponent(pageId)}`);
      if (response.ok) setTarget((await response.json()) as ReportTarget);
      else setFetchError(await readErrorMessage(response, "页面不存在或已删除"));
    })();
  }, [pageId]);

  async function submit() {
    if (!category) {
      setError("请选择举报类型");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const response = await apiFetch("/api/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page_id: pageId, slug: slug || null, category, description, contact }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "提交失败，请稍后再试"));
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请稍后再试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="legal-page report-page">
      <header className="legal-header">
        <Link className="legal-back" href="/">← 返回首页</Link>
      </header>
      <article className="legal-article">
        <h1>举报违法和不良信息</h1>
        {loadError && <p className="report-error">{loadError}</p>}
        {done ? (
          <div className="report-done">
            <p><strong>举报已收到，感谢你的反馈。</strong></p>
            <p>我们会在 15 个工作日内完成审核，违规页面会被下架。如需补充材料，请发邮件至 {LEGAL_CONTACT_EMAIL}。</p>
          </div>
        ) : (
          target && (
            <>
              <p>
                被举报页面：<a href={target.page_url} target="_blank" rel="noopener noreferrer">{target.title}</a>
              </p>
              <fieldset className="report-categories">
                <legend>举报类型</legend>
                {Object.entries(target.categories).map(([key, label]) => (
                  <label key={key}>
                    <input type="radio" name="category" value={key} checked={category === key} onChange={() => setCategory(key)} />
                    {label}
                  </label>
                ))}
              </fieldset>
              <label className="report-field">
                情况说明（选填）
                <textarea
                  value={description}
                  maxLength={2000}
                  rows={4}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="请描述页面中的违规内容，便于我们快速核实"
                />
              </label>
              <label className="report-field">
                联系方式（选填）
                <input
                  value={contact}
                  maxLength={128}
                  onChange={(event) => setContact(event.target.value)}
                  placeholder="手机号或邮箱，仅用于反馈处理结果"
                />
              </label>
              {error && <p className="report-error">{error}</p>}
              <button className="report-submit" type="button" disabled={submitting} onClick={() => void submit()}>
                {submitting ? "提交中…" : "提交举报"}
              </button>
              <p className="report-tip">也可以发送邮件至 {LEGAL_CONTACT_EMAIL} 举报。恶意举报将被限制使用。</p>
            </>
          )
        )}
      </article>
    </main>
  );
}

export default function ReportPage() {
  return (
    <Suspense fallback={null}>
      <ReportForm />
    </Suspense>
  );
}
