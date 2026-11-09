import type { ProgressStep, WorkspaceRound, WorkspaceRun } from "./types";

const DEMO_MODELS = [
  { key: "qwen3-max", label: "通义千问 3 Max" },
  { key: "qwen3-flash", label: "通义千问 3 Flash" },
  { key: "doubao-pro", label: "豆包 Seed 2.1 Pro" },
  { key: "doubao-turbo", label: "豆包 Seed 2.1 Turbo" },
  { key: "deepseek-v4-flash", label: "DeepSeek V4 Flash" },
  { key: "deepseek-v4-pro", label: "DeepSeek V4 Pro" },
  { key: "glm-5", label: "智谱 GLM 5.3" },
];

const THINK_MS = [2800, 4200, 3600, 5200, 6100, 7400, 8800];
const WRITE_MS = [7000, 9000, 11000, 8000, 13000, 15000, 12000];
const DEPLOY_MS = 1600;
const TOKEN_TARGET = [6200, 8400, 9100, 5400, 12000, 15000, 10200];
const CREDITS = [48, 62, 71, 44, 96, 118, 80];

const REASONING = [
  "先确认页面目标：让访客在首屏理解产品，并找到报名入口。",
  "结构分成首屏、亮点、议程、报名四段，避免信息平铺。",
  "配色压低饱和度，标题用大字号建立节奏。",
  "主按钮只留一个，其余入口收到页尾。",
];

export const DEMO_PROMPT = "为新品发布会做一个深色科技感落地页，突出产品亮点、议程和报名。";

export function readDemoCount(): number | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("demo");
  if (!raw) return null;
  const count = Number(raw);
  if (!Number.isFinite(count)) return null;
  return Math.max(1, Math.min(DEMO_MODELS.length, Math.round(count)));
}

/** `?at=12` 把演示定格在第 12 秒，方便对照某一帧。 */
export function readDemoFreezeSeconds(): number | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("at");
  if (!raw) return null;
  const seconds = Number(raw);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  return seconds;
}

function step(id: ProgressStep["id"], title: string, description: string, status: ProgressStep["status"], outputTokens?: number): ProgressStep {
  return {
    id,
    title,
    description,
    status,
    outputTokens,
    tokenSource: id === "model_output" ? "estimated" : undefined,
  };
}

function easedTokens(target: number, fraction: number): number {
  if (fraction <= 0) return 0;
  const clamped = Math.min(1, fraction);
  const curve = (1 - Math.exp(-3 * clamped)) / (1 - Math.exp(-3));
  return Math.round(target * curve);
}

const ROUND_PROMPTS = [
  DEMO_PROMPT,
  "主色换成品牌紫，议程改成竖向时间轴。",
  "首屏加一个倒计时，报名按钮再醒目一些。",
  "页尾补一段常见问题，语气轻松一点。",
];

/** 第 2 轮基于某个结果改（其余模型都在它上面改），之后各模型接着自己上一轮改。 */
const ROUND_BASE_INDEX = [-1, 0, -1, 2];

/** `?rounds=3` 演示第 3 轮正在生成，前两轮已结束。 */
export function readDemoRounds(): number {
  if (typeof window === "undefined") return 1;
  const raw = Number(new URLSearchParams(window.location.search).get("rounds"));
  if (!Number.isFinite(raw) || raw < 1) return 1;
  return Math.min(ROUND_PROMPTS.length, Math.round(raw));
}

export function demoRound(index: number, count: number, elapsedMs: number): WorkspaceRound {
  const baseIndex = ROUND_BASE_INDEX[index] ?? -1;
  const base = DEMO_MODELS[baseIndex];
  return {
    prompt: ROUND_PROMPTS[index] ?? DEMO_PROMPT,
    basedOn: base ? { modelKey: base.key, modelLabel: base.label } : null,
    runs: buildDemoRuns(index === 0 ? DEMO_MODELS.length : count, elapsedMs, { round: index + 1 }),
  };
}

export function buildDemoHistory(count: number, rounds: number): WorkspaceRound[] {
  return Array.from({ length: rounds - 1 }, (_, index) => demoRound(index, count, 60_000));
}

export function buildDemoRuns(count: number, elapsedMs: number, options: { round?: number } = {}): WorkspaceRun[] {
  const round = options.round ?? 1;
  const scale = round === 1 ? 1 : 0.55;
  return DEMO_MODELS.slice(0, count).map((model, index) => {
    const thinkMs = Math.round((THINK_MS[index] ?? 4000) * (round === 1 ? 1 : 0.7));
    const writeMs = Math.round((WRITE_MS[index] ?? 9000) * (round === 1 ? 1 : 0.7));
    const tokenTarget = Math.round((TOKEN_TARGET[index] ?? 8000) * scale);
    const shouldFail = count >= 3 && index === count - 1 && round !== 2;
    const failAt = thinkMs + 3200;
    const finishAt = thinkMs + writeMs + DEPLOY_MS;
    const elapsed = Math.max(0, elapsedMs);

    let phase: "think" | "write" | "deploy" | "done" | "fail" = "think";
    if (shouldFail && elapsed >= failAt) phase = "fail";
    else if (elapsed >= finishAt) phase = "done";
    else if (elapsed >= thinkMs + writeMs) phase = "deploy";
    else if (elapsed >= thinkMs) phase = "write";

    const writeFraction = phase === "think" ? 0 : phase === "fail" ? (failAt - thinkMs) / writeMs : (Math.min(elapsed, thinkMs + writeMs) - thinkMs) / writeMs;
    const tokens = phase === "done" || phase === "deploy" ? tokenTarget : easedTokens(tokenTarget, writeFraction);
    const revealed = Math.min(REASONING.length, 1 + Math.floor(elapsed / 1800));
    const reasoning = phase === "think" || elapsed < thinkMs + 400 ? REASONING.slice(0, revealed).join("\n") : REASONING.join("\n");

    const thinkingStatus = phase === "think" ? "running" : "completed";
    const outputStatus = phase === "write" ? "running" : phase === "think" ? "pending" : phase === "fail" ? "failed" : "completed";
    const deployStatus = phase === "deploy" ? "running" : phase === "done" ? "completed" : "pending";

    const status = phase === "done" ? "completed" : phase === "fail" ? "failed" : phase === "think" ? "thinking" : "creating";
    const simulatedElapsedMs = phase === "done" ? finishAt : phase === "fail" ? failAt : elapsed;

    const run: WorkspaceRun = {
      taskId: `demo-r${round}-${model.key}`,
      pageId: `demo-r${round}-${model.key}`,
      modelKey: model.key,
      modelLabel: model.label,
      status,
      reasoning,
      statusText: phase === "done" ? "页面已创建完成" : phase === "fail" ? "生成失败" : phase === "deploy" ? "正在发布页面" : phase === "write" ? "正在编写页面" : "正在构思页面结构",
      pageUrl: phase === "done" ? `/demo-result.html?tone=${index}&round=${round}` : "",
      errorMessage: phase === "fail" ? "模型在写页面时中断了，可以换个模型或稍后再试。" : "",
      progressSteps: [
        step("model_thinking", "模型思考", phase === "think" ? "正在构思" : "构思完成", thinkingStatus),
        step("model_output", "模型输出答案", phase === "write" ? "正在写页面" : "等待输出", outputStatus, tokens),
        step("deploy", "部署", phase === "deploy" ? "正在发布" : "等待发布", deployStatus),
      ],
      simulatedElapsedMs,
    };

    if (phase === "done" || tokens > 0) {
      run.usageSummary = {
        inputTokens: 1680 + index * 260,
        outputTokens: tokens,
        totalTokens: 1680 + index * 260 + tokens,
        cachedInputTokens: index % 2 === 0 ? 420 : 0,
        reasoningTokens: 480 + index * 70,
        creditsCharged: phase === "done" ? Math.round((CREDITS[index] ?? 60) * scale) : undefined,
        discountCredits: phase === "done" && index % 2 === 0 ? 8 : undefined,
      };
    }
    return run;
  });
}
