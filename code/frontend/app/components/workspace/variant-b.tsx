"use client";

import { useEffect, useState } from "react";
import { Ring, StatusMark } from "./bits";
import { useArrowModelNav, useWorkspace } from "./context";
import {
  batchElapsed,
  estimateProgress,
  formatClock,
  formatDuration,
  formatTokens,
  modelAccent,
  outputTokenCount,
  stageLabel,
  sumCredits,
  sumOutputTokens,
} from "./progress-utils";
import type { WorkspaceRun } from "./types";

function liveCount(run: WorkspaceRun): string {
  const tokens = outputTokenCount(run);
  return tokens > 0 ? formatTokens(tokens) : stageLabel(run);
}

function LiveCard({ run }: { run: WorkspaceRun }) {
  const { elapsed, renderPreview } = useWorkspace();
  const value = estimateProgress(run, elapsed(run));
  const tokens = outputTokenCount(run);
  const pending = run.status === "thinking" || run.status === "creating";
  return (
    <div className="vb-live">
      {renderPreview(run, { presentation: "default" })}
      {pending && (
        <div className="vb-live-overlay">
          <Ring value={value} />
          <strong>{liveCount(run)}</strong>
          <span>{tokens > 0 ? `${stageLabel(run)} · tokens` : "还没开始写页面"}</span>
        </div>
      )}
    </div>
  );
}

export function VariantBProgress() {
  const { runs, elapsed, isGenerating } = useWorkspace();
  const [open, setOpen] = useState(false);
  const done = runs.filter((run) => run.status === "completed").length;
  const clock = batchElapsed(runs, elapsed);
  const tokens = sumOutputTokens(runs);
  const clockText = clock === null ? "" : isGenerating ? formatClock(clock) : formatDuration(clock);
  const credits = sumCredits(runs);
  const title = isGenerating ? `${runs.length} 个模型生成中` : `${done}/${runs.length} 已完成`;
  const sub = isGenerating
    ? `已写 ${formatTokens(tokens)} tokens`
    : `${formatTokens(tokens)} tokens${credits.hasBilling ? ` · 花费 ${credits.charged} 积分` : ""}`;

  return (
    <div className="vb-status">
      <button type="button" className="vb-pill" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className={`vb-pill-dot ${isGenerating ? "is-run" : "is-done"}`} />
        <span className="vb-pill-text">
          <strong>
            {title}
            {clockText && <em>{clockText}</em>}
          </strong>
          <span>{sub}</span>
        </span>
        <span className="vb-pill-more">{open ? "收起" : "明细"}</span>
      </button>
      {open && (
        <ul className="vb-mini">
          {runs.map((run) => (
            <li key={run.taskId || run.modelKey}>
              <StatusMark status={run.status} />
              <span>{run.modelLabel}</span>
              <em>{stageLabel(run)}</em>
              <b>{formatTokens(outputTokenCount(run))}</b>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function VariantBResults() {
  const { runs, activeModelKey, selectModel, isUnseen, renderPreview, elapsed } = useWorkspace();
  const [carousel, setCarousel] = useState(false);
  const many = runs.length >= 3;
  useArrowModelNav(many);
  const activeIndex = Math.max(0, runs.findIndex((run) => run.modelKey === activeModelKey));
  const active = runs[activeIndex] ?? runs[0];
  const done = runs.filter((run) => run.status === "completed").length;

  useEffect(() => {
    if (!carousel) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setCarousel(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [carousel]);

  function step(delta: number) {
    if (runs.length === 0) return;
    const next = (activeIndex + delta + runs.length) % runs.length;
    const run = runs[next];
    if (run) selectModel(run.modelKey);
  }

  return (
    <article className="panel preview-panel workspace-preview vb-results">
      <div className="panel-heading preview-heading">
        <h2>结果预览 · {runs.length} 个版本 · {done} 已完成</h2>
        {many && (
          <button type="button" className="vb-carousel-btn" onClick={() => setCarousel(true)}>
            全屏轮播
          </button>
        )}
      </div>
      {!many && (
        <div className="preview-grid" style={{ gridTemplateColumns: `repeat(${Math.max(runs.length, 1)}, minmax(0, 1fr))` }}>
          {runs.map((run) => (
            <LiveCard key={run.taskId || run.modelKey} run={run} />
          ))}
        </div>
      )}
      {many && active && (
        <div className="vb-gallery">
          <div className="vb-stage">
            <button type="button" className="vb-nav vb-prev" onClick={() => step(-1)} aria-label="上一个模型">‹</button>
            <div className="vb-stage-body">
              <span className="vb-stage-label">
                <i style={{ background: modelAccent(active.modelKey) }} />
                {active.modelLabel}
                <StatusMark status={active.status} />
                <em>{activeIndex + 1} / {runs.length}</em>
              </span>
              {renderPreview(active, { presentation: "stage" })}
              {(active.status === "thinking" || active.status === "creating") && (
                <div className="vb-stage-overlay">
                  <Ring value={estimateProgress(active, elapsed(active))} />
                  <strong>{liveCount(active)}</strong>
                  <span>{active.modelLabel} · {stageLabel(active)}</span>
                </div>
              )}
            </div>
            <button type="button" className="vb-nav vb-next" onClick={() => step(1)} aria-label="下一个模型">›</button>
          </div>
          <div className="vb-film" role="tablist" aria-label="模型胶片">
            {runs.map((run) => (
              <div
                key={run.taskId || run.modelKey}
                role="tab"
                tabIndex={0}
                aria-selected={run.modelKey === active.modelKey}
                className={`vb-frame ${run.modelKey === active.modelKey ? "is-active" : ""}`}
                onClick={() => selectModel(run.modelKey)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    selectModel(run.modelKey);
                  }
                }}
              >
                <span className={`vb-frame-preview is-${run.status}`}>
                  {run.status === "completed" ? renderPreview(run, { presentation: "thumb" }) : run.status === "failed" ? (
                    <span className="vb-frame-pending is-failed">生成失败</span>
                  ) : (
                    <span className="vb-frame-pending">
                      <Ring value={estimateProgress(run, elapsed(run))} />
                      <b>{liveCount(run)}</b>
                    </span>
                  )}
                  {isUnseen(run.modelKey) && <b className="va-unseen" aria-label="新完成" />}
                </span>
                <span className="vb-frame-meta">
                  <i style={{ background: modelAccent(run.modelKey) }} />
                  <span>{run.modelLabel}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {carousel && active && (
        <div className="vb-carousel" role="dialog" aria-modal="true" aria-label="全屏轮播">
          <header>
            <span>{activeIndex + 1} / {runs.length} · {active.modelLabel}</span>
            <button type="button" onClick={() => setCarousel(false)}>关闭</button>
          </header>
          <div className="vb-carousel-body">
            <button type="button" onClick={() => step(-1)} aria-label="上一个">‹</button>
            <div>{renderPreview(active, { presentation: "stage" })}</div>
            <button type="button" onClick={() => step(1)} aria-label="下一个">›</button>
          </div>
        </div>
      )}
    </article>
  );
}
