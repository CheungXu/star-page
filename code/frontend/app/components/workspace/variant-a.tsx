"use client";

import { useEffect, useState } from "react";
import { StatusMark } from "./bits";
import { useArrowModelNav, useWorkspace } from "./context";
import {
  averageProgress,
  batchElapsed,
  comfortLine,
  estimateProgress,
  formatClock,
  formatDuration,
  formatTokenCount,
  formatTokens,
  isSettled,
  modelAccent,
  outputTokenCount,
  stageLabel,
  sumCredits,
  sumOutputTokens,
} from "./progress-utils";

export function VariantAProgress() {
  const { runs, activeModelKey, selectModel, appliedSkill, elapsed } = useWorkspace();
  const [comfort, setComfort] = useState(0);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const generating = runs.some((run) => run.status === "thinking" || run.status === "creating");

  useEffect(() => {
    if (!generating) return;
    const timer = window.setInterval(() => setComfort((value) => value + 1), 6000);
    return () => window.clearInterval(timer);
  }, [generating]);

  const done = runs.filter((run) => run.status === "completed").length;
  const failed = runs.filter((run) => run.status === "failed").length;
  const settled = runs.length > 0 && runs.every(isSettled);
  const progress = settled ? 100 : averageProgress(runs, elapsed);
  const clock = batchElapsed(runs, elapsed);
  const credits = sumCredits(runs);
  const tokens = sumOutputTokens(runs);
  const headline = generating
    ? "正在生成"
    : done === 0 && failed > 0
      ? "生成失败"
      : failed > 0
        ? `已完成 · ${failed} 个失败`
        : settled
          ? "全部完成"
          : "生成结果";

  return (
    <div className="chat-message assistant-message va-board">
      <div className="va-head">
        <div>
          <strong>{headline}</strong>
          <span>
            {done}/{runs.length} 已完成
            {clock !== null ? ` · ${settled ? formatDuration(clock) : formatClock(clock)}` : ""}
          </span>
        </div>
        {appliedSkill && <span className="applied-skill-badge">技能 · {appliedSkill.name}</span>}
      </div>
      <div className="va-total" role="progressbar" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100} aria-label="本轮总进度">
        <span style={{ width: `${progress}%` }} />
      </div>
      {generating && <p className="va-comfort">{comfortLine(comfort)}</p>}
      <ul className="va-rows">
        {runs.map((run) => {
          const value = estimateProgress(run, elapsed(run));
          const count = outputTokenCount(run);
          const time = elapsed(run);
          const open = openKey === run.modelKey;
          const stage = stageLabel(run);
          const detail = count > 0 && run.status !== "failed" ? `${stage} · ${formatTokens(count)}` : stage;
          return (
            <li key={run.taskId || run.modelKey} className={`va-row ${run.modelKey === activeModelKey ? "is-active" : ""} is-${run.status}`}>
              <button type="button" className="va-row-main" onClick={() => selectModel(run.modelKey)}>
                <StatusMark status={run.status} />
                <span className="va-accent" style={{ background: modelAccent(run.modelKey) }} />
                <span className="va-name" title={run.modelLabel}>{run.modelLabel}</span>
                <span className="va-stage">{detail}</span>
                <span className="va-time">
                  {run.status === "completed" && run.usageSummary?.creditsCharged
                    ? `${run.usageSummary.creditsCharged} 积分`
                    : time !== null && run.status !== "idle"
                      ? formatDuration(time)
                      : ""}
                </span>
              </button>
              <button
                type="button"
                className="va-think"
                aria-expanded={open}
                title="查看模型的思考过程"
                onClick={() => setOpenKey(open ? null : run.modelKey)}
              >
                {open ? "收起" : "思考"}
              </button>
              <span className="va-bar" aria-hidden="true"><span style={{ width: `${value}%` }} /></span>
              {run.status === "failed" && run.errorMessage && <p className="va-error">{run.errorMessage}</p>}
              {open && <pre className="va-reasoning">{run.reasoning || "模型还没有写出思考内容。"}</pre>}
            </li>
          );
        })}
      </ul>
      <div
        className="va-foot"
        title={credits.hasBilling ? `输出 ${formatTokenCount(tokens)} tokens` : undefined}
      >
        {settled ? (
          <>
            <span>输出 {formatTokens(tokens)} tokens</span>
            {credits.hasBilling && (
              <span>
                花费 {credits.charged} 积分
                {credits.discount > 0 && <em>优惠 -{credits.discount}</em>}
              </span>
            )}
            {clock !== null && <span>用时 {formatDuration(clock)}</span>}
          </>
        ) : (
          <span>已写 {formatTokens(tokens)} tokens · 完成后结算</span>
        )}
      </div>
    </div>
  );
}

export function VariantAResults() {
  const { runs, activeModelKey, selectModel, isUnseen, renderPreview } = useWorkspace();
  const [mode, setMode] = useState<"focus" | "overview">("focus");
  const many = runs.length >= 3;
  useArrowModelNav(many && mode === "focus");
  const done = runs.filter((run) => run.status === "completed").length;
  const active = runs.find((run) => run.modelKey === activeModelKey) ?? runs[0];

  return (
    <article className="panel preview-panel workspace-preview va-results">
      <div className="panel-heading preview-heading">
        <h2>结果预览{many ? ` · ${runs.length} 个版本 · ${done} 已完成` : ""}</h2>
        {!many && runs.length > 1 && <span className="preview-count">{runs.length} 个版本</span>}
        {many && (
          <div className="va-seg" role="group" aria-label="结果查看方式">
            <button type="button" aria-pressed={mode === "focus"} onClick={() => setMode("focus")}>聚焦</button>
            <button type="button" aria-pressed={mode === "overview"} onClick={() => setMode("overview")}>总览</button>
          </div>
        )}
      </div>
      {!many && (
        <div className="preview-grid" style={{ gridTemplateColumns: `repeat(${Math.max(runs.length, 1)}, minmax(0, 1fr))` }}>
          {runs.map((run) => (
            <div key={run.taskId || run.modelKey} className="va-pair">{renderPreview(run, { presentation: "default" })}</div>
          ))}
        </div>
      )}
      {many && mode === "focus" && (
        <>
          <div className="va-switcher" role="tablist" aria-label="选择模型结果">
            {runs.map((run) => (
              <button
                key={run.taskId || run.modelKey}
                type="button"
                role="tab"
                aria-selected={run.modelKey === activeModelKey}
                className={`va-tab is-${run.status} ${run.modelKey === activeModelKey ? "is-active" : ""}`}
                onClick={() => selectModel(run.modelKey)}
              >
                <span className="va-accent" style={{ background: modelAccent(run.modelKey) }} />
                <span>{run.modelLabel}</span>
                <StatusMark status={run.status} />
                {isUnseen(run.modelKey) && <i className="va-unseen" aria-label="新完成" />}
              </button>
            ))}
            <span className="va-keyhint">← → 切换</span>
          </div>
          <div className="va-focus">{active && renderPreview(active, { presentation: "default" })}</div>
        </>
      )}
      {many && mode === "overview" && (
        <div className="va-overview">
          {runs.map((run) => (
            <div key={run.taskId || run.modelKey} className={`va-overview-card ${run.modelKey === activeModelKey ? "is-active" : ""}`}>
              {renderPreview(run, { presentation: "thumb" })}
              <button
                type="button"
                className="va-hit"
                aria-label={`查看 ${run.modelLabel}`}
                onClick={() => {
                  selectModel(run.modelKey);
                  setMode("focus");
                }}
              />
              {isUnseen(run.modelKey) && <i className="va-unseen" aria-hidden="true" />}
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
