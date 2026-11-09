"use client";

import type { WorkspaceRun } from "./types";

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
