"use client";

import type { CSSProperties } from "react";
import { modelAccent, modelGlyph } from "./progress-utils";
import type { WorkspaceRound, WorkspaceRun } from "./types";

export function Ring({ value, label }: { value: number; label?: string }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - Math.min(100, Math.max(0, value)) / 100);
  return (
    <svg className="vp-ring" viewBox="0 0 72 72" role="img" aria-label={label ?? "生成进度"}>
      <circle className="vp-ring-track" cx="36" cy="36" r={radius} />
      <circle
        className="vp-ring-value"
        cx="36"
        cy="36"
        r={radius}
        style={{ strokeDasharray: circumference, strokeDashoffset: offset }}
      />
    </svg>
  );
}

export function StatusMark({ status }: { status: WorkspaceRun["status"] }) {
  if (status === "completed") return <span className="vp-mark is-done">✓</span>;
  if (status === "failed") return <span className="vp-mark is-fail">!</span>;
  return <span className="vp-mark is-run" />;
}

export function MiniAvatar({ modelKey, label }: { modelKey: string; label: string }) {
  return (
    <span className="round-source-avatar" style={{ "--accent": modelAccent(modelKey) } as CSSProperties} title={label}>
      {modelGlyph(label)}
    </span>
  );
}

/** 用户气泡右上角的来源标识：新建 / 基于某个结果改 / 各模型接着自己上一轮改。 */
export function RoundSource({
  roundIndex,
  basedOn,
  runs,
}: {
  roundIndex: number;
  basedOn: WorkspaceRound["basedOn"];
  runs: WorkspaceRun[];
}) {
  if (roundIndex === 0) {
    return (
      <span className="round-source is-new" title="这一轮新建页面">
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path className="is-fill" d="M6 1.2 7.1 4.9 10.8 6 7.1 7.1 6 10.8 4.9 7.1 1.2 6 4.9 4.9Z" />
        </svg>
        新建
      </span>
    );
  }
  if (basedOn) {
    return (
      <span className="round-source is-branch" title={`基于「${basedOn.modelLabel}」的结果修改`}>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <circle cx="3.5" cy="2.5" r="1.3" />
          <circle cx="3.5" cy="9.5" r="1.3" />
          <circle cx="8.5" cy="3.5" r="1.3" />
          <path d="M3.5 3.8v4.4M8.5 4.8c0 2.2-5 1.4-5 3.4" />
        </svg>
        <span className="round-source-label">基于</span>
        <MiniAvatar modelKey={basedOn.modelKey} label={basedOn.modelLabel} />
        <span className="round-source-name">{basedOn.modelLabel}</span>
      </span>
    );
  }
  const shown = runs.slice(0, 4);
  return (
    <span className="round-source is-parallel" title="每个模型接着自己上一轮的结果修改">
      <svg viewBox="0 0 12 12" aria-hidden="true">
        <path d="M1.5 3.75h7.5M7.5 2.25 9 3.75 7.5 5.25M1.5 8.25h7.5M7.5 6.75 9 8.25 7.5 9.75" />
      </svg>
      <span className="round-source-stack">
        {shown.map((run) => (
          <MiniAvatar key={run.modelKey} modelKey={run.modelKey} label={run.modelLabel} />
        ))}
        {runs.length > shown.length && <span className="round-source-more">+{runs.length - shown.length}</span>}
      </span>
      各自续改
    </span>
  );
}
