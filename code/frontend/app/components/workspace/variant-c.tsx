"use client";

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { StatusMark } from "./bits";
import { useWorkspace } from "./context";
import {
  estimateProgress,
  formatDuration,
  formatTokens,
  modelAccent,
  outputTokenCount,
  stageLabel,
} from "./progress-utils";
import type { WorkspaceRun } from "./types";

function rankOf(order: string[], key: string): number {
  const index = order.indexOf(key);
  return index >= 0 ? index + 1 : 0;
}

export function VariantCProgress() {
  const { runs, elapsed, completionOrder, selectModel } = useWorkspace();
  const done = runs.filter((run) => run.status === "completed").length;

  function place(event: MouseEvent<HTMLButtonElement>, key: string) {
    const slot = event.shiftKey ? "b" : "a";
    selectModel(key);
    window.dispatchEvent(new CustomEvent("vp-place-model", { detail: { slot, key } }));
  }

  return (
    <div className="chat-message assistant-message vc-race">
      <div className="vc-race-head">
        <strong>生成赛道</strong>
        <span>{done}/{runs.length} 抵达 · 点一下放入 A，Shift 点放入 B</span>
      </div>
      <ol className="vc-lanes">
        {runs.map((run) => {
          const value = estimateProgress(run, elapsed(run));
          const time = elapsed(run);
          const rank = rankOf(completionOrder, run.modelKey);
          return (
            <li key={run.taskId || run.modelKey} className={`vc-lane is-${run.status}`}>
              <button type="button" onClick={(event) => place(event, run.modelKey)}>
                <span className="vc-lane-name">
                  <i style={{ background: modelAccent(run.modelKey) }} />
                  {run.modelLabel}
                </span>
                <span className="vc-track" aria-hidden="true">
                  <span className="vc-fill" style={{ width: `${value}%`, background: modelAccent(run.modelKey) }} />
                  <span className="vc-flag" />
                </span>
                <span className="vc-lane-meta">
                  {rank > 0 && <em className={`vc-rank is-${rank}`}>第 {rank} 名</em>}
                  <span>{stageLabel(run)}</span>
                  {outputTokenCount(run) > 0 && <span>{formatTokens(outputTokenCount(run))}</span>}
                  {time !== null && <span>{formatDuration(time)}</span>}
                </span>
              </button>
              {run.status === "failed" && run.errorMessage && <p className="vc-lane-error">{run.errorMessage}</p>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Slot({
  label,
  run,
  runs,
  onChange,
  onDropKey,
  onFrame,
}: {
  label: string;
  run: WorkspaceRun | undefined;
  runs: WorkspaceRun[];
  onChange: (key: string) => void;
  onDropKey: (key: string) => void;
  onFrame: (node: HTMLIFrameElement | null) => void;
}) {
  const { renderPreview, continueBasePageId, elapsed } = useWorkspace();
  const pending = run && (run.status === "thinking" || run.status === "creating");
  return (
    <div
      className={`vc-slot ${continueBasePageId && run?.pageId === continueBasePageId ? "is-best" : ""}`}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        const key = event.dataTransfer.getData("text/model-key");
        if (key) onDropKey(key);
      }}
    >
      <header>
        <span className="vc-slot-badge">{label}</span>
        <select aria-label={`${label} 槽模型`} value={run?.modelKey ?? ""} onChange={(event) => onChange(event.target.value)}>
          {runs.map((item) => (
            <option key={item.modelKey} value={item.modelKey}>
              {item.modelLabel} · {stageLabel(item)}
            </option>
          ))}
        </select>
      </header>
      {run && (
        <span className={`vc-slot-track ${pending ? "is-run" : ""}`} aria-hidden="true">
          <span
            style={{
              width: `${pending ? estimateProgress(run, elapsed(run)) : run.status === "completed" ? 100 : 0}%`,
              background: modelAccent(run.modelKey),
            }}
          />
        </span>
      )}
      <div className="vc-slot-body">{run && renderPreview(run, { presentation: "default", onFrame })}</div>
    </div>
  );
}

export function VariantCResults() {
  const { runs, completionOrder, renderPreview } = useWorkspace();
  const [slotA, setSlotA] = useState("");
  const [slotB, setSlotB] = useState("");
  const [sync, setSync] = useState(true);
  const [drawer, setDrawer] = useState(false);
  const [assigned, setAssigned] = useState(false);
  const frameA = useRef<HTMLIFrameElement | null>(null);
  const frameB = useRef<HTMLIFrameElement | null>(null);
  const [framesReady, setFramesReady] = useState(0);
  const modelsSig = runs.map((run) => run.modelKey).join(",");
  const [modelBatch, setModelBatch] = useState(modelsSig);

  if (modelBatch !== modelsSig) {
    setModelBatch(modelsSig);
    setAssigned(false);
    setSlotA(runs[0]?.modelKey ?? "");
    setSlotB(runs[1]?.modelKey ?? runs[0]?.modelKey ?? "");
  } else if (!assigned) {
    if (completionOrder[0] && completionOrder[0] !== slotA) setSlotA(completionOrder[0]);
    if (completionOrder[1] && completionOrder[1] !== slotB) setSlotB(completionOrder[1]);
  }

  const reportA = useCallback((node: HTMLIFrameElement | null) => {
    frameA.current = node;
    if (node) setFramesReady((value) => value + 1);
  }, []);
  const reportB = useCallback((node: HTMLIFrameElement | null) => {
    frameB.current = node;
    if (node) setFramesReady((value) => value + 1);
  }, []);

  useEffect(() => {
    const onPlace = (event: Event) => {
      const detail = (event as CustomEvent<{ slot: "a" | "b"; key: string }>).detail;
      if (!detail?.key) return;
      setAssigned(true);
      if (detail.slot === "b") setSlotB(detail.key);
      else setSlotA(detail.key);
    };
    window.addEventListener("vp-place-model", onPlace);
    return () => window.removeEventListener("vp-place-model", onPlace);
  }, []);

  useEffect(() => {
    if (!sync || runs.length < 2) return;
    const left = frameA.current;
    const right = frameB.current;
    if (!left || !right) return;
    let lock = false;
    const bind = (source: HTMLIFrameElement, target: HTMLIFrameElement) => {
      const win = source.contentWindow;
      if (!win) return () => {};
      const onScroll = () => {
        if (lock) return;
        lock = true;
        try {
          target.contentWindow?.scrollTo(0, win.scrollY);
        } catch {
          // 真实生成页在沙箱里，滚动同步只对演示页生效。
        }
        window.setTimeout(() => {
          lock = false;
        }, 40);
      };
      win.addEventListener("scroll", onScroll, { passive: true });
      return () => win.removeEventListener("scroll", onScroll);
    };
    let unbindA = () => {};
    let unbindB = () => {};
    const attach = () => {
      unbindA();
      unbindB();
      unbindA = bind(left, right);
      unbindB = bind(right, left);
    };
    left.addEventListener("load", attach);
    right.addEventListener("load", attach);
    attach();
    return () => {
      left.removeEventListener("load", attach);
      right.removeEventListener("load", attach);
      unbindA();
      unbindB();
    };
  }, [sync, slotA, slotB, runs.length, framesReady]);

  function assign(slot: "a" | "b", key: string) {
    setAssigned(true);
    if (slot === "a") setSlotA(key);
    else setSlotB(key);
  }

  if (runs.length <= 1) {
    const only = runs[0];
    return (
      <article className="panel preview-panel workspace-preview">
        <div className="panel-heading preview-heading"><h2>结果预览</h2></div>
        <div className="preview-grid">{only && renderPreview(only)}</div>
      </article>
    );
  }

  const runA = runs.find((run) => run.modelKey === slotA) ?? runs[0];
  const runB = runs.find((run) => run.modelKey === slotB) ?? runs[1];
  const done = runs.filter((run) => run.status === "completed").length;

  return (
    <article className="panel preview-panel workspace-preview vc-results">
      <div className="panel-heading preview-heading">
        <h2>对照 · {done}/{runs.length} 已完成</h2>
        <label className="vc-sync">
          <input type="checkbox" checked={sync} onChange={(event) => setSync(event.target.checked)} />
          同步滚动
        </label>
        {runs.length > 2 && (
          <button type="button" className="vc-all" onClick={() => setDrawer(true)}>全部 {runs.length} 个</button>
        )}
      </div>
      <div className="vc-ab">
        <Slot label="A" run={runA} runs={runs} onChange={(key) => assign("a", key)} onDropKey={(key) => assign("a", key)} onFrame={reportA} />
        <Slot label="B" run={runB} runs={runs} onChange={(key) => assign("b", key)} onDropKey={(key) => assign("b", key)} onFrame={reportB} />
      </div>
      {drawer && (
        <div className="vc-drawer" role="dialog" aria-label="全部模型">
          <header>
            <strong>全部 {runs.length} 个</strong>
            <button type="button" onClick={() => setDrawer(false)}>关闭</button>
          </header>
          <p>拖到 A 或 B 位置，或用卡片上的按钮放入。</p>
          <div className="vc-drawer-grid">
            {runs.map((run) => (
              <div
                key={run.modelKey}
                draggable
                className={`vc-drawer-card ${run.modelKey === runA?.modelKey ? "in-a" : ""} ${run.modelKey === runB?.modelKey ? "in-b" : ""}`}
                onDragStart={(event) => event.dataTransfer.setData("text/model-key", run.modelKey)}
              >
                <span className="vc-drawer-thumb">
                  {run.status === "completed" ? renderPreview(run, { presentation: "thumb" }) : (
                    <span className="vc-drawer-pending">{run.status === "failed" ? "生成失败" : `${stageLabel(run)}…`}</span>
                  )}
                </span>
                <span className="vc-drawer-name">
                  <StatusMark status={run.status} />
                  {run.modelLabel}
                </span>
                <em>{stageLabel(run)} · {formatTokens(outputTokenCount(run))} tokens</em>
                <span className="vc-drawer-actions">
                  <button type="button" onClick={() => assign("a", run.modelKey)}>放入 A</button>
                  <button type="button" onClick={() => assign("b", run.modelKey)}>放入 B</button>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}
