"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { isEndedStatus } from "./progress-utils";
import type { ActionLayout, AppliedSkill, PreviewPresentation, RoundNav, WorkspaceRun } from "./types";

export type RenderPreview = (
  run: WorkspaceRun,
  options?: {
    presentation?: PreviewPresentation;
    onFrame?: (node: HTMLIFrameElement | null) => void;
    /** 页面 iframe 加载完成（用于舞台切换时等新页面画好再淡入）。 */
    onReady?: () => void;
  },
) => ReactNode;

export type RenderActions = (run: WorkspaceRun, layout: ActionLayout) => ReactNode;

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
  renderActions: RenderActions;
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
          if (next[key] && !next[key].endedAt && isEndedStatus(run.status)) {
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
      if (typeof run.durationMs === "number") return run.durationMs;
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
  renderActions,
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
  renderActions: RenderActions;
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
      renderActions,
      continueBasePageId,
      roundNav,
    }),
    [runs, focusedKey, selectModel, isUnseen, completionOrder, elapsed, appliedSkill, isGenerating, renderPreview, renderActions, continueBasePageId, roundNav],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
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
