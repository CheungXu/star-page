import type { WorkspaceRun } from "./types";

const COMFORT_LINES = [
  "模型正在规划版式结构",
  "正在组织标题和段落层级",
  "正在写入样式与交互",
  "正在打磨间距和配色",
  "快好了，正在做最后检查",
];

const MODEL_ACCENTS = ["#3563e9", "#8b5cf6", "#0ea5e9", "#f97316", "#14b8a6", "#ec4899"];

export function modelAccent(key: string): string {
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) >>> 0;
  }
  return MODEL_ACCENTS[hash % MODEL_ACCENTS.length];
}

/** 模型头像上的字：取名字首字，英文转大写。 */
export function modelGlyph(label: string): string {
  const first = label.trim().charAt(0);
  return /[a-z]/i.test(first) ? first.toUpperCase() : first;
}

export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes <= 0) return `${seconds} 秒`;
  return `${minutes}分${String(seconds).padStart(2, "0")}秒`;
}

export function formatTokens(value: number): string {
  if (value >= 1000) {
    const compact = value / 1000;
    const text = compact >= 10 ? compact.toFixed(1) : compact.toFixed(1);
    return `${text.replace(/\.0$/, "")}k`;
  }
  return String(Math.max(0, Math.round(value)));
}

export function formatTokenCount(value: number): string {
  return Math.max(0, Math.round(value)).toLocaleString("zh-CN");
}

export function outputTokenCount(run: WorkspaceRun): number {
  return run.usageSummary?.outputTokens ?? run.progressSteps.find((step) => step.id === "model_output")?.outputTokens ?? 0;
}

export function stageLabel(run: WorkspaceRun): string {
  if (run.status === "completed") return "完成";
  if (run.status === "failed") return "失败";
  const running = run.progressSteps.find((step) => step.status === "running");
  switch (running?.id) {
    case "upload_file":
    case "parse_file":
      return "读取资料";
    case "compress_document":
      return "整理资料";
    case "model_thinking":
      return "构思中";
    case "model_output":
      return "编写页面";
    case "deploy":
    case "upload":
    case "database":
      return "发布中";
    default:
      break;
  }
  if (run.status === "thinking") return "构思中";
  if (run.status === "creating") return "编写页面";
  return "准备中";
}

/** 估算进度只用来安抚等待，不显示百分比数字。 */
export function estimateProgress(run: WorkspaceRun, elapsedMs: number | null): number {
  if (run.status === "completed") return 100;
  const elapsed = Math.max(0, elapsedMs ?? 0);
  const thinking = run.progressSteps.find((step) => step.id === "model_thinking");
  const output = run.progressSteps.find((step) => step.id === "model_output");
  const deploy = run.progressSteps.find((step) => step.id === "deploy" || step.id === "upload" || step.id === "database");
  const preparing = run.progressSteps.find((step) =>
    step.id === "upload_file" || step.id === "parse_file" || step.id === "compress_document",
  );

  let value = 4;
  if (preparing && preparing.status !== "completed" && thinking?.status !== "running" && thinking?.status !== "completed") {
    value = preparing.status === "running" ? 8 : 5;
  }
  if (thinking?.status === "running" || (run.status === "thinking" && thinking?.status !== "completed")) {
    value = 10 + 20 * (1 - Math.exp(-elapsed / 12000));
  }
  if (thinking?.status === "completed") value = Math.max(value, 30);
  if (output?.status === "running" || (run.status === "creating" && output?.status !== "completed" && deploy?.status !== "running")) {
    const tokens = output?.outputTokens ?? run.usageSummary?.outputTokens ?? 0;
    const fraction = 1 - Math.exp(-tokens / 4500);
    value = 30 + fraction * 60;
  }
  if (output?.status === "completed") value = Math.max(value, 90);
  if (deploy?.status === "running") value = 94;
  if (run.status === "failed") return Math.min(value, 92);
  return Math.max(2, Math.min(99, value));
}

export function averageProgress(runs: WorkspaceRun[], elapsedOf: (run: WorkspaceRun) => number | null): number {
  if (runs.length === 0) return 0;
  const total = runs.reduce((sum, run) => sum + estimateProgress(run, elapsedOf(run)), 0);
  return total / runs.length;
}

export function comfortLine(index: number): string {
  return COMFORT_LINES[Math.abs(index) % COMFORT_LINES.length];
}

export function batchElapsed(runs: WorkspaceRun[], elapsedOf: (run: WorkspaceRun) => number | null): number | null {
  const values = runs.map((run) => elapsedOf(run)).filter((value): value is number => value !== null);
  if (values.length === 0) return null;
  return Math.max(...values);
}

export function sumCredits(runs: WorkspaceRun[]): { charged: number; discount: number; hasBilling: boolean } {
  let charged = 0;
  let discount = 0;
  let hasBilling = false;
  for (const run of runs) {
    if (typeof run.usageSummary?.creditsCharged === "number") {
      hasBilling = true;
      charged += run.usageSummary.creditsCharged;
    }
    if (typeof run.usageSummary?.discountCredits === "number") discount += run.usageSummary.discountCredits;
  }
  return { charged, discount, hasBilling };
}

export function sumOutputTokens(runs: WorkspaceRun[]): number {
  return runs.reduce((sum, run) => sum + outputTokenCount(run), 0);
}

export function isSettled(run: WorkspaceRun): boolean {
  return run.status === "completed" || run.status === "failed";
}
