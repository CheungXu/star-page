"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { RenderIssueBadge, Ring, StatusMark } from "./bits";
import { useArrowModelNav, useWorkspace } from "./context";
import {
  batchElapsed,
  estimateProgress,
  formatClock,
  formatDuration,
  formatTokens,
  isEndedStatus,
  isLiveStatus,
  modelAccent,
  modelGlyph,
  outputTokenCount,
  stageLabel,
  sumCredits,
  sumOutputTokens,
} from "./progress-utils";
import type { WorkspaceRun } from "./types";
import { UX_VARIANT } from "./variant";

function liveCount(run: WorkspaceRun): string {
  const tokens = outputTokenCount(run);
  return tokens > 0 ? formatTokens(tokens) : stageLabel(run);
}

/** 排队原因：后端 queued 事件给的说明去掉「排队中 · 」前缀，标题已经写了排队中。 */
function queueHint(run: WorkspaceRun): string {
  return run.statusText.replace(/^排队中\s*·\s*/, "") || "轮到后自动开始";
}

function LiveCard({ run }: { run: WorkspaceRun }) {
  const { elapsed, renderPreview } = useWorkspace();
  const value = estimateProgress(run, elapsed(run));
  const tokens = outputTokenCount(run);
  const pending = isLiveStatus(run.status);
  return (
    <div className="vb-live">
      {renderPreview(run, { presentation: "default" })}
      {pending && (
        <div className={`vb-live-overlay ${run.status === "queued" ? "is-queued" : ""}`}>
          <Ring value={value} />
          <strong>{liveCount(run)}</strong>
          <span>{run.status === "queued" ? queueHint(run) : tokens > 0 ? `${stageLabel(run)} · tokens` : "还没开始写页面"}</span>
        </div>
      )}
    </div>
  );
}

export type PastRoundProps = {
  /** 右侧结果区当前是否在显示这一轮。 */
  viewing: boolean;
  /** 下一轮选它作为基础继续修改的模型。 */
  pickedKey: string;
  /** 下一轮的称呼，例如「第 2 轮」。 */
  nextLabel: string;
  onView: () => void;
};

/** 对话栏里的进度回复气泡。传 history 时是已结束轮次的摘要卡，点击切换右侧结果。 */
export function GenerationProgress({ history }: { history?: PastRoundProps } = {}) {
  const { runs, elapsed, isGenerating, activeModelKey, selectModel, roundNav } = useWorkspace();
  const [open, setOpen] = useState(false);
  const done = runs.filter((run) => run.status === "completed").length;
  const stopped = runs.filter((run) => run.status === "cancelled").length;
  const queued = runs.filter((run) => run.status === "queued").length;
  const clock = batchElapsed(runs, elapsed);
  const tokens = sumOutputTokens(runs);
  const clockText = clock === null ? "" : isGenerating ? formatClock(clock) : formatDuration(clock);
  const credits = sumCredits(runs);
  const settled = runs.every((run) => isEndedStatus(run.status));
  const insights = batchInsights(runs, elapsed);
  const working = runs.filter((run) => run.status === "thinking" || run.status === "creating").length;
  const title = isGenerating
    ? queued > 0
      ? `${working} 个生成中 · ${queued} 个排队`
      : `${runs.length} 个模型生成中`
    : `${done}/${runs.length} 已完成${stopped > 0 ? ` · ${stopped} 已停止` : ""}`;
  const sub = isGenerating ? (
    `已写 ${formatTokens(tokens)} tokens`
  ) : (
    <>
      {formatTokens(tokens)} tokens
      {credits.hasBilling && (
        <>
          {" · "}
          <CoinIcon />
          {credits.charged}
        </>
      )}
    </>
  );
  // 多轮时点卡片＝在右侧显示这一轮；单轮时点卡片＝展开明细。
  const latest = roundNav ? roundNav.items.length - 1 : -1;
  const view = history
    ? { viewing: history.viewing, onView: history.onView }
    : roundNav && latest > 0
      ? { viewing: roundNav.viewing === latest, onView: () => roundNav.onView(latest) }
      : null;
  const showActive = !view || view.viewing;
  const toggle = () => setOpen((value) => !value);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const isViewing = Boolean(view?.viewing);

  useEffect(() => {
    if (isViewing) cardRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isViewing]);

  return (
    <div
      ref={cardRef}
      className={`vb-status ${open ? "is-open" : ""} ${history ? "is-history" : ""} ${view ? "is-switchable" : ""} ${view?.viewing ? "is-viewing" : ""}`}
    >
      <div className="vb-status-head">
        <button
          type="button"
          className="vb-pill"
          aria-expanded={view ? undefined : open}
          aria-pressed={view ? view.viewing : undefined}
          onClick={view ? view.onView : toggle}
          title={view ? (view.viewing ? "右侧正在显示这一轮" : "在右侧显示这一轮的结果") : undefined}
        >
          <span className={`vb-pill-dot ${isGenerating ? "is-run" : "is-done"}`} />
          <span className="vb-pill-text">
            <strong>
              {title}
              {clockText && <em>{clockText}</em>}
            </strong>
            <span className="vb-pill-sub">{open ? <span className="vb-pill-hint">点模型行，右侧切换到该版本</span> : sub}</span>
          </span>
          {view && (
            <span className={`vb-showing ${view.viewing ? "" : "is-hint"}`} aria-hidden={!view.viewing}>
              <ScreenIcon />
              {view.viewing ? "显示中" : "查看"}
            </span>
          )}
          {!view && <span className="vb-pill-more">{open ? "收起" : "明细"}</span>}
        </button>
        {view && (
          <button type="button" className="vb-more-btn" aria-expanded={open} onClick={toggle} title={open ? "收起明细" : "展开明细"}>
            <span className="vb-more-label">{open ? "收起" : "明细"}</span>
            <svg viewBox="0 0 10 10" aria-hidden="true">
              <path d="M2.5 3.75 5 6.25l2.5-2.5" />
            </svg>
          </button>
        )}
      </div>
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
                active={showActive && run.modelKey === activeModelKey}
                pickedFor={history && history.pickedKey === run.modelKey ? history.nextLabel : ""}
                insights={insights}
                onSelect={() => selectModel(run.modelKey)}
              />
            ))}
          </ul>
          <div className="vb-mini-foot">
            <span>合计{credits.discount > 0 ? ` · 已优惠 ${credits.discount}` : ""}</span>
            <span>{formatTokens(tokens)}</span>
            <span>{shortDuration(clock)}</span>
            <span className={credits.hasBilling ? "is-charged" : ""}>
              {credits.hasBilling ? (
                <>
                  <CoinIcon />
                  {credits.charged}
                </>
              ) : (
                settled ? "—" : "…"
              )}
            </span>
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
  已停止: "已停止",
  排队中: "排队中",
};

function shortDuration(ms: number | null): string {
  if (ms === null) return "—";
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return `${total}秒`;
  return `${Math.floor(total / 60)}分${String(total % 60).padStart(2, "0")}`;
}

type BatchInsights = { maxTokens: number; fastestKey: string; cheapestKey: string };

function batchInsights(runs: WorkspaceRun[], elapsedOf: (run: WorkspaceRun) => number | null): BatchInsights {
  const maxTokens = Math.max(1, ...runs.map(outputTokenCount));
  const finished = runs.filter((run) => run.status === "completed");
  if (finished.length < 2) return { maxTokens, fastestKey: "", cheapestKey: "" };
  const pick = (score: (run: WorkspaceRun) => number | null | undefined) => {
    let best: WorkspaceRun | null = null;
    let bestScore = Infinity;
    for (const run of finished) {
      const value = score(run);
      if (typeof value === "number" && value < bestScore) {
        best = run;
        bestScore = value;
      }
    }
    return best?.modelKey ?? "";
  };
  return {
    maxTokens,
    fastestKey: pick(elapsedOf),
    cheapestKey: pick((run) => run.usageSummary?.creditsCharged),
  };
}

function ModelAvatar({ run }: { run: WorkspaceRun }) {
  return (
    <span className={`vb-avatar is-${run.status}`} style={{ "--accent": modelAccent(run.modelKey) } as CSSProperties}>
      {modelGlyph(run.modelLabel)}
      {run.status === "completed" && <i className="vb-avatar-badge">✓</i>}
      {run.status === "failed" && <i className="vb-avatar-badge">!</i>}
      {run.status === "cancelled" && <i className="vb-avatar-badge" aria-label="已停止" />}
    </span>
  );
}

const ScreenIcon = () => (
  <svg className="vb-screen" viewBox="0 0 12 12" aria-hidden="true">
    <rect x="1.25" y="2" width="9.5" height="6.5" rx="1.5" />
    <path d="M4.5 10.5h3" />
  </svg>
);

const CoinIcon = () => (
  <svg className="vb-coin" viewBox="0 0 12 12" aria-hidden="true">
    <circle cx="6" cy="6" r="5.25" />
    <path d="M4 6h4M6 4v4" />
  </svg>
);

function MiniRow({
  run,
  active,
  pickedFor = "",
  insights,
  onSelect,
}: {
  run: WorkspaceRun;
  active: boolean;
  /** 下一轮基于这个结果修改时，填下一轮的称呼。 */
  pickedFor?: string;
  insights: BatchInsights;
  onSelect: () => void;
}) {
  const { elapsed } = useWorkspace();
  const progress = estimateProgress(run, elapsed(run));
  const tokens = outputTokenCount(run);
  const pending = isLiveStatus(run.status);
  const charged = run.usageSummary?.creditsCharged;
  const discount = run.usageSummary?.discountCredits;
  const failedAt = run.status === "failed" ? PHASES.findIndex((phase) => progress < phase.to) : -1;
  const isFastest = insights.fastestKey === run.modelKey;
  const isCheapest = insights.cheapestKey === run.modelKey;
  const hasRenderIssue = run.status === "completed" && Boolean(run.renderIssue);
  const stage = SHORT_STAGE[stageLabel(run)] ?? stageLabel(run);

  return (
    <li className={`vb-mini-row is-${run.status} ${active ? "is-active" : ""}`}>
      <button type="button" onClick={onSelect} title={run.errorMessage || `在右侧查看 ${run.modelLabel}`}>
        <ModelAvatar run={run} />
        <span className="vb-mini-name">{run.modelLabel}</span>
        <span className="vb-mini-phase">
          {!(run.status === "completed" && (isFastest || isCheapest || pickedFor)) && (
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
          )}
          {isFastest || isCheapest || pickedFor ? (
            <span className="vb-tags">
              {pickedFor && (
                <em className="vb-tag is-picked" title={`${pickedFor}基于这个结果修改`}>
                  {pickedFor.replace(/\s/g, "")}
                </em>
              )}
              {isFastest && <em className="vb-tag is-fast">最快</em>}
              {isCheapest && <em className="vb-tag is-cheap">最省</em>}
            </span>
          ) : run.status === "completed" ? null : (
            <span className="vb-mini-stage">{stage}</span>
          )}
        </span>
        <span className={`vb-mini-cell is-tokens ${tokens > 0 ? "" : "is-empty"}`}>
          {tokens > 0 ? formatTokens(tokens) : "—"}
          <span className="vb-meter" aria-hidden="true">
            <span style={{ width: `${(tokens / insights.maxTokens) * 100}%` }} />
          </span>
        </span>
        <span className="vb-mini-cell is-time">{shortDuration(elapsed(run))}</span>
        <span
          className={`vb-mini-cell is-cost ${typeof charged === "number" ? "is-charged" : "is-empty"}`}
          title={discount ? `已优惠 ${discount} 积分` : undefined}
        >
          {typeof charged === "number" ? (
            <>
              <CoinIcon />
              {charged}
            </>
          ) : pending ? (
            "…"
          ) : (
            "—"
          )}
          {discount ? <i aria-label="有优惠" /> : null}
        </span>
        {hasRenderIssue && (
          <span className="vb-mini-issue">
            <RenderIssueBadge issue={run.renderIssue} variant="row" />
          </span>
        )}
      </button>
    </li>
  );
}

/** 回看旧轮时挂在标题里的胶囊：左半说明在看哪一轮，右半一键回到最新。 */
function PastRoundChip() {
  const { roundNav } = useWorkspace();
  if (!roundNav) return null;
  const latest = roundNav.items.length - 1;
  if (roundNav.viewing >= latest) return null;
  const live = roundNav.items[latest]?.live;
  return (
    <span className="vb-past-chip" title="可以在任一版本上点「以此结果继续」，从这一轮接着改">
      <span className="vb-past-chip-label">
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2.2 6a3.8 3.8 0 1 0 1.1-2.7M2.2 1.8v1.9h1.9M6 3.8V6l1.5 1" />
        </svg>
        正在回看 第 {roundNav.viewing + 1} 轮
      </span>
      <button type="button" onClick={() => roundNav.onView(latest)}>
        {live && <i aria-label="生成中" />}
        回到第 {latest + 1} 轮
        <svg viewBox="0 0 10 10" aria-hidden="true">
          <path d="M3.5 2 6.5 5l-3 3" />
        </svg>
      </button>
    </span>
  );
}

/** 胶片条：竖向滚轮直接横向滚动（不用按 Shift），两端渐隐提示还有内容。 */
function useFilmScroll() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [edges, setEdges] = useState({ start: true, end: true });

  useEffect(() => {
    const film = ref.current;
    if (!film) return;
    let target = film.scrollLeft;
    let expected = film.scrollLeft;
    let frame = 0;

    const syncEdges = () => {
      const max = film.scrollWidth - film.clientWidth;
      const start = film.scrollLeft <= 1;
      const end = film.scrollLeft >= max - 1;
      setEdges((current) => (current.start === start && current.end === end ? current : { start, end }));
    };
    const tick = () => {
      // 拖滚动条、自动滚到选中项等别处改了位置时，平滑动画让出，不来回拉扯。
      if (Math.abs(film.scrollLeft - expected) > 2) {
        frame = 0;
        return;
      }
      const diff = target - film.scrollLeft;
      if (Math.abs(diff) < 1) {
        film.scrollLeft = target;
        frame = 0;
        return;
      }
      film.scrollLeft += Math.sign(diff) * Math.max(1, Math.abs(diff) * 0.2);
      expected = film.scrollLeft;
      frame = requestAnimationFrame(tick);
    };
    const onWheel = (event: WheelEvent) => {
      // 触控板横滑和 Shift+滚轮本来就是横向，交给浏览器；Ctrl+滚轮是缩放。
      if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
      const max = film.scrollWidth - film.clientWidth;
      if (max <= 0) return;
      const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? film.clientWidth : 1;
      const base = frame ? target : film.scrollLeft;
      const next = Math.max(0, Math.min(max, base + event.deltaY * unit));
      if (next === base) return;
      event.preventDefault();
      target = next;
      if (!frame) {
        expected = film.scrollLeft;
        frame = requestAnimationFrame(tick);
      }
    };

    film.addEventListener("wheel", onWheel, { passive: false });
    film.addEventListener("scroll", syncEdges, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(syncEdges);
    observer?.observe(film);
    syncEdges();
    return () => {
      film.removeEventListener("wheel", onWheel);
      film.removeEventListener("scroll", syncEdges);
      observer?.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);

  return [ref, edges] as const;
}

/**
 * 切换选中项时的胶片动效：C1 的高亮底板跟着选中格滑过去，并把选中格平滑带进可视区。
 * 选中格自己还在做宽度过渡，所以每帧读它的实时位置缓动追随，过渡结束且稳定后停下。
 */
function useFilmMotion(filmRef: RefObject<HTMLDivElement | null>, gliderRef: RefObject<HTMLSpanElement | null>, activeKey: string) {
  const posRef = useRef({ x: Number.NaN, w: 0 });

  useEffect(() => {
    const film = filmRef.current;
    if (!film || !activeKey) return;
    const pos = posRef.current;
    let frame = 0;
    let stable = 0;
    let userScrolled = false;
    let startedAt = performance.now();
    const stopFollow = () => {
      userScrolled = true;
    };

    const step = () => {
      const slot = film.querySelector<HTMLElement>(".vb-frame-slot.is-active");
      if (!slot) {
        frame = 0;
        return;
      }
      const tx = slot.offsetLeft;
      const tw = slot.offsetWidth;
      if (Number.isNaN(pos.x)) {
        pos.x = tx;
        pos.w = tw;
      } else {
        pos.x += (tx - pos.x) * 0.24;
        pos.w += (tw - pos.w) * 0.24;
      }
      const settled = Math.abs(tx - pos.x) < 0.5 && Math.abs(tw - pos.w) < 0.5;
      stable = settled ? stable + 1 : 0;
      const finished = stable > 8 && performance.now() - startedAt > 480;
      if (finished) {
        pos.x = tx;
        pos.w = tw;
      }
      const glider = gliderRef.current;
      if (glider) {
        glider.style.transform = `translate3d(${pos.x}px, ${slot.offsetTop}px, 0)`;
        glider.style.width = `${pos.w}px`;
        glider.style.height = `${slot.offsetHeight}px`;
        glider.style.opacity = "1";
      }
      if (!userScrolled) {
        const pad = 28;
        const left = film.scrollLeft;
        const max = film.scrollWidth - film.clientWidth;
        let want = left;
        if (tx < left + pad) want = tx - pad;
        else if (tx + tw > left + film.clientWidth - pad) want = tx + tw - film.clientWidth + pad;
        want = Math.max(0, Math.min(max, want));
        const diff = want - left;
        if (Math.abs(diff) >= 1) film.scrollLeft = left + Math.sign(diff) * Math.max(1, Math.abs(diff) * 0.22);
      }
      frame = finished ? 0 : requestAnimationFrame(step);
    };
    const kick = () => {
      startedAt = performance.now();
      stable = 0;
      if (!frame) frame = requestAnimationFrame(step);
    };

    film.addEventListener("wheel", stopFollow, { passive: true });
    film.addEventListener("pointerdown", stopFollow, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(kick);
    observer?.observe(film);
    kick();
    return () => {
      film.removeEventListener("wheel", stopFollow);
      film.removeEventListener("pointerdown", stopFollow);
      observer?.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [filmRef, gliderRef, activeKey]);
}

type StageLayer = { key: string; run: WorkspaceRun; ready: boolean };

/**
 * 舞台换模型：新页面图层立刻淡入（跟手），没画出来之前透明，由下面的旧页面顶着防闪白；
 * 新页面加载完（或最多 1.5 秒）后移除旧页面。
 */
function StageSwap({ active }: { active: WorkspaceRun }) {
  const { renderPreview } = useWorkspace();
  const [layers, setLayers] = useState<StageLayer[]>(() => [{ key: active.modelKey, run: active, ready: true }]);
  const top = layers[layers.length - 1];
  if (top.key !== active.modelKey) {
    const under = layers.filter((layer) => layer.ready && layer.key !== active.modelKey).slice(-1);
    setLayers([...under, { key: active.modelKey, run: active, ready: active.status !== "completed" }]);
  }

  const markReady = useCallback((key: string) => {
    setLayers((current) => current.map((layer) => (layer.key === key && !layer.ready ? { ...layer, ready: true } : layer)));
  }, []);

  useEffect(() => {
    if (top.ready) return;
    const timer = window.setTimeout(() => markReady(top.key), 1500);
    return () => window.clearTimeout(timer);
  }, [top.key, top.ready, markReady]);

  useEffect(() => {
    if (!top.ready || layers.length < 2) return;
    const timer = window.setTimeout(() => setLayers((current) => current.slice(-1)), 120);
    return () => window.clearTimeout(timer);
  }, [top.ready, layers.length]);

  return (
    <>
      {layers.map((layer) => {
        const isTop = layer.key === top.key;
        return (
          <div key={layer.key} className={`vb-stage-layer ${isTop ? "is-top" : "is-under"} ${layer.ready ? "is-ready" : ""}`}>
            {renderPreview(isTop ? active : layer.run, { presentation: "stage", onReady: isTop ? () => markReady(layer.key) : undefined })}
          </div>
        );
      })}
    </>
  );
}

/**
 * 方案 C1（线上）：选中的缩略图向右展开焦点卡（每格常驻，靠宽度过渡展开 / 收起）。
 * 用量三项与「打开 / 放大 / 分享」按列对齐成一张小表压在底部，模型名在表上方竖向居中；「继续改」在缩略图上。
 */
function FocusPanel({ run, active }: { run: WorkspaceRun; active: boolean }) {
  const { elapsed, renderActions } = useWorkspace();
  const tokens = outputTokenCount(run);
  const credits = run.usageSummary?.creditsCharged;
  const duration = elapsed(run);
  const completed = run.status === "completed";
  const pending = isLiveStatus(run.status);
  return (
    <div className={`tb-focus ${active ? "is-active" : ""}`} inert={!active} style={{ "--accent": modelAccent(run.modelKey) } as CSSProperties}>
      <div className="tb-focus-inner">
        <div className="tb-focus-info">
          <div className="tb-focus-head">
            <i className="tb-focus-dot" aria-hidden="true" />
            <strong title={run.modelLabel}>{run.modelLabel}</strong>
            {!completed && <StatusMark status={run.status} />}
          </div>
          <div className={`tb-focus-meta ${completed ? "is-grid" : ""}`}>
            {run.status === "failed" ? (
              <span className="is-failed">生成失败</span>
            ) : run.status === "cancelled" ? (
              <span className="is-stopped">已停止</span>
            ) : (
              <>
                <span title={`输出 ${tokens} tokens`}>
                  <TokenIcon />
                  {tokens > 0 ? formatTokens(tokens) : "—"}
                </span>
                <span title={`生成用时 ${formatDuration(duration ?? 0)}`}>
                  <ClockIcon />
                  {shortDuration(duration)}
                </span>
                {typeof credits === "number" && (
                  <span className="is-cost" title={`消耗 ${credits} 积分`}>
                    <CoinIcon />
                    {credits}
                  </span>
                )}
              </>
            )}
          </div>
          {completed ? (
            renderActions(run, "card")
          ) : (
            <p className="tb-focus-hint">
              {run.status === "queued"
                ? `排队中：${queueHint(run)}`
                : pending
                ? "生成完成后就能打开、分享"
                : run.status === "cancelled"
                  ? "这个模型已停止生成，不扣积分"
                  : "这个模型没有生成成功，可以换一个看看"}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** 方案 C2：聚光胶片。选中的缩略图放大浮起，模型名和操作压在缩略图底部的渐暗层上。 */
function SpotOverlay({ run, active }: { run: WorkspaceRun; active: boolean }) {
  const { renderActions } = useWorkspace();
  return (
    <span className="tb-spot" inert={!active} style={{ "--accent": modelAccent(run.modelKey) } as CSSProperties}>
      <span className="tb-spot-caption">
        <i aria-hidden="true" />
        <strong>{run.modelLabel}</strong>
        <StatusMark status={run.status} />
      </span>
      {run.status === "completed" && renderActions(run, "spot")}
    </span>
  );
}

const TokenIcon = () => (
  <svg className="vb-meta-icon" viewBox="0 0 12 12" aria-hidden="true">
    <path d="M2 3h8M2 6h8M2 9h5" />
  </svg>
);

const ClockIcon = () => (
  <svg className="vb-meta-icon" viewBox="0 0 12 12" aria-hidden="true">
    <circle cx="6" cy="6" r="4.6" />
    <path d="M6 3.6V6l1.6 1" />
  </svg>
);

/** 右侧结果区：1-2 个模型并排，3 个以上舞台 + 胶片条 + 全屏轮播。 */
export function GenerationResults() {
  const { runs, activeModelKey, selectModel, isUnseen, renderPreview, renderActions, elapsed, roundNav } = useWorkspace();
  const [carousel, setCarousel] = useState(false);
  const many = runs.length >= 3;
  useArrowModelNav(many);
  const activeIndex = Math.max(0, runs.findIndex((run) => run.modelKey === activeModelKey));
  const active = runs[activeIndex] ?? runs[0];
  const done = runs.filter((run) => run.status === "completed").length;
  const [filmRef, filmEdges] = useFilmScroll();
  const gliderRef = useRef<HTMLSpanElement | null>(null);
  useFilmMotion(filmRef, gliderRef, many ? (active?.modelKey ?? "") : "");
  const variant = UX_VARIANT;

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
    <article className={`panel preview-panel workspace-preview vb-results tb-variant-${variant.toLowerCase()}`}>
      <div className="panel-heading preview-heading">
        <h2>
          {roundNav && roundNav.items.length > 1 ? "" : "结果预览 · "}
          {runs.length} 个版本 · {done} 已完成
        </h2>
        <PastRoundChip />
        {many && (
          <div className="vb-heading-tools">
            {active?.status === "completed" && active.renderIssue && (
              <RenderIssueBadge issue={active.renderIssue} modelLabel={active.modelLabel} />
            )}
            <button type="button" className="vb-carousel-btn" onClick={() => setCarousel(true)}>
              全屏轮播
            </button>
          </div>
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
              {variant === "A" && (
                <div className="tb-chrome" style={{ "--accent": modelAccent(active.modelKey) } as CSSProperties}>
                  <span className="tb-chrome-model" title={active.modelLabel}>
                    <span className="tb-chrome-avatar" aria-hidden="true">{modelGlyph(active.modelLabel)}</span>
                    <strong>{active.modelLabel}</strong>
                    <StatusMark status={active.status} />
                    <span className="tb-chrome-index">
                      {activeIndex + 1}
                      <i>/</i>
                      {runs.length}
                    </span>
                  </span>
                  {renderActions(active, "chrome")}
                </div>
              )}
              <div className="vb-stage-view">
                <StageSwap active={active} />
                {isLiveStatus(active.status) && (
                  <div className={`vb-stage-overlay ${active.status === "queued" ? "is-queued" : ""}`}>
                    <Ring value={estimateProgress(active, elapsed(active))} />
                    <strong>{liveCount(active)}</strong>
                    <span>{active.modelLabel} · {active.status === "queued" ? queueHint(active) : stageLabel(active)}</span>
                  </div>
                )}
              </div>
            </div>
            <button type="button" className="vb-nav vb-next" onClick={() => step(1)} aria-label="下一个模型">›</button>
          </div>
          <div
            ref={filmRef}
            className={`vb-film ${filmEdges.start ? "" : "has-before"} ${filmEdges.end ? "" : "has-after"}`}
            role="tablist"
            aria-label="模型胶片"
          >
            {variant === "C1" && <span ref={gliderRef} className="vb-film-glider" aria-hidden="true" />}
            {runs.map((run) => {
              const isActive = run.modelKey === active.modelKey;
              return (
                <div key={run.taskId || run.modelKey} className={`vb-frame-slot ${isActive ? "is-active" : ""}`} role="presentation">
                  <div
                    role="tab"
                    tabIndex={0}
                    aria-selected={isActive}
                    className={`vb-frame ${isActive ? "is-active" : ""}`}
                    onClick={() => selectModel(run.modelKey)}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return;
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        selectModel(run.modelKey);
                      }
                    }}
                  >
                    <span className={`vb-frame-preview is-${run.status}`}>
                      {run.status === "completed" ? renderPreview(run, { presentation: "thumb" }) : run.status === "failed" ? (
                        <span className="vb-frame-pending is-failed">生成失败</span>
                      ) : run.status === "cancelled" ? (
                        <span className="vb-frame-pending is-stopped">已停止</span>
                      ) : (
                        <span className="vb-frame-pending">
                          <Ring value={estimateProgress(run, elapsed(run))} />
                          <b>{liveCount(run)}</b>
                        </span>
                      )}
                      {isUnseen(run.modelKey) && <b className="vb-unseen" aria-label="新完成" />}
                      {variant === "C1" && isActive && (
                        <span className="tb-thumb-cta-slot">{renderActions(run, "continue")}</span>
                      )}
                      {variant === "C2" && <SpotOverlay run={run} active={isActive} />}
                    </span>
                    <span className="vb-frame-meta">
                      <i style={{ background: modelAccent(run.modelKey) }} />
                      <span>{run.modelLabel}</span>
                    </span>
                  </div>
                  {variant === "C1" && <FocusPanel run={run} active={isActive} />}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {carousel && active && (
        <div className="vb-carousel" role="dialog" aria-modal="true" aria-label="全屏轮播">
          <header>
            <span className="vb-carousel-title">
              <span>{activeIndex + 1} / {runs.length} · {active.modelLabel}</span>
              {active.status === "completed" && active.renderIssue && (
                <RenderIssueBadge issue={active.renderIssue} variant="on-dark" />
              )}
            </span>
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
