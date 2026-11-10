import type { WorkspaceRun } from "./types";

/**
 * 演示模式：`?demo=9` 用一次真实的 9 模型会话（页面已存成 public/demo-pages 下的静态文件）
 * 还原工作区，不调后端、不扣积分。`&live=1` 让最后一个模型停在生成中。
 */
const DEMO_MODELS = [
  { key: "qwen", label: "通义千问 3.8 Max", tokens: 40100, ms: 947_000, credits: 159, discount: 18 },
  { key: "qwen-plus", label: "通义千问 3.8 Flash", tokens: 47200, ms: 536_000, credits: 14 },
  { key: "doubao", label: "豆包 Seed 2.1 Pro", tokens: 12700, ms: 251_000, credits: 43 },
  { key: "doubao-code", label: "豆包 Seed 2.1 Turbo", tokens: 9900, ms: 160_000, credits: 17 },
  { key: "deepseek-v4.1-flash", label: "DeepSeek V4.1 Flash", tokens: 45200, ms: 242_000, credits: 39, discount: 20 },
  { key: "deepseek-v4-pro", label: "DeepSeek V4 Pro", tokens: 16900, ms: 377_000, credits: 46 },
  { key: "glm-5.3", label: "智谱 GLM-5.3", tokens: 65700, ms: 890_000, credits: 251 },
  { key: "glm-5.3-flash", label: "智谱 GLM-5.3 Flash", tokens: 25500, ms: 257_000, credits: 8 },
  { key: "kimi-k3.1-pro", label: "Kimi K3.1 Pro", tokens: 6800, ms: 561_000, credits: 0, unfinished: true },
];

export const DEMO_PROMPT =
  "完成一个网页，充分发挥你最顶级的审美、艺术设计和3D编程能力，在构图、光影、材质、粒子、动画和空间氛围上做到极致。画面需要有令人震撼的视觉表现力，并且可以通过鼠标自由交互。不限制你的创意和技术方案，尽可能展示你的能力上限。";

const DEMO_PREFIX = "demo-";
const DEMO_SLUG = "p-7eb0371fc9";

export function isDemoRun(run: Pick<WorkspaceRun, "pageId">): boolean {
  return run.pageId.startsWith(DEMO_PREFIX);
}

export function readDemo(): { prompt: string; runs: WorkspaceRun[] } | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("demo");
  if (raw === null) return null;
  const count = Math.max(1, Math.min(DEMO_MODELS.length, Math.round(Number(raw)) || DEMO_MODELS.length));
  const live = params.get("live") === "1";
  return { prompt: DEMO_PROMPT, runs: DEMO_MODELS.slice(0, count).map((model) => buildRun(model, live)) };
}

function buildRun(model: (typeof DEMO_MODELS)[number], live: boolean): WorkspaceRun {
  const status = model.unfinished ? (live ? "creating" : "failed") : "completed";
  const done = status === "completed";
  return {
    taskId: `${DEMO_PREFIX}task-${model.key}`,
    pageId: `${DEMO_PREFIX}${model.key}`,
    modelKey: model.key,
    modelLabel: model.label,
    status,
    reasoning: "",
    statusText: done ? "页面已创建完成" : status === "failed" ? "生成失败" : "正在编写页面",
    pageUrl: done ? `/demo-pages/${model.key}.html` : "",
    errorMessage: status === "failed" ? "模型在写页面时中断了，可以换个模型或稍后再试。" : "",
    progressSteps: [
      { id: "model_thinking", title: "模型思考", description: "构思完成", status: "completed" },
      {
        id: "model_output",
        title: "模型输出答案",
        description: done ? "页面已写完" : "正在写页面",
        status: done ? "completed" : status === "failed" ? "failed" : "running",
        outputTokens: model.tokens,
        tokenSource: "actual",
      },
      { id: "deploy", title: "部署", description: done ? "已发布" : "等待发布", status: done ? "completed" : "pending" },
    ],
    usageSummary: {
      inputTokens: 2100,
      outputTokens: model.tokens,
      creditsCharged: done ? model.credits : undefined,
      discountCredits: done ? model.discount : undefined,
    },
    durationMs: model.ms,
  };
}

export type SharePublication = {
  url: string;
  /** 「打开链接」实际打开的地址；演示模式下固定链接不存在，改开本地页面。 */
  openUrl: string;
  qrUrl: string;
  republished: boolean;
  externalViews: number;
};

let demoShared = false;

/** 模拟 POST /api/publications：同一会话只有一条固定链接，第二次起都算「重新发布」。 */
export async function publishDemo(run: WorkspaceRun): Promise<SharePublication> {
  await new Promise((resolve) => window.setTimeout(resolve, 900));
  const republished = demoShared;
  demoShared = true;
  return {
    url: `https://stars-page.com/u/${DEMO_SLUG}`,
    openUrl: run.pageUrl,
    qrUrl: "/demo-pages/qr.png",
    republished,
    externalViews: 12,
  };
}
