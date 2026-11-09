"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { VariantAProgress, VariantAResults } from "./variant-a";
import { VariantBProgress, VariantBResults, type PastRoundProps } from "./variant-b";
import { VariantCProgress, VariantCResults } from "./variant-c";
import { VARIANT, VARIANT_PORTS } from "./variant";
import type { AppliedSkill, PreviewPresentation, RoundNav, WorkspaceRun } from "./types";

export type RenderPreview = (
  run: WorkspaceRun,
  options?: {
    presentation?: PreviewPresentation;
    onFrame?: (node: HTMLIFrameElement | null) => void;
  },
) => ReactNode;

type WorkspaceContextValue = {
  runs: WorkspaceRun[];
  activeModelKey: string;
  selectModel: (key: string) => void;
  isUnseen: (key: string) => boolean;
  completionOrder: string[];
  elapsed: (run: WorkspaceRun) => number | null;
  appliedSkill: AppliedSkill;
  isGenerating: boolean;
  renderPreview: RenderPreview;
  continueBasePageId: string;
  roundNav: RoundNav | null;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("工作区插槽必须放在 WorkspaceProvider 里");
  return value;
}

function useRunTimings(runs: WorkspaceRun[]) {
  const [meta, setMeta] = useState<Record<string, { startedAt: number; endedAt?: number }>>({});
  const [now, setNow] = useState(() => Date.now());
  const signature = runs.map((run) => `${run.taskId || run.modelKey}:${run.status}`).join("|");

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const timestamp = Date.now();
    const timer = window.setTimeout(() => {
      setMeta((current) => {
        const next = { ...current };
        let changed = false;
        for (const run of runs) {
          const key = run.taskId || run.modelKey;
          const active = run.status === "thinking" || run.status === "creating";
          if (!next[key] && active) {
            next[key] = { startedAt: timestamp };
            changed = true;
          }
          if (next[key] && !next[key].endedAt && (run.status === "completed" || run.status === "failed")) {
            next[key] = { ...next[key], endedAt: timestamp };
            changed = true;
          }
        }
        return changed ? next : current;
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [signature, runs]);

  return useCallback(
    (run: WorkspaceRun) => {
      if (typeof run.simulatedElapsedMs === "number") return run.simulatedElapsedMs;
      const item = meta[run.taskId || run.modelKey];
      if (!item) return null;
      return (item.endedAt ?? now) - item.startedAt;
    },
    [meta, now],
  );
}

export function WorkspaceProvider({
  runs,
  activeModelKey,
  onSelectModel,
  appliedSkill,
  isGenerating,
  continueBasePageId,
  renderPreview,
  pinnedModelKey,
  roundNav = null,
  trackUnseen = true,
  children,
}: {
  runs: WorkspaceRun[];
  activeModelKey: string;
  onSelectModel: (key: string) => void;
  appliedSkill: AppliedSkill;
  isGenerating: boolean;
  continueBasePageId: string;
  renderPreview: RenderPreview;
  /** 外部指定的聚焦模型，优先于组件内的点选（用于跨轮次联动）。 */
  pinnedModelKey?: string;
  roundNav?: RoundNav | null;
  /** 回看已结束的轮次时不需要"新完成"提示。 */
  trackUnseen?: boolean;
  children: ReactNode;
}) {
  const elapsed = useRunTimings(runs);
  const batchSig = runs.map((run) => `${run.modelKey}:${run.taskId}`).join("|");
  const [trackedBatch, setTrackedBatch] = useState(batchSig);
  const [completionOrder, setCompletionOrder] = useState<string[]>([]);
  const [seen, setSeen] = useState<string[]>([]);
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const completedKeys = runs.filter((run) => run.status === "completed").map((run) => run.modelKey);

  if (trackedBatch !== batchSig) {
    setTrackedBatch(batchSig);
    setCompletionOrder(completedKeys);
    setSeen([]);
    setPickedKey(null);
  } else {
    const missing = completedKeys.filter((key) => !completionOrder.includes(key));
    if (missing.length > 0) setCompletionOrder([...completionOrder, ...missing]);
  }

  const focusedKey = pinnedModelKey || pickedKey || completionOrder[0] || activeModelKey;
  const focused = runs.find((run) => run.modelKey === focusedKey);
  const seenNow = focused?.status === "completed" && !seen.includes(focused.modelKey) ? [...seen, focused.modelKey] : seen;
  if (seenNow !== seen) setSeen(seenNow);

  const selectModel = useCallback((key: string) => {
    setPickedKey(key);
    setSeen((current) => (current.includes(key) ? current : [...current, key]));
    onSelectModel(key);
  }, [onSelectModel]);

  const isUnseen = useCallback(
    (key: string) =>
      trackUnseen && key !== focusedKey && runs.some((run) => run.modelKey === key && run.status === "completed" && !seen.includes(key)),
    [runs, seen, focusedKey, trackUnseen],
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      runs,
      activeModelKey: focusedKey,
      selectModel,
      isUnseen,
      completionOrder,
      elapsed,
      appliedSkill,
      isGenerating,
      renderPreview,
      continueBasePageId,
      roundNav,
    }),
    [runs, focusedKey, selectModel, isUnseen, completionOrder, elapsed, appliedSkill, isGenerating, renderPreview, continueBasePageId, roundNav],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function ProgressSlot() {
  if (VARIANT.id === "B") return <VariantBProgress />;
  if (VARIANT.id === "C") return <VariantCProgress />;
  return <VariantAProgress />;
}

/** 已结束的一轮在对话栏里的摘要卡。只有方案 B 做了专门的历史样式，A/C 沿用进度卡。 */
export function PastRoundSlot(props: PastRoundProps) {
  if (VARIANT.id === "B") return <VariantBProgress history={props} />;
  if (VARIANT.id === "C") return <VariantCProgress />;
  return <VariantAProgress />;
}

export function ResultsSlot() {
  if (VARIANT.id === "B") return <VariantBResults />;
  if (VARIANT.id === "C") return <VariantCResults />;
  return <VariantAResults />;
}

export function VariantBadge() {
  const [location, setLocation] = useState<{ hostname: string; protocol: string; search: string } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLocation({ hostname: window.location.hostname, protocol: window.location.protocol, search: window.location.search });
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const demoCount = location ? new URLSearchParams(location.search).get("demo") : null;
  return (
    <div className="variant-tag" aria-label="当前预览方案">
      <span className="variant-dot" />
      <span>
        方案 {VARIANT.id} · {VARIANT.name}
        {demoCount ? ` · 演示 ${demoCount} 个模型` : ""}
      </span>
      {location && (
        <span className="variant-links">
          {VARIANT_PORTS.map((item) => (
            <a
              key={item.id}
              href={`${location.protocol}//${location.hostname}:${item.port}/${location.search}`}
              className={item.id === VARIANT.id ? "is-current" : ""}
              title={`方案 ${item.id} · ${item.name}`}
            >
              {item.id}
            </a>
          ))}
          {demoCount && (
            <a href={`/${location.search}`} title="从头重播演示">
              重播
            </a>
          )}
        </span>
      )}
    </div>
  );
}

export function useArrowModelNav(enabled: boolean) {
  const { runs, activeModelKey, selectModel } = useWorkspace();
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      const dialog = document.querySelector("[role='dialog']");
      if (dialog && !dialog.classList.contains("vb-carousel")) return;
      const index = Math.max(0, runs.findIndex((run) => run.modelKey === activeModelKey));
      const next = event.key === "ArrowRight" ? (index + 1) % runs.length : (index - 1 + runs.length) % runs.length;
      const run = runs[next];
      if (!run) return;
      event.preventDefault();
      selectModel(run.modelKey);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, runs, activeModelKey, selectModel]);
}
