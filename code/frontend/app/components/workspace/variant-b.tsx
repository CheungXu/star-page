"use client";

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
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
  const { runs, elapsed, isGenerating, activeModelKey, selectModel } = useWorkspace();
  const [open, setOpen] = useState(false);
  const done = runs.filter((run) => run.status === "completed").length;
  const clock = batchElapsed(runs, elapsed);
  const tokens = sumOutputTokens(runs);
  const clockText = clock === null ? "" : isGenerating ? formatClock(clock) : formatDuration(clock);
  const credits = sumCredits(runs);
  const title = isGenerating ? `${runs.length} 个模型生成中` : `${done}/${runs.length} 已完成`;
  const sub = isGenerating
    ? `已写 ${formatTokens(tokens)} tokens`
    : `${formatTokens(tokens)} tokens${credits.hasBilling ? ` · ${credits.charged} 积分` : ""}`;

  return (
    <div className={`vb-status ${open ? "is-open" : ""}`}>
      <button type="button" className="vb-pill" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className={`vb-pill-dot ${isGenerating ? "is-run" : "is-done"}`} />
        <span className="vb-pill-text">
          <strong>
            {title}
            {clockText && <em>{clockText}</em>}
          </strong>
          {!open && <span>{sub}</span>}
        </span>
        <span className="vb-pill-more">{open ? "收起" : "明细"}</span>
      </button>
      {open && (
        <div className="vb-mini">
          <div className="vb-mini-head" aria-hidden="true">
            <span>模型</span>
            <span>输出</span>
            <span>用时</span>
            <span>积分</span>
          </div>
          <ul>
            {runs.map((run) => (
              <MiniRow
                key={run.taskId || run.modelKey}
                run={run}
                active={run.modelKey === activeModelKey}
                onSelect={() => selectModel(run.modelKey)}
              />
            ))}
          </ul>
          <div className="vb-mini-foot">
            <span>合计{credits.discount > 0 ? ` · 已优惠 ${credits.discount}` : ""}</span>
            <span>{formatTokens(tokens)}</span>
            <span>{shortDuration(clock)}</span>
            <span className={credits.hasBilling ? "is-charged" : ""}>{credits.hasBilling ? credits.charged : "…"}</span>
          </div>
        </div>
      )}
    </div>
  );
}

const PHASES = [
  { label: "构思", from: 0, to: 30 },
  { label: "编写", from: 30, to: 90 },
  { label: "发布", from: 90, to: 100 },
];

const SHORT_STAGE: Record<string, string> = {
  读取资料: "读取",
  整理资料: "整理",
  构思中: "构思",
  编写页面: "编写",
  发布中: "发布",
  准备中: "准备",
};

function shortDuration(ms: number | null): string {
  if (ms === null) return "—";
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return `${total}秒`;
  return `${Math.floor(total / 60)}分${String(total % 60).padStart(2, "0")}`;
}

function MiniRow({ run, active, onSelect }: { run: WorkspaceRun; active: boolean; onSelect: () => void }) {
  const { elapsed } = useWorkspace();
  const progress = estimateProgress(run, elapsed(run));
  const tokens = outputTokenCount(run);
  const pending = run.status === "thinking" || run.status === "creating";
  const charged = run.usageSummary?.creditsCharged;
  const discount = run.usageSummary?.discountCredits;
  const failedAt = run.status === "failed" ? PHASES.findIndex((phase) => progress < phase.to) : -1;

  return (
    <li className={`vb-mini-row is-${run.status} ${active ? "is-active" : ""}`}>
      <button type="button" onClick={onSelect} title={run.errorMessage || `在右侧查看 ${run.modelLabel}`}>
        <StatusMark status={run.status} />
        <span className="vb-mini-name">{run.modelLabel}</span>
        <span className="vb-mini-phase">
          <span className="vb-pips" aria-label={`阶段：${stageLabel(run)}`}>
            {PHASES.map((phase, index) => {
              const fill =
                run.status === "completed"
                  ? 100
                  : Math.max(0, Math.min(100, ((progress - phase.from) / (phase.to - phase.from)) * 100));
              const state =
                index === failedAt ? "is-fail" : fill >= 100 ? "is-full" : pending && fill > 0 ? "is-live" : "";
              return (
                <span key={phase.label} className={`vb-pip ${state}`} title={phase.label}>
                  <span style={{ width: `${index === failedAt ? 100 : fill}%` }} />
                </span>
              );
            })}
          </span>
          <span className="vb-mini-stage">{SHORT_STAGE[stageLabel(run)] ?? stageLabel(run)}</span>
        </span>
        <span className={`vb-mini-cell ${tokens > 0 ? "" : "is-empty"}`}>{tokens > 0 ? formatTokens(tokens) : "—"}</span>
        <span className="vb-mini-cell is-time">{shortDuration(elapsed(run))}</span>
        <span
          className={`vb-mini-cell is-cost ${typeof charged === "number" ? "is-charged" : "is-empty"}`}
          title={discount ? `已优惠 ${discount} 积分` : undefined}
        >
          {typeof charged === "number" ? charged : pending ? "…" : "—"}
          {discount ? <i aria-label="有优惠" /> : null}
        </span>
      </button>
    </li>
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
    <>
    <PaneResizer />
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
    </>
  );
}

const CONV_WIDTH_KEY = "star-page-conv-width";
const CONV_MIN = 300;

function clampConvWidth(value: number, layout: HTMLElement): number {
  const max = Math.max(CONV_MIN, Math.min(720, layout.clientWidth * 0.5));
  return Math.round(Math.min(max, Math.max(CONV_MIN, value)));
}

/** 对话栏与结果区之间的拖拽分隔条：宽度写进 --conv-width，并记在本机。 */
function PaneResizer() {
  const handleRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);

  function layoutOf(): HTMLElement | null {
    return handleRef.current?.closest(".workspace-layout") ?? null;
  }

  function apply(next: number | null, persist: boolean) {
    const layout = layoutOf();
    if (!layout) return;
    if (next === null) {
      layout.style.removeProperty("--conv-width");
      setWidth(null);
      if (persist) localStorage.removeItem(CONV_WIDTH_KEY);
      return;
    }
    const value = clampConvWidth(next, layout);
    layout.style.setProperty("--conv-width", `${value}px`);
    setWidth(value);
    if (persist) localStorage.setItem(CONV_WIDTH_KEY, String(value));
  }

  useEffect(() => {
    const saved = Number(localStorage.getItem(CONV_WIDTH_KEY));
    const layout = handleRef.current?.closest<HTMLElement>(".workspace-layout");
    if (!(saved > 0) || !layout) return;
    const value = clampConvWidth(saved, layout);
    layout.style.setProperty("--conv-width", `${value}px`);
    const timer = window.setTimeout(() => setWidth(value), 0);
    return () => window.clearTimeout(timer);
  }, []);

  function currentWidth(): number {
    const pane = layoutOf()?.querySelector<HTMLElement>(".conversation-pane");
    return width ?? pane?.getBoundingClientRect().width ?? CONV_MIN;
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const pane = layoutOf()?.querySelector<HTMLElement>(".conversation-pane");
    if (!pane) return;
    event.preventDefault();
    const left = pane.getBoundingClientRect().left;
    const offset = event.clientX - pane.getBoundingClientRect().right;
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    setDragging(true);
    document.body.classList.add("is-resizing-panes");

    const onMove = (move: globalThis.PointerEvent) => apply(move.clientX - offset - left, false);
    const onUp = (up: globalThis.PointerEvent) => {
      apply(up.clientX - offset - left, true);
      setDragging(false);
      document.body.classList.remove("is-resizing-panes");
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
    };
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.stopPropagation();
    const delta = (event.key === "ArrowRight" ? 1 : -1) * (event.shiftKey ? 64 : 16);
    apply(currentWidth() + delta, true);
  }

  return (
    <div
      ref={handleRef}
      className={`pane-resizer ${dragging ? "is-dragging" : ""}`}
      role="separator"
      aria-orientation="vertical"
      aria-label="拖动调整对话栏宽度，双击恢复默认"
      aria-valuemin={CONV_MIN}
      aria-valuenow={width ?? undefined}
      tabIndex={0}
      title="拖动调整宽度，双击恢复默认"
      onPointerDown={onPointerDown}
      onDoubleClick={() => apply(null, true)}
      onKeyDown={onKeyDown}
    >
      <span />
    </div>
  );
}
