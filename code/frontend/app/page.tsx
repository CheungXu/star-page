"use client";

import type { ChangeEvent, FormEvent, ReactNode, SyntheticEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

type GenerationStatus = "idle" | "thinking" | "creating" | "completed" | "failed";

type CreateGenerationResponse = {
  task_id: string;
  page_id: string;
  status: string;
  page_url: string;
};

type SsePayload = {
  type: string;
  text?: string;
  page_id?: string;
  url?: string;
  message?: string;
  step?: ProgressStepId;
  status?: ProgressStepStatus;
  output_tokens?: number;
  token_source?: "actual" | "estimated";
};

type ProgressStepId =
  | "upload_file"
  | "parse_file"
  | "compress_document"
  | "model_thinking"
  | "model_output"
  | "deploy"
  | "database"
  | "upload";
type ProgressStepStatus = "pending" | "running" | "completed" | "failed";

type ProgressStep = {
  id: ProgressStepId;
  title: string;
  description: string;
  status: ProgressStepStatus;
  outputTokens?: number;
  tokenSource?: "actual" | "estimated";
};

type StoredSession = {
  id: string;
  taskId?: string;
  pageId?: string;
  prompt: string;
  fileNames: string[];
  status: GenerationStatus;
  reasoning: string;
  statusText: string;
  pageUrl: string;
  errorMessage: string;
  progressSteps: ProgressStep[];
  createdAt: string;
  updatedAt: string;
};

type HistoryItem = {
  id: string;
  taskId?: string;
  pageId?: string;
  title: string;
  prompt: string;
  fileNames: string[];
  pageUrl: string;
  status: GenerationStatus;
  createdAt: string;
  updatedAt: string;
};

type PageHistoryResponseItem = {
  id: string;
  task_id?: string | null;
  title: string;
  prompt: string;
  file_names: string[];
  page_url: string;
  page_status: string;
  generation_status?: string | null;
  created_at: string;
  updated_at: string;
};

const PROGRESS_STEP_META: Record<ProgressStepId, Pick<ProgressStep, "title" | "description">> = {
  upload_file: {
    title: "上传文件",
    description: "正在上传文件到服务端",
  },
  parse_file: {
    title: "解析文件",
    description: "等待上传文件解析完成",
  },
  compress_document: {
    title: "压缩文件内容",
    description: "等待长文本压缩为页面生成简报",
  },
  model_thinking: {
    title: "模型思考",
    description: "等待模型开始思考",
  },
  model_output: {
    title: "模型输出答案",
    description: "等待模型开始输出 HTML",
  },
  deploy: {
    title: "部署",
    description: "等待 HTML 上传和数据库更新",
  },
  upload: {
    title: "上传文件中",
    description: "等待 HTML 文件生成完成",
  },
  database: {
    title: "记录数据库",
    description: "等待页面版本和任务状态写入",
  },
};

function createProgressStep(id: ProgressStepId, status: ProgressStepStatus = "pending"): ProgressStep {
  return {
    id,
    ...PROGRESS_STEP_META[id],
    status,
    outputTokens: id === "model_output" ? 0 : undefined,
    tokenSource: id === "model_output" ? "estimated" : undefined,
  };
}

function createInitialProgressSteps(hasFile = false): ProgressStep[] {
  return [
    ...(hasFile ? [createProgressStep("upload_file", "running"), createProgressStep("parse_file"), createProgressStep("compress_document")] : []),
    createProgressStep("model_thinking"),
    createProgressStep("model_output"),
    createProgressStep("deploy"),
  ];
}

const PREVIEW_VIEWPORT_WIDTH = 1200;
const PREVIEW_DEFAULT_HEIGHT = 900;
const CURRENT_SESSION_KEY = "star-page-current-session";
const ACCEPTED_FILE_EXTENSIONS = [".docx", ".pptx", ".xlsx", ".xls", ".pdf", ".txt", ".md", ".markdown", ".html", ".htm"];
const ACCEPTED_FILE_TYPES = ACCEPTED_FILE_EXTENSIONS.join(",");
const MAX_FILE_COUNT = 3;
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;
const MAX_TOTAL_FILE_SIZE_BYTES = 50 * 1024 * 1024;

const HistoryIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10"></circle>
    <polyline points="12 6 12 12 16 14"></polyline>
  </svg>
);

const PlusIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="5" x2="12" y2="19"></line>
    <line x1="5" y1="12" x2="19" y2="12"></line>
  </svg>
);

const AttachmentIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
  </svg>
);

const ArrowUpIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="12" y1="19" x2="12" y2="5" />
    <polyline points="5 12 12 5 19 12" />
  </svg>
);

/* 加载态转圈：用于"生成中"按钮，明确表达"处理中"而非"可点击" */
const SpinnerIcon = () => (
  <svg className="spinner-icon" width="1em" height="1em" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
  </svg>
);

/* 关闭/清空小图标：让"清空"从纯文本升级为带图标的次级按钮 */
const CloseIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);

/* 纸飞机：工作区底部"发送/修改"用，比"创建 ⬆"更贴合"迭代调整"的语义 */
const SendIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
);

/* 闪电：Token 用量前缀的科技感微图标，替代默认列表小圆点 */
const BoltIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5z" />
  </svg>
);

/* Chip 用 emoji：面向年轻白领与学生群体，emoji 比单色线性图标更活泼亲切 */
type PromptPreset = { id: string; emoji: string; label: string; prompt: string };

const PROMPT_PRESETS: PromptPreset[] = [
  {
    id: "product",
    emoji: "🚀",
    label: "产品介绍页",
    prompt: "结合我上传的产品资料，做一个面向客户的介绍页，风格简洁、高级，包含核心卖点与适用场景。",
  },
  {
    id: "report",
    emoji: "📊",
    label: "工作汇报",
    prompt: "根据我的内容，做一份图文并茂的工作汇报页面，结构清晰，突出关键数据与下一步计划。",
  },
  {
    id: "resume",
    emoji: "👤",
    label: "个人简历",
    prompt: "帮我生成一个精致的个人作品集 / 简历单页，突出履历、项目和联系方式。",
  },
  {
    id: "event",
    emoji: "🎉",
    label: "活动邀请",
    prompt: "做一个活动邀请落地页，包含活动主题、时间地点、亮点议程和报名引导。",
  },
];

/* ==========================================================================
   首页 ↔ 生成页 衔接过渡（motion 单方案 + 兜底直接切换）
   ------------------------------------------------------------------------
   用 motion 库做命令式编排：FLIP 输入卡飞行 + 文字上浮成气泡 + 内容 stagger 入场。
   兜底：prefers-reduced-motion 或 motion 库加载失败 → 直接切换（不报错）。
   曾评估并放弃的 View Transitions / 纯 CSS 两级降级见
   wiki/frontend-home-workspace-transition.md；完整三级版留档在 full-animation-mode 分支。
   ========================================================================== */
type MotionLib = {
  animate: (
    target: Element | Element[],
    keyframes: Record<string, unknown>,
    options?: Record<string, unknown>,
  ) => { finished: Promise<unknown> };
};

const TRANSITION_EASE = [0.22, 1, 0.36, 1] as const;

/* 过渡依赖的 DOM「契约」——集中在此，便于未来改页面时同步维护。
   这些选择器 / 标记是 motion 抓取共享元素与入场目标的唯一依据；若重命名相关 class
   或调整结构，请同步更新这里，否则过渡会「静默失效」（只是没有动画，不会报错、
   不影响功能）。 */
const TRANSITION_DOM = {
  sharedCard: ".prompt-card", // FLIP 飞行的共享输入卡（首页大卡 ↔ 工作区底部 composer）
  promptText: ".prompt-card textarea", // 文字「变气泡」动画的源文字（首页输入框）
  bubbleTarget: ".user-message p", // 文字「变气泡」动画的落点（工作区需求气泡）
  staggerItems: "[data-anim-stagger]", // 逐项 stagger 入场的内容（JSX 上用 data-anim-stagger 标记）
} as const;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/* 旧视图快照：克隆当前 <main> 覆盖全屏，用于 motion 离场淡出 */
function createTransitionOverlay(oldMain: HTMLElement): HTMLElement {
  const overlay = oldMain.cloneNode(true) as HTMLElement;
  overlay.setAttribute("aria-hidden", "true");
  overlay.style.position = "fixed";
  overlay.style.inset = "0";
  overlay.style.zIndex = "40";
  overlay.style.pointerEvents = "none";
  document.body.appendChild(overlay);
  return overlay;
}

/* 过渡总入口（C 方案：仅 motion + 兜底直接切换）。
   mutate 内是会切换 status 的一批 setState；motion 路径用 flushSync 同步提交，
   以便在切换前后测量、给输入卡做 FLIP。
   兜底：reduced-motion 或 motion 库未就绪（动态 import 失败）→ 直接切换，
   等同改造前的瞬切但不报错。 */
function runStageTransition(
  stage: HTMLElement | null,
  motionLib: MotionLib | null,
  mutate: () => void,
): void {
  if (!stage || !motionLib || prefersReducedMotion()) {
    mutate();
    return;
  }

  motionStageTransition(stage, motionLib, mutate);
}

/* 主方案：motion 库命令式 FLIP / 编排 */
function motionStageTransition(stage: HTMLElement, motionLib: MotionLib, mutate: () => void): void {
  const { animate } = motionLib;
  const oldMain = stage.firstElementChild as HTMLElement | null;
  const oldCard = oldMain?.querySelector(TRANSITION_DOM.sharedCard) as HTMLElement | null;
  const firstCard = oldCard?.getBoundingClientRect() ?? null;
  const oldTextarea = oldMain?.querySelector(TRANSITION_DOM.promptText) as HTMLTextAreaElement | null;
  const floatText = oldTextarea?.value ?? "";
  const firstText = oldTextarea?.getBoundingClientRect() ?? null;
  const fromIdle = oldMain?.classList.contains("home-shell") ?? false;
  const overlay = oldMain ? createTransitionOverlay(oldMain) : null;

  flushSync(mutate);

  const newMain = stage.firstElementChild as HTMLElement | null;
  if (!newMain) {
    overlay?.remove();
    return;
  }
  const newCard = newMain.querySelector(TRANSITION_DOM.sharedCard) as HTMLElement | null;
  const lastCard = newCard?.getBoundingClientRect() ?? null;
  const bubble = newMain.querySelector(TRANSITION_DOM.bubbleTarget) as HTMLElement | null;

  // 旧视图淡出上移
  if (overlay) {
    const removeOverlay = () => overlay.remove();
    animate(
      overlay,
      { opacity: [1, 0], transform: ["translateY(0px)", "translateY(-10px)"] },
      { duration: 0.28, ease: [0.4, 0, 0.2, 1] },
    ).finished.then(removeOverlay, removeOverlay);
  }

  // 新视图整体淡入
  animate(newMain, { opacity: [0, 1] }, { duration: 0.36, delay: 0.05 });

  // 输入卡 FLIP：从旧位置飞到新位置
  if (firstCard && newCard && lastCard) {
    const dx = firstCard.left - lastCard.left;
    const dy = firstCard.top - lastCard.top;
    const sx = lastCard.width ? firstCard.width / lastCard.width : 1;
    const sy = lastCard.height ? firstCard.height / lastCard.height : 1;
    newCard.style.transformOrigin = "top left";
    const clearOrigin = () => {
      newCard.style.transformOrigin = "";
    };
    animate(
      newCard,
      {
        transform: [
          `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`,
          "translate(0px, 0px) scale(1, 1)",
        ],
      },
      { duration: 0.52, ease: TRANSITION_EASE },
    ).finished.then(clearOrigin, clearOrigin);
  }

  // 输入文字上浮“变成”需求气泡（仅 首页 → 生成 方向）
  if (fromIdle && floatText && firstText && bubble) {
    const lastText = bubble.getBoundingClientRect();
    const clone = document.createElement("div");
    clone.textContent = floatText;
    Object.assign(clone.style, {
      position: "fixed",
      left: `${firstText.left}px`,
      top: `${firstText.top}px`,
      width: `${firstText.width}px`,
      margin: "0",
      zIndex: "41",
      pointerEvents: "none",
      whiteSpace: "pre-wrap",
      color: "var(--color-text-primary)",
      fontSize: "14px",
      lineHeight: "1.7",
    } as Partial<CSSStyleDeclaration>);
    document.body.appendChild(clone);
    bubble.style.opacity = "0";
    const tdx = lastText.left - firstText.left;
    const tdy = lastText.top - firstText.top;
    const cleanupClone = () => {
      clone.remove();
      bubble.style.opacity = "";
    };
    animate(
      clone,
      { transform: ["translate(0px, 0px)", `translate(${tdx}px, ${tdy}px)`], opacity: [0.95, 0] },
      { duration: 0.5, ease: TRANSITION_EASE },
    ).finished.then(cleanupClone, cleanupClone);
  }

  // 新内容逐项 stagger 入场
  const items = Array.from(newMain.querySelectorAll<HTMLElement>(TRANSITION_DOM.staggerItems));
  items.forEach((element, index) => {
    animate(
      element,
      { opacity: [0, 1], transform: ["translateY(14px)", "translateY(0px)"] },
      { duration: 0.42, delay: 0.12 + index * 0.05, ease: TRANSITION_EASE },
    );
  });
}

export default function HomePage() {
  const [prompt, setPrompt] = useState("");
  const [currentSessionId, setCurrentSessionId] = useState("");
  const [currentTaskId, setCurrentTaskId] = useState("");
  const [currentPageId, setCurrentPageId] = useState("");
  const [submittedPrompt, setSubmittedPrompt] = useState("");
  const [submittedFileNames, setSubmittedFileNames] = useState<string[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState("");
  const [status, setStatus] = useState<GenerationStatus>("idle");
  const [reasoning, setReasoning] = useState("");
  const [statusText, setStatusText] = useState("描述你想创建的页面");
  const [pageUrl, setPageUrl] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState("");
  const [thinkingExpanded, setThinkingExpanded] = useState(true);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(true);
  const [historyItems, setHistoryItems] = useState<HistoryItem[]>([]);
  const [previewMetrics, setPreviewMetrics] = useState({
    viewportWidth: PREVIEW_VIEWPORT_WIDTH,
    contentHeight: PREVIEW_DEFAULT_HEIGHT,
    scale: 0.5,
  });
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>(createInitialProgressSteps);
  const eventSourceRef = useRef<EventSource | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const previewStageRef = useRef<HTMLDivElement | null>(null);
  const previewIframeRef = useRef<HTMLIFrameElement | null>(null);
  const hasHydratedRef = useRef(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const motionRef = useRef<MotionLib | null>(null);

  const absolutePageUrl = useMemo(() => {
    if (!pageUrl) return "";
    if (pageUrl.startsWith("http://") || pageUrl.startsWith("https://")) {
      return pageUrl;
    }
    return `${window.location.origin}${pageUrl}`;
  }, [pageUrl]);

  useEffect(() => {
    void loadHistory();

    const session = readCurrentSession();
    if (session) {
      applyStoredSession(session);
      if ((session.status === "thinking" || session.status === "creating") && session.taskId) {
        connectToEvents(session.taskId);
      }
    }

    hasHydratedRef.current = true;
    // 这里只在首屏恢复本地会话，避免重连逻辑随状态变化重复执行。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 动态加载 motion（主过渡方案）；失败则保持为空，过渡时自动降级到 View Transitions / 纯 CSS
  useEffect(() => {
    let cancelled = false;
    import("motion")
      .then((mod) => {
        if (!cancelled && typeof mod.animate === "function") {
          motionRef.current = { animate: mod.animate as MotionLib["animate"] };
        }
      })
      .catch(() => {
        motionRef.current = null;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 三处状态切换统一经此入口，套用「motion → View Transitions → 纯 CSS → 直接切换」降级链
  const playTransition = (mutate: () => void) => {
    runStageTransition(stageRef.current, motionRef.current, mutate);
  };

  useEffect(() => {
    if (!hasHydratedRef.current || status === "idle" || !currentSessionId) return;

    const now = new Date().toISOString();
    const session: StoredSession = {
      id: currentSessionId,
      taskId: currentTaskId || undefined,
      pageId: currentPageId || undefined,
      prompt: submittedPrompt,
      fileNames: submittedFileNames,
      status,
      reasoning,
      statusText,
      pageUrl,
      errorMessage,
      progressSteps,
      createdAt: readCurrentSession()?.createdAt ?? now,
      updatedAt: now,
    };

    writeCurrentSession(session);
  }, [
    currentSessionId,
    currentTaskId,
    currentPageId,
    errorMessage,
    pageUrl,
    progressSteps,
    reasoning,
    status,
    statusText,
    submittedFileNames,
    submittedPrompt,
  ]);

  async function loadHistory(): Promise<void> {
    try {
      const response = await fetch("/api/pages");
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const data = (await response.json()) as PageHistoryResponseItem[];
      setHistoryItems(data.map(mapPageHistoryItem));
    } catch {
      setHistoryItems([]);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPrompt = prompt.trim();
    const validationError = validateFiles(selectedFiles);

    if (!trimmedPrompt) {
      setStatusText("先描述你想创建的页面");
      return;
    }

    if (validationError) {
      setFileError(validationError);
      return;
    }

    if (status === "thinking" || status === "creating") {
      return;
    }

    const fileNames = selectedFiles.map((file) => file.name);
    eventSourceRef.current?.close();
    const optimisticSessionId = createClientId();
    const now = new Date().toISOString();
    playTransition(() => {
      setCurrentSessionId(optimisticSessionId);
      setCurrentTaskId("");
      setCurrentPageId("");
      setSubmittedPrompt(trimmedPrompt);
      setSubmittedFileNames(fileNames);
      setStatus("thinking");
      setReasoning("");
      setThinkingExpanded(true);
      setPageUrl("");
      setCopied(false);
      setCopyFeedback("");
      setPreviewMetrics({
        viewportWidth: PREVIEW_VIEWPORT_WIDTH,
        contentHeight: PREVIEW_DEFAULT_HEIGHT,
        scale: 0.5,
      });
      setErrorMessage("");
      setFileError("");
      setStatusText(selectedFiles.length ? "正在上传并解析文件..." : "正在提交你的需求...");
      setProgressSteps(createInitialProgressSteps(fileNames.length > 0));
      setPrompt("");
    });
    writeCurrentSession({
      id: optimisticSessionId,
      prompt: trimmedPrompt,
      fileNames,
      status: "thinking",
      reasoning: "",
      statusText: selectedFiles.length ? "正在上传并解析文件..." : "正在提交你的需求...",
      pageUrl: "",
      errorMessage: "",
      progressSteps: createInitialProgressSteps(fileNames.length > 0),
      createdAt: now,
      updatedAt: now,
    });

    try {
      const formData = new FormData();
      formData.append("prompt", trimmedPrompt);
      selectedFiles.forEach((file) => formData.append("files", file));

      const response = await fetch("/api/generations", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const data = (await response.json()) as CreateGenerationResponse;
      setCurrentSessionId(data.task_id);
      setCurrentTaskId(data.task_id);
      setCurrentPageId(data.page_id);
      void loadHistory();
      if (fileNames.length > 0) {
        setProgressSteps((current) => updateProgressSteps(current, {
          type: "progress",
          step: "upload_file",
          status: "completed",
          text: "文件已上传到服务端",
        }));
      }
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      connectToEvents(data.task_id);
    } catch (error) {
      setStatus("failed");
      setErrorMessage(error instanceof Error ? error.message : "创建生成任务失败");
      setStatusText("生成失败");
    }
  }

  function connectToEvents(taskId: string) {
    eventSourceRef.current?.close();
    const source = new EventSource(`/api/generations/${taskId}/events`);
    eventSourceRef.current = source;

    source.addEventListener("status", (event) => {
      const payload = parsePayload(event);
      if (payload.text) setStatusText(payload.text);
    });

    source.addEventListener("reasoning_delta", (event) => {
      const payload = parsePayload(event);
      if (payload.text) {
        setReasoning((current) => current + payload.text);
        setStatus("thinking");
        setProgressSteps((current) => updateProgressSteps(current, {
          type: "progress",
          step: "model_thinking",
          status: "running",
          text: "模型正在展开思考",
        }));
      }
    });

    source.addEventListener("answer_started", () => {
      setStatus("creating");
      setStatusText("页面创建中...");
      setProgressSteps((current) => updateProgressSteps(current, {
        type: "progress",
        step: "model_thinking",
        status: "completed",
        text: "模型思考完成",
      }));
    });

    source.addEventListener("progress", (event) => {
      const payload = parsePayload(event);
      if (!payload.step || !payload.status) return;

      setProgressSteps((current) => updateProgressSteps(current, payload));
    });

    source.addEventListener("completed", (event) => {
      const payload = parsePayload(event);
      setStatus("completed");
      setStatusText("页面已创建完成");
      setPageUrl(payload.url ?? "");
      if (payload.page_id) setCurrentPageId(payload.page_id);
      void loadHistory();
      setProgressSteps((current) =>
        current.map((step) => ({
          ...step,
          status: step.status === "pending" || step.status === "running" ? "completed" : step.status,
        })),
      );
      source.close();
    });

    source.addEventListener("failed", (event) => {
      const payload = parsePayload(event);
      setStatus("failed");
      setStatusText("生成失败");
      setErrorMessage(payload.message ?? "页面生成失败，请稍后重试。");
      setProgressSteps((current) =>
        current.map((step) => (step.status === "running" ? { ...step, status: "failed" } : step)),
      );
      void loadHistory();
      source.close();
    });

    source.onerror = () => {
      if (status !== "completed") {
        setStatus("failed");
        setStatusText("连接中断");
        setErrorMessage("生成连接中断，请刷新后重试。");
      }
      source.close();
    };
  }

  function startNewChat() {
    eventSourceRef.current?.close();
    localStorage.removeItem(CURRENT_SESSION_KEY);
    if (fileInputRef.current) fileInputRef.current.value = "";
    playTransition(() => {
      setCurrentSessionId("");
      setCurrentTaskId("");
      setCurrentPageId("");
      setPrompt("");
      setSubmittedPrompt("");
      setSubmittedFileNames([]);
      setSelectedFiles([]);
      setFileError("");
      setStatus("idle");
      setReasoning("");
      setThinkingExpanded(true);
      setStatusText("描述你想创建的页面");
      setPageUrl("");
      setErrorMessage("");
      setCopied(false);
      setCopyFeedback("");
      setProgressSteps(createInitialProgressSteps());
      setPreviewMetrics({
        viewportWidth: PREVIEW_VIEWPORT_WIDTH,
        contentHeight: PREVIEW_DEFAULT_HEIGHT,
        scale: 0.5,
      });
    });
  }

  function restoreHistoryItem(item: HistoryItem) {
    const stored = readStoredSession(item.id);
    eventSourceRef.current?.close();

    if (stored) {
      writeCurrentSession(stored);
      playTransition(() => applyStoredSession(stored));

      if ((stored.status === "thinking" || stored.status === "creating") && stored.taskId) {
        connectToEvents(stored.taskId);
      }
      return;
    }

    const session = buildSessionFromHistoryItem(item);
    writeCurrentSession(session);
    playTransition(() => applyStoredSession(session));

    if ((session.status === "thinking" || session.status === "creating") && session.taskId) {
      connectToEvents(session.taskId);
    }
  }

  function renderHistorySidebar() {
    const isOnNewChat = status === "idle" && !currentSessionId;
    return (
      <nav className={`history-sidebar ${isSidebarCollapsed ? "collapsed" : ""}`} aria-label="历史创建">
        <button
          className="sidebar-brand"
          type="button"
          onClick={() => setIsSidebarCollapsed((value) => !value)}
          title={isSidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
          aria-label={isSidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
          aria-expanded={!isSidebarCollapsed}
        >
          <span className="brand-glyph" aria-hidden="true">
            <img src="/stars-page-logo-simple.png" alt="" width={28} height={28} />
          </span>
          {!isSidebarCollapsed && (
            <span className="brand-text">
              <span className="brand-name-cn">星页</span>
              <span className="brand-name-en">StarPage</span>
            </span>
          )}
        </button>

        {!isSidebarCollapsed && <div className="sidebar-section-divider" aria-hidden="true" />}

        <button
          className={`new-chat-button ${isOnNewChat ? "is-active" : ""}`}
          type="button"
          onClick={startNewChat}
          title="新对话"
          aria-label="新对话"
          aria-current={isOnNewChat ? "page" : undefined}
        >
          <span className="sidebar-icon"><PlusIcon /></span>
          {!isSidebarCollapsed && <span className="sidebar-label">新对话</span>}
        </button>
        {isSidebarCollapsed && (
          <button
            className="sidebar-icon-button"
            type="button"
            onClick={() => setIsSidebarCollapsed(false)}
            title="历史创建"
            aria-label="历史创建"
          >
            <span className="sidebar-icon"><HistoryIcon /></span>
          </button>
        )}

        <div className="history-content" aria-hidden={isSidebarCollapsed}>
          <div className="history-title">历史创建</div>
          <div className="history-list">
            {historyItems.length === 0 ? (
              <p className="history-empty">暂无历史</p>
            ) : (
              historyItems.map((item) => (
                <button
                  className={`history-item ${item.id === currentSessionId ? "active" : ""}`}
                  key={item.id}
                  type="button"
                  onClick={() => restoreHistoryItem(item)}
                  aria-current={item.id === currentSessionId ? "page" : undefined}
                >
                  <span>{item.title}</span>
                  <small>{formatHistoryTime(item.updatedAt)}</small>
                </button>
              ))
            )}
          </div>
        </div>
      </nav>
    );
  }

  function applyStoredSession(session: StoredSession) {
    setCurrentSessionId(session.id);
    setCurrentTaskId(session.taskId ?? "");
    setCurrentPageId(session.pageId ?? "");
    setSubmittedPrompt(session.prompt);
    setSubmittedFileNames(session.fileNames ?? []);
    setPrompt("");
    setSelectedFiles([]);
    setFileError("");
    setStatus(session.status);
    setReasoning(session.reasoning);
    setThinkingExpanded(true);
    setStatusText(session.statusText);
    setPageUrl(session.pageUrl);
    setErrorMessage(session.errorMessage);
    setProgressSteps(
      session.progressSteps.length ? session.progressSteps : createInitialProgressSteps((session.fileNames ?? []).length > 0),
    );
    setCopied(false);
    setCopyFeedback("");
  }

  function handleFileChange(files: FileList | null) {
    const nextFiles = Array.from(files ?? []);
    const validationError = validateFiles(nextFiles);

    setSelectedFiles(validationError ? [] : nextFiles);
    setFileError(validationError);
    if (validationError && fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function clearSelectedFiles() {
    setSelectedFiles([]);
    setFileError("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handlePromptChange(event: ChangeEvent<HTMLTextAreaElement>) {
    setPrompt(event.target.value);
    resizePromptTextarea(event.currentTarget);
  }

  async function copyPageUrl() {
    if (!absolutePageUrl) return;

    try {
      await copyTextToClipboard(absolutePageUrl);
      setCopied(true);
      setCopyFeedback("已复制链接");
      window.setTimeout(() => {
        setCopied(false);
        setCopyFeedback("");
      }, 1800);
    } catch {
      setCopied(false);
      setCopyFeedback("复制失败，请手动复制");
      window.setTimeout(() => setCopyFeedback(""), 2400);
    }
  }

  function handlePreviewLoad(event: SyntheticEvent<HTMLIFrameElement>) {
    updatePreviewMetrics(event.currentTarget);
  }

  const isGenerating = status === "thinking" || status === "creating";
  const isLongPrompt = submittedPrompt.length > 260;
  const previewShellStyle = {
    width: `${previewMetrics.viewportWidth * previewMetrics.scale}px`,
    height: `${previewMetrics.contentHeight * previewMetrics.scale}px`,
  };
  const previewIframeStyle = {
    width: `${previewMetrics.viewportWidth}px`,
    height: `${previewMetrics.contentHeight}px`,
    transform: `scale(${previewMetrics.scale})`,
  };

  useEffect(() => {
    if (status !== "completed") return;

    const handleResize = () => {
      if (previewIframeRef.current) {
        updatePreviewMetrics(previewIframeRef.current);
      }
    };

    window.addEventListener("resize", handleResize);
    window.setTimeout(handleResize, 0);

    return () => window.removeEventListener("resize", handleResize);
  }, [status, absolutePageUrl]);

  function updatePreviewMetrics(iframe: HTMLIFrameElement) {
    try {
      const doc = iframe.contentDocument;
      const stage = previewStageRef.current;
      if (!doc || !stage) return;

      const availableWidth = Math.max(stage.clientWidth - 28, 320);
      const scale = Math.min(1, availableWidth / PREVIEW_VIEWPORT_WIDTH);
      const availableHeight = Math.max(stage.clientHeight - 28, PREVIEW_DEFAULT_HEIGHT * scale);
      const contentHeight = Math.max(PREVIEW_DEFAULT_HEIGHT, Math.round(availableHeight / scale));

      setPreviewMetrics({
        viewportWidth: PREVIEW_VIEWPORT_WIDTH,
        contentHeight,
        scale,
      });
    } catch {
      setPreviewMetrics((current) => ({
        ...current,
        scale: Math.min(1, Math.max((previewStageRef.current?.clientWidth ?? 640) - 28, 320) / PREVIEW_VIEWPORT_WIDTH),
      }));
    }
  }

  function handlePresetClick(preset: PromptPreset) {
    if (isGenerating) return;
    setPrompt(preset.prompt);
  }

  function renderPromptForm(compact = false) {
    return (
      <div className={`prompt-form-wrap ${compact ? "compact-wrap" : "hero-wrap"}`}>
        <form className={`prompt-card ${compact ? "compact-prompt" : "hero-prompt"}`} onSubmit={handleSubmit}>
          <textarea
            value={prompt}
            onChange={handlePromptChange}
            placeholder={compact ? "继续描述你想调整的方向…" : "说说你想做的页面，例如「面向客户的产品介绍页」"}
            rows={compact ? 1 : 3}
            disabled={isGenerating}
          />
          <div className="prompt-toolbar">
            <div className="prompt-tool-group">
              <label className="file-upload-button" title="上传文档作为生成参考">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept={ACCEPTED_FILE_TYPES}
                  disabled={isGenerating}
                  onChange={(event) => handleFileChange(event.target.files)}
                />
                <span className="button-icon" aria-hidden="true"><AttachmentIcon /></span>
                上传资料
              </label>
              {selectedFiles.length > 0 ? (
                <div className="selected-files" aria-label="已选择文件">
                  {selectedFiles.map((file, index) => (
                    <span className="selected-file" key={`${file.name}-${file.size}-${index}`}>
                      {file.name} · {formatFileSize(file.size)}
                    </span>
                  ))}
                  <button className="clear-files-button" type="button" onClick={clearSelectedFiles} disabled={isGenerating}>
                    <span className="button-icon" aria-hidden="true"><CloseIcon /></span>
                    清空
                  </button>
                </div>
              ) : !compact ? (
                <span className="file-hint">docx · pptx · xlsx · pdf · txt · md · html，最多 3 个，总计 ≤ 50MB</span>
              ) : null}
            </div>
            <button
              className={`submit-button ${compact ? "is-secondary" : ""} ${isGenerating ? "is-loading" : ""}`}
              type="submit"
              disabled={isGenerating || Boolean(fileError)}
              aria-label={isGenerating ? "正在生成" : compact ? "发送修改" : "创建页面"}
              aria-busy={isGenerating}
            >
              {isGenerating ? (
                <>
                  <span className="button-icon" aria-hidden="true"><SpinnerIcon /></span>
                  生成中
                </>
              ) : compact ? (
                <>
                  发送
                  <span className="button-icon" aria-hidden="true"><SendIcon /></span>
                </>
              ) : (
                <>
                  创建
                  <span className="button-icon" aria-hidden="true"><ArrowUpIcon /></span>
                </>
              )}
            </button>
          </div>
        </form>
        {!compact && (
          <div className="prompt-chip-row" role="list" aria-label="推荐场景" data-anim-stagger>
            {PROMPT_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                role="listitem"
                className="prompt-chip"
                onClick={() => handlePresetClick(preset)}
                disabled={isGenerating}
              >
                <span className="chip-emoji" aria-hidden="true">{preset.emoji}</span>
                {preset.label}
              </button>
            ))}
          </div>
        )}
        {fileError && (
          <div className="prompt-meta-row">
            <span className="file-error">{fileError}</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="app-stage" ref={stageRef}>
      {status === "idle" ? (
      <main key="hero" className={`home-shell ${isSidebarCollapsed ? "sidebar-collapsed" : ""}`}>
        <div className="hero-aurora" aria-hidden="true">
          <span className="aurora-blob aurora-blob-1" />
          <span className="aurora-blob aurora-blob-2" />
          <span className="aurora-blob aurora-blob-3" />
          <span className="aurora-grid" />
        </div>
        {renderHistorySidebar()}
        <section className="page-shell">
          <div className="hero">
            <div className="brand-mark" data-anim-stagger>
              <img
                src="/stars-page-logo-simple.png"
                alt="星页 StarPage"
                className="brand-logo"
                width={56}
                height={56}
              />
            </div>
            <h1 data-anim-stagger>想做什么页面？</h1>
            <p className="subtitle" data-anim-stagger>
              说说你的想法，<strong className="brand-inline">星页 StarPage</strong> 帮你生成一个可分享的精致网页。
            </p>

            {renderPromptForm()}
          </div>
        </section>
      </main>
      ) : (
      <main key="workspace" className="workspace-shell">
      <section className={`workspace-layout ${isSidebarCollapsed ? "sidebar-collapsed" : ""}`}>
        {renderHistorySidebar()}
        <aside className="conversation-pane">
          <div className="conversation-scroll">
            <div className={`chat-message user-message ${isLongPrompt ? "long-message" : ""}`} data-anim-stagger>
              <div className="user-message-meta">
                <span>你的需求</span>
                <span>
                  {submittedPrompt.length} 字
                  {submittedFileNames.length ? ` · ${submittedFileNames.length} 个文件` : ""}
                </span>
              </div>
              <p>{submittedPrompt}</p>
              {submittedFileNames.length > 0 && (
                <div className="submitted-files">
                  {submittedFileNames.map((name) => (
                    <span key={name}>{name}</span>
                  ))}
                </div>
              )}
            </div>

            <div className="chat-message assistant-message progress-message" data-anim-stagger>
              <div className="assistant-label">创建节点</div>
              <div className="progress-list">
                {progressSteps.map((step, index) => (
                  <div
                    className={`progress-item ${step.status} ${index === progressSteps.length - 1 ? "is-last" : ""}`}
                    key={step.id}
                  >
                    <span className="progress-icon" aria-hidden="true">{getProgressIcon(step.status)}</span>
                    <div className="progress-body">
                      <div className="progress-title-row">
                        <strong>{step.title}</strong>
                        {step.id === "model_thinking" && (
                          <button className="node-toggle" type="button" onClick={() => setThinkingExpanded((value) => !value)}>
                            {thinkingExpanded ? "收起" : "展开"}
                          </button>
                        )}
                      </div>
                      <p>{step.description}</p>
                      {step.id === "model_output" && (
                        <span className="token-meta">
                          <span className="token-meta-icon" aria-hidden="true"><BoltIcon /></span>
                          输出 {step.outputTokens ?? 0} tokens
                          {step.tokenSource === "estimated" ? "（估算）" : ""}
                        </span>
                      )}
                      {step.id === "model_thinking" && thinkingExpanded && (
                        <div className="thinking-node-body">
                          {reasoning ? (
                            <pre>{reasoning}</pre>
                          ) : (
                            <p>模型开始思考后，会在这里展示 reasoning_content。</p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {status === "failed" && (
              <div className="chat-message assistant-message error-message">
                <div className="assistant-label">生成失败</div>
                <p>{errorMessage}</p>
              </div>
            )}
          </div>

          <div className="composer-wrap">{renderPromptForm(true)}</div>
        </aside>

        <section className="preview-pane">
          <article className="panel preview-panel workspace-preview" data-anim-stagger>
            <div className="panel-heading">
              <span
                className={`dot ${status === "completed" ? "ready" : status === "failed" ? "failed" : "loading"}`}
                aria-hidden="true"
              />
              <h2>页面预览</h2>
              <span className="preview-status-text">
                {status === "completed" ? "已生成" : status === "failed" ? "生成失败" : "生成中"}
              </span>
            </div>

            {status === "completed" && absolutePageUrl && (
              <>
                <div className="preview-window">
                  <div className="preview-window-bar" aria-hidden="true">
                    <span className="win-dots">
                      <span className="win-dot win-dot-red" />
                      <span className="win-dot win-dot-amber" />
                      <span className="win-dot win-dot-green" />
                    </span>
                    <span className="preview-window-url">{absolutePageUrl}</span>
                  </div>
                  <div className="preview-viewport" ref={previewStageRef}>
                    <div className="preview-scale-shell" style={previewShellStyle}>
                      <iframe
                        ref={previewIframeRef}
                        title="生成页面预览"
                        src={absolutePageUrl}
                        onLoad={handlePreviewLoad}
                        style={previewIframeStyle}
                      />
                    </div>
                  </div>
                </div>
                <div className="link-actions">
                  <a href={absolutePageUrl} target="_blank" rel="noreferrer">
                    打开页面
                  </a>
                  <button className={copied ? "copied" : ""} type="button" onClick={copyPageUrl}>
                    {copied ? "复制成功" : copyFeedback || "复制链接"}
                  </button>
                </div>
              </>
            )}

            {(status === "thinking" || status === "creating") && (
              <div className="preview-empty">
                <div className="preview-skeleton" aria-hidden="true">
                  <div className="skeleton-bar skeleton-topbar">
                    <span className="skeleton-chip" />
                    <span className="skeleton-chip" />
                    <span className="skeleton-chip" />
                  </div>
                  <div className="skeleton-hero">
                    <span className="skeleton-line skeleton-line-lg" />
                    <span className="skeleton-line skeleton-line-md" />
                    <span className="skeleton-line skeleton-line-sm" />
                  </div>
                  <div className="skeleton-cards">
                    <span className="skeleton-card" />
                    <span className="skeleton-card" />
                    <span className="skeleton-card" />
                  </div>
                </div>
                <h3>正在为你生成页面…</h3>
                <p>左侧实时展示模型思考与创建节点，完成后这里会渲染最终网页。</p>
              </div>
            )}

            {status === "failed" && <p className="error-text">{errorMessage}</p>}
          </article>
        </section>
      </section>
    </main>
      )}
    </div>
  );
}

function readCurrentSession(): StoredSession | null {
  return readJson<StoredSession>(CURRENT_SESSION_KEY);
}

function writeCurrentSession(session: StoredSession): void {
  localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify(session));
  localStorage.setItem(`${CURRENT_SESSION_KEY}:${session.id}`, JSON.stringify(session));
}

function readStoredSession(id: string): StoredSession | null {
  return readJson<StoredSession>(`${CURRENT_SESSION_KEY}:${id}`);
}

function readJson<T>(key: string): T | null {
  try {
    const rawValue = localStorage.getItem(key);
    return rawValue ? (JSON.parse(rawValue) as T) : null;
  } catch {
    return null;
  }
}

function buildHistoryTitle(prompt: string): string {
  const normalized = prompt.replace(/\s+/g, " ").trim();
  return normalized.length > 22 ? `${normalized.slice(0, 22)}...` : normalized || "未命名页面";
}

function mapPageHistoryItem(item: PageHistoryResponseItem): HistoryItem {
  const taskId = item.task_id ?? undefined;
  const pageId = item.id;
  return {
    id: taskId ?? pageId,
    taskId,
    pageId,
    title: item.title || buildHistoryTitle(item.prompt),
    prompt: item.prompt,
    fileNames: item.file_names ?? [],
    pageUrl: item.page_url,
    status: mapHistoryStatus(item.page_status, item.generation_status),
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

function mapHistoryStatus(pageStatus: string, generationStatus?: string | null): GenerationStatus {
  if (pageStatus === "ready" || generationStatus === "succeeded") return "completed";
  if (pageStatus === "failed" || generationStatus === "failed" || generationStatus === "cancelled") return "failed";
  if (pageStatus === "generating" || generationStatus === "pending" || generationStatus === "running") return "thinking";
  return "idle";
}

function buildSessionFromHistoryItem(item: HistoryItem): StoredSession {
  const statusText = getStoredStatusText(item.status);
  return {
    id: item.id,
    taskId: item.taskId,
    pageId: item.pageId,
    prompt: item.prompt,
    fileNames: item.fileNames,
    status: item.status,
    reasoning: "",
    statusText,
    pageUrl: item.status === "completed" ? item.pageUrl : "",
    errorMessage: item.status === "failed" ? "页面生成失败，请重新创建。" : "",
    progressSteps: createInitialProgressSteps(item.fileNames.length > 0).map((step) => ({
      ...step,
      status: item.status === "completed" ? "completed" : step.status,
    })),
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
  };
}

function getStoredStatusText(status: GenerationStatus): string {
  if (status === "completed") return "页面已创建完成";
  if (status === "failed") return "生成失败";
  if (status === "thinking" || status === "creating") return "正在恢复生成进度...";
  return "描述你想创建的页面";
}

function formatHistoryTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function createClientId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }

  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function updateProgressSteps(current: ProgressStep[], payload: SsePayload): ProgressStep[] {
  if (!payload.step || !payload.status) return current;

  const nextStep = (step: ProgressStep): ProgressStep => ({
    ...step,
    status: payload.status ?? step.status,
    description: payload.text ?? step.description,
    outputTokens: payload.output_tokens !== undefined ? payload.output_tokens : step.outputTokens,
    tokenSource: payload.token_source ?? step.tokenSource,
  });

  if (current.some((step) => step.id === payload.step)) {
    return current.map((step) => (step.id === payload.step ? nextStep(step) : step));
  }

  const insertedStep = nextStep(createProgressStep(payload.step));
  const modelIndex = current.findIndex((step) => step.id === "model_output");
  if (payload.step === "compress_document" && modelIndex >= 0) {
    return [...current.slice(0, modelIndex), insertedStep, ...current.slice(modelIndex)];
  }
  return [insertedStep, ...current];
}

function parsePayload(event: Event): SsePayload {
  const message = event as MessageEvent<string>;
  return JSON.parse(message.data) as SsePayload;
}

function getProgressIcon(status: ProgressStepStatus): ReactNode {
  if (status === "completed") return "✓";
  if (status === "running") return <span className="progress-spinner" aria-hidden="true" />;
  if (status === "failed") return "!";
  return null;
}

function validateFiles(files: File[]): string {
  if (files.length > MAX_FILE_COUNT) {
    return `当前一次最多上传 ${MAX_FILE_COUNT} 个文件`;
  }

  let totalSize = 0;
  for (const file of files) {
    const extension = getFileExtension(file.name);
    if (!ACCEPTED_FILE_EXTENSIONS.includes(extension)) {
      return `${file.name} 的格式暂不支持，请上传 docx、pptx、xlsx、xls、pdf、txt、md 或 html 文件`;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return `${file.name} 超过 50MB，请压缩或拆分后再上传`;
    }
    totalSize += file.size;
    if (totalSize > MAX_TOTAL_FILE_SIZE_BYTES) {
      return "本次上传文件总大小超过 50MB，请减少文件大小";
    }
  }

  return "";
}

function getFileExtension(filename: string): string {
  const dotIndex = filename.lastIndexOf(".");
  return dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : "";
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function resizePromptTextarea(textarea: HTMLTextAreaElement): void {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
  } catch {
    return "创建生成任务失败";
  }

  return "创建生成任务失败";
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  const success = document.execCommand("copy");
  document.body.removeChild(textarea);

  if (!success) {
    throw new Error("复制失败");
  }
}
