"use client";

import type { ChangeEvent, FormEvent, KeyboardEvent, MouseEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

type GenerationStatus = "idle" | "thinking" | "creating" | "completed" | "failed";

type ModelInfo = {
  key: string;
  label: string;
  provider: string;
  is_default: boolean;
  available: boolean;
};

type CreateGenerationResponse = {
  conversation_id: string;
  batch_id: string;
  kind: string;
  runs: GenerationRunResponse[];
};

type GenerationRunResponse = {
  task_id: string;
  page_id: string;
  model_key: string;
  model_label: string;
  page_url: string;
  status: string;
};

type SsePayload = {
  type: string;
  text?: string;
  page_id?: string;
  url?: string;
  message?: string;
  model_key?: string;
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

/* 单个模型 run 的前端状态：每个模型一份独立的思考/进度/预览。 */
type RunState = {
  taskId: string;
  pageId: string;
  modelKey: string;
  modelLabel: string;
  status: GenerationStatus;
  reasoning: string;
  statusText: string;
  pageUrl: string;
  errorMessage: string;
  progressSteps: ProgressStep[];
};

type StoredSession = {
  conversationId: string;
  batchId: string;
  title: string;
  prompt: string;
  fileNames: string[];
  selectedModelKeys: string[];
  runs: RunState[];
  roundIndex: number;
  basePageId?: string;
  baseModelLabel?: string;
  createdAt: string;
  updatedAt: string;
};

type HistoryItem = {
  id: string;
  title: string;
  isFavorite: boolean;
  modelKeys: string[];
  nodeCount: number;
  status: GenerationStatus;
  updatedAt: string;
};

type HistoryScope = "all" | "favorite";

type ConversationListResponseItem = {
  id: string;
  title: string;
  origin: string;
  is_favorite: boolean;
  model_keys: string[];
  node_count: number;
  latest_batch_status?: string | null;
  created_at: string;
  updated_at: string;
};

type ConversationNodeResponse = {
  page_id: string;
  task_id?: string | null;
  model_key?: string | null;
  model_label?: string | null;
  model_name?: string | null;
  parent_page_id?: string | null;
  page_status: string;
  generation_status?: string | null;
  page_url: string;
};

type ConversationBatchResponse = {
  batch_id: string;
  kind: string;
  base_page_id?: string | null;
  selected_models: string[];
  status: string;
  prompt: string;
  user_prompt?: string | null;
  file_names: string[];
  created_at: string;
  nodes: ConversationNodeResponse[];
};

type ConversationDetailResponse = {
  id: string;
  title: string;
  origin: string;
  created_at: string;
  updated_at: string;
  batches: ConversationBatchResponse[];
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
    ...(hasFile ? [createProgressStep("upload_file", "completed"), createProgressStep("parse_file"), createProgressStep("compress_document")] : []),
    createProgressStep("model_thinking"),
    createProgressStep("model_output"),
    createProgressStep("deploy"),
  ];
}

const PREVIEW_VIEWPORT_WIDTH = 1200;
const PREVIEW_DEFAULT_HEIGHT = 900;
const CURRENT_SESSION_KEY = "star-page-current-session";
const SELECTED_MODELS_KEY = "star-page-selected-models";
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

const SearchIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="11" cy="11" r="7" />
    <line x1="16.5" y1="16.5" x2="21" y2="21" />
  </svg>
);

const StarIcon = ({ filled = false }: { filled?: boolean }) => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <polygon points="12 2 15.1 8.4 22 9.3 17 14.2 18.2 21 12 17.8 5.8 21 7 14.2 2 9.3 8.9 8.4 12 2" />
  </svg>
);

const TrashIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <polyline points="3 6 5 6 21 6" />
    <path d="M8 6V4h8v2" />
    <path d="M19 6l-1 14H6L5 6" />
    <line x1="10" y1="11" x2="10" y2="17" />
    <line x1="14" y1="11" x2="14" y2="17" />
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

/* 分支：从某个结果"继续生成"，呼应生成树的分支语义 */
const BranchIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="6" cy="6" r="2.4" />
    <circle cx="6" cy="18" r="2.4" />
    <circle cx="18" cy="9" r="2.4" />
    <path d="M6 8.4v7.2" />
    <path d="M18 11.4c0 3-3 3.6-6 3.6" />
    <path d="M15.8 7l2.2 2 2.2-2" transform="translate(-2.2 -1)" />
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
   见 wiki/frontend-home-workspace-transition.md。
   ========================================================================== */
type MotionLib = {
  animate: (
    target: Element | Element[],
    keyframes: Record<string, unknown>,
    options?: Record<string, unknown>,
  ) => { finished: Promise<unknown> };
};

const TRANSITION_EASE = [0.22, 1, 0.36, 1] as const;

const TRANSITION_DOM = {
  sharedCard: ".prompt-card",
  promptText: ".prompt-card textarea",
  bubbleTarget: ".user-message p",
  staggerItems: "[data-anim-stagger]",
} as const;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

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

  if (overlay) {
    const removeOverlay = () => overlay.remove();
    animate(
      overlay,
      { opacity: [1, 0], transform: ["translateY(0px)", "translateY(-10px)"] },
      { duration: 0.28, ease: [0.4, 0, 0.2, 1] },
    ).finished.then(removeOverlay, removeOverlay);
  }

  animate(newMain, { opacity: [0, 1] }, { duration: 0.36, delay: 0.05 });

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

  const items = Array.from(newMain.querySelectorAll<HTMLElement>(TRANSITION_DOM.staggerItems));
  items.forEach((element, index) => {
    animate(
      element,
      { opacity: [0, 1], transform: ["translateY(14px)", "translateY(0px)"] },
      { duration: 0.42, delay: 0.12 + index * 0.05, ease: TRANSITION_EASE },
    );
  });
}

/* ==========================================================================
   预览单元：每个模型结果一个"浏览器视窗"，自管缩放（固定 1200px 视口 → 按单元宽度 scale）。
   ========================================================================== */
function PreviewCell({
  run,
  multi,
  focused,
  onToggleFocus,
  onContinue,
  canContinue,
}: {
  run: RunState;
  multi: boolean;
  focused: boolean;
  onToggleFocus: () => void;
  onContinue: () => void;
  canContinue: boolean;
}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [metrics, setMetrics] = useState({
    viewportWidth: PREVIEW_VIEWPORT_WIDTH,
    contentHeight: PREVIEW_DEFAULT_HEIGHT,
    scale: 0.5,
  });
  const [copied, setCopied] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState("");

  const absoluteUrl = useMemo(() => toAbsoluteUrl(run.pageUrl), [run.pageUrl]);

  function recomputeMetrics() {
    const stage = stageRef.current;
    if (!stage) return;

    const availableWidth = Math.max(stage.clientWidth - 28, 280);
    const scale = Math.min(1, availableWidth / PREVIEW_VIEWPORT_WIDTH);
    const availableHeight = Math.max(stage.clientHeight - 28, PREVIEW_DEFAULT_HEIGHT * scale);
    const contentHeight = Math.max(PREVIEW_DEFAULT_HEIGHT, Math.round(availableHeight / scale));
    setMetrics({ viewportWidth: PREVIEW_VIEWPORT_WIDTH, contentHeight, scale });
  }

  useEffect(() => {
    if (run.status !== "completed") return;

    const handleResize = () => recomputeMetrics();
    window.addEventListener("resize", handleResize);
    const timer = window.setTimeout(handleResize, 0);
    return () => {
      window.removeEventListener("resize", handleResize);
      window.clearTimeout(timer);
    };
  }, [run.status, absoluteUrl, focused]);

  function handlePreviewLoad() {
    recomputeMetrics();
  }

  async function copyUrl() {
    if (!absoluteUrl) return;
    try {
      await copyTextToClipboard(absoluteUrl);
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

  const shellStyle = {
    width: `${metrics.viewportWidth * metrics.scale}px`,
    height: `${metrics.contentHeight * metrics.scale}px`,
  };
  const iframeStyle = {
    width: `${metrics.viewportWidth}px`,
    height: `${metrics.contentHeight}px`,
    transform: `scale(${metrics.scale})`,
  };

  const statusLabel =
    run.status === "completed" ? "已生成" : run.status === "failed" ? "生成失败" : run.status === "creating" ? "创建中" : "思考中";

  return (
    <article className={`preview-cell ${focused ? "is-focused" : ""}`}>
      <header className="preview-cell-head">
        <span
          className={`dot ${run.status === "completed" ? "ready" : run.status === "failed" ? "failed" : "loading"}`}
          aria-hidden="true"
        />
        <strong className="preview-cell-model" title={run.modelLabel}>{run.modelLabel}</strong>
        <span className={`run-status-chip is-${run.status}`}>{statusLabel}</span>
        {multi && (
          <button className="cell-action focus-toggle" type="button" onClick={onToggleFocus}>
            {focused ? "退出聚焦" : "聚焦"}
          </button>
        )}
      </header>

      {run.status === "completed" && absoluteUrl ? (
        <>
          <div className="preview-window">
            <div className="preview-window-bar" aria-hidden="true">
              <span className="win-dots">
                <span className="win-dot win-dot-red" />
                <span className="win-dot win-dot-amber" />
                <span className="win-dot win-dot-green" />
              </span>
              <span className="preview-window-url">{absoluteUrl}</span>
            </div>
            <div className="preview-viewport" ref={stageRef}>
              <div className="preview-scale-shell" style={shellStyle}>
                <iframe
                  ref={iframeRef}
                  title={`${run.modelLabel} 生成页面预览`}
                  src={absoluteUrl}
                  onLoad={handlePreviewLoad}
                  sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
                  style={iframeStyle}
                />
              </div>
            </div>
          </div>
          <div className="link-actions">
            <a href={absoluteUrl} target="_blank" rel="noreferrer">
              打开页面
            </a>
            <button className={copied ? "copied" : ""} type="button" onClick={copyUrl}>
              {copied ? "复制成功" : copyFeedback || "复制链接"}
            </button>
            {canContinue && (
              <button className="continue-button" type="button" onClick={onContinue}>
                <span className="button-icon" aria-hidden="true"><BranchIcon /></span>
                以此结果继续
              </button>
            )}
          </div>
        </>
      ) : run.status === "failed" ? (
        <div className="preview-cell-error">
          <p>{run.errorMessage || "页面生成失败，请稍后重试。"}</p>
        </div>
      ) : (
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
          <p className="preview-cell-hint">{run.statusText || "正在生成…"}</p>
        </div>
      )}
    </article>
  );
}

export default function HomePage() {
  const [prompt, setPrompt] = useState("");
  const [phase, setPhase] = useState<"idle" | "active">("idle");
  const [conversationId, setConversationId] = useState("");
  const [batchId, setBatchId] = useState("");
  const [submittedPrompt, setSubmittedPrompt] = useState("");
  const [submittedFileNames, setSubmittedFileNames] = useState<string[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState("");
  const [runs, setRuns] = useState<RunState[]>([]);
  const [activeModelKey, setActiveModelKey] = useState("");
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);
  const [thinkingExpanded, setThinkingExpanded] = useState(true);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(true);
  const [historyItems, setHistoryItems] = useState<HistoryItem[]>([]);
  const [historyScope, setHistoryScope] = useState<HistoryScope>("all");
  const [historySearch, setHistorySearch] = useState("");
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [selectedModelKeys, setSelectedModelKeys] = useState<string[]>([]);
  const [roundIndex, setRoundIndex] = useState(0);
  const [continueBase, setContinueBase] = useState<{ pageId: string; modelLabel: string } | null>(null);

  const eventSourcesRef = useRef<EventSource[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const hasHydratedRef = useRef(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const motionRef = useRef<MotionLib | null>(null);

  useEffect(() => {
    void loadHistory();
    void loadModels();

    const session = readCurrentSession();
    if (session && session.runs?.length) {
      applyStoredSession(session);
      session.runs.forEach((run) => {
        if ((run.status === "thinking" || run.status === "creating") && run.taskId) {
          connectRun(run.taskId);
        }
      });
    }

    hasHydratedRef.current = true;
    // 仅首屏恢复本地会话与模型列表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const playTransition = (mutate: () => void) => {
    runStageTransition(stageRef.current, motionRef.current, mutate);
  };

  useEffect(() => {
    if (!hasHydratedRef.current || phase === "idle" || !conversationId) return;

    const now = new Date().toISOString();
    const session: StoredSession = {
      conversationId,
      batchId,
      title: buildHistoryTitle(submittedPrompt),
      prompt: submittedPrompt,
      fileNames: submittedFileNames,
      selectedModelKeys,
      runs,
      roundIndex,
      basePageId: continueBase?.pageId,
      baseModelLabel: continueBase?.modelLabel,
      createdAt: readCurrentSession()?.createdAt ?? now,
      updatedAt: now,
    };
    writeCurrentSession(session);
  }, [phase, conversationId, batchId, runs, submittedPrompt, submittedFileNames, selectedModelKeys, roundIndex, continueBase]);

  useEffect(() => {
    return () => closeAllSources();
  }, []);

  async function loadHistory(scope = historyScope, search = historySearch): Promise<void> {
    const params = new URLSearchParams();
    if (scope === "favorite") params.set("favorite_only", "true");
    const keyword = search.trim();
    if (keyword) params.set("q", keyword);
    const query = params.toString();

    setIsHistoryLoading(true);
    try {
      const response = await fetch(`/api/conversations${query ? `?${query}` : ""}`);
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const data = (await response.json()) as ConversationListResponseItem[];
      setHistoryItems(data.map(mapConversationHistoryItem));
    } catch {
      setHistoryItems([]);
    } finally {
      setIsHistoryLoading(false);
    }
  }

  async function loadModels(): Promise<void> {
    try {
      const response = await fetch("/api/models");
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const data = (await response.json()) as ModelInfo[];
      setAvailableModels(data);

      const stored = readSelectedModels();
      const availableKeys = new Set(data.filter((model) => model.available).map((model) => model.key));
      const restored = (stored ?? []).filter((key) => availableKeys.has(key));
      if (restored.length) {
        setSelectedModelKeys(restored);
        return;
      }
      const defaults = data.filter((model) => model.available && model.is_default).map((model) => model.key);
      if (defaults.length) {
        setSelectedModelKeys(defaults);
        return;
      }
      const firstAvailable = data.find((model) => model.available);
      if (firstAvailable) setSelectedModelKeys([firstAvailable.key]);
    } catch {
      setAvailableModels([]);
    }
  }

  function closeAllSources() {
    eventSourcesRef.current.forEach((source) => source.close());
    eventSourcesRef.current = [];
  }

  function toggleModel(key: string) {
    setSelectedModelKeys((current) => {
      const next = current.includes(key) ? current.filter((item) => item !== key) : [...current, key];
      const ordered = availableModels.filter((model) => next.includes(model.key)).map((model) => model.key);
      const result = ordered.length ? ordered : current; // 至少保留一个
      writeSelectedModels(result);
      return result;
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPrompt = prompt.trim();
    const validationError = validateFiles(selectedFiles);

    if (!trimmedPrompt) {
      return;
    }
    if (validationError) {
      setFileError(validationError);
      return;
    }
    if (selectedModelKeys.length === 0) {
      setFileError("请至少选择一个模型");
      return;
    }
    if (isGenerating) {
      return;
    }

    // 在工作区里提交即"在本会话内续写"：
    //  - 选中了某个结果节点(continueBase) -> 以该节点为基分支；
    //  - 未选中 -> 每个模型各自接上自己上一轮结果，并行继续。
    // 首页(idle)提交则新建会话。
    const inConversation = phase === "active" && Boolean(conversationId);
    const fileNames = selectedFiles.map((file) => file.name);
    const hasFile = fileNames.length > 0;
    closeAllSources();

    const pendingRuns: RunState[] = selectedModelKeys.map((key) => makePendingRun(key, labelForModel(key, availableModels), hasFile));

    playTransition(() => {
      setPhase("active");
      setSubmittedPrompt(trimmedPrompt);
      setSubmittedFileNames(fileNames);
      setRuns(pendingRuns);
      setActiveModelKey(pendingRuns[0]?.modelKey ?? "");
      setFocusedTaskId(null);
      setThinkingExpanded(true);
      setFileError("");
      setPrompt("");
    });

    try {
      const formData = new FormData();
      formData.append("prompt", trimmedPrompt);
      selectedFiles.forEach((file) => formData.append("files", file));
      selectedModelKeys.forEach((key) => formData.append("models", key));
      if (inConversation) {
        formData.append("conversation_id", conversationId);
        if (continueBase) formData.append("base_page_id", continueBase.pageId);
      }

      const response = await fetch("/api/generations", { method: "POST", body: formData });
      if (!response.ok) throw new Error(await readErrorMessage(response));

      const data = (await response.json()) as CreateGenerationResponse;
      setConversationId(data.conversation_id);
      setBatchId(data.batch_id);
      if (inConversation) setRoundIndex((value) => value + 1);
      setContinueBase(null);
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";

      const nextRuns = data.runs.map((run) => makeRunFromResponse(run, hasFile));
      setRuns(nextRuns);
      setActiveModelKey(nextRuns[0]?.modelKey ?? "");
      void loadHistory();

      nextRuns.forEach((run) => connectRun(run.taskId));
    } catch (error) {
      const message = error instanceof Error ? error.message : "创建生成任务失败";
      setRuns((current) =>
        current.map((run) => ({ ...run, status: "failed", statusText: "生成失败", errorMessage: message })),
      );
    }
  }

  function connectRun(taskId: string) {
    const source = new EventSource(`/api/generations/${taskId}/events`);
    eventSourcesRef.current.push(source);

    const patch = (updater: (run: RunState) => RunState) => {
      setRuns((current) => current.map((run) => (run.taskId === taskId ? updater(run) : run)));
    };

    source.addEventListener("status", (event) => {
      const payload = parsePayload(event);
      if (payload.text) patch((run) => ({ ...run, statusText: payload.text ?? run.statusText }));
    });

    source.addEventListener("reasoning_delta", (event) => {
      const payload = parsePayload(event);
      if (payload.text) {
        patch((run) => ({
          ...run,
          status: "thinking",
          reasoning: run.reasoning + payload.text,
          progressSteps: updateProgressSteps(run.progressSteps, {
            type: "progress",
            step: "model_thinking",
            status: "running",
            text: "模型正在展开思考",
          }),
        }));
      }
    });

    source.addEventListener("answer_started", () => {
      patch((run) => ({
        ...run,
        status: "creating",
        statusText: "页面创建中...",
        progressSteps: updateProgressSteps(run.progressSteps, {
          type: "progress",
          step: "model_thinking",
          status: "completed",
          text: "模型思考完成",
        }),
      }));
    });

    source.addEventListener("progress", (event) => {
      const payload = parsePayload(event);
      if (!payload.step || !payload.status) return;
      patch((run) => ({ ...run, progressSteps: updateProgressSteps(run.progressSteps, payload) }));
    });

    source.addEventListener("completed", (event) => {
      const payload = parsePayload(event);
      patch((run) => ({
        ...run,
        status: "completed",
        statusText: "页面已创建完成",
        pageUrl: payload.url ?? run.pageUrl,
        pageId: payload.page_id ?? run.pageId,
        progressSteps: run.progressSteps.map((step) => ({
          ...step,
          status: step.status === "pending" || step.status === "running" ? "completed" : step.status,
        })),
      }));
      void loadHistory();
      source.close();
    });

    source.addEventListener("failed", (event) => {
      const payload = parsePayload(event);
      patch((run) => ({
        ...run,
        status: "failed",
        statusText: "生成失败",
        errorMessage: payload.message ?? "页面生成失败，请稍后重试。",
        progressSteps: run.progressSteps.map((step) => (step.status === "running" ? { ...step, status: "failed" } : step)),
      }));
      void loadHistory();
      source.close();
    });

    source.onerror = () => {
      patch((run) =>
        run.status === "completed"
          ? run
          : { ...run, status: "failed", statusText: "连接中断", errorMessage: "生成连接中断，请刷新后重试。" },
      );
      source.close();
    };
  }

  function startNewChat() {
    closeAllSources();
    localStorage.removeItem(CURRENT_SESSION_KEY);
    if (fileInputRef.current) fileInputRef.current.value = "";
    playTransition(() => {
      setPhase("idle");
      setConversationId("");
      setBatchId("");
      setPrompt("");
      setSubmittedPrompt("");
      setSubmittedFileNames([]);
      setSelectedFiles([]);
      setFileError("");
      setRuns([]);
      setActiveModelKey("");
      setFocusedTaskId(null);
      setThinkingExpanded(true);
      setRoundIndex(0);
      setContinueBase(null);
    });
  }

  async function restoreHistoryItem(item: HistoryItem) {
    closeAllSources();
    try {
      const response = await fetch(`/api/conversations/${item.id}`);
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const detail = (await response.json()) as ConversationDetailResponse;
      const session = buildSessionFromDetail(detail);
      writeCurrentSession(session);
      playTransition(() => applyStoredSession(session));
      session.runs.forEach((run) => {
        if ((run.status === "thinking" || run.status === "creating") && run.taskId) {
          connectRun(run.taskId);
        }
      });
    } catch {
      // 恢复失败时保持当前视图。
    }
  }

  function switchHistoryScope(scope: HistoryScope) {
    setHistoryScope(scope);
    if (isSidebarCollapsed) setIsSidebarCollapsed(false);
    void loadHistory(scope, historySearch);
  }

  function handleHistorySearchChange(event: ChangeEvent<HTMLInputElement>) {
    const nextValue = event.target.value;
    setHistorySearch(nextValue);
    void loadHistory(historyScope, nextValue);
  }

  function clearHistorySearch() {
    setHistorySearch("");
    void loadHistory(historyScope, "");
  }

  function handleHistoryItemKeyDown(event: KeyboardEvent<HTMLDivElement>, item: HistoryItem) {
    if (event.target !== event.currentTarget) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    void restoreHistoryItem(item);
  }

  async function toggleHistoryFavorite(event: MouseEvent<HTMLButtonElement>, item: HistoryItem) {
    event.preventDefault();
    event.stopPropagation();
    const nextFavorite = !item.isFavorite;
    setHistoryItems((current) =>
      current
        .map((historyItem) => (historyItem.id === item.id ? { ...historyItem, isFavorite: nextFavorite } : historyItem))
        .filter((historyItem) => historyScope !== "favorite" || historyItem.isFavorite),
    );

    try {
      const response = await fetch(`/api/conversations/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_favorite: nextFavorite }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const data = (await response.json()) as ConversationListResponseItem;
      const updatedItem = mapConversationHistoryItem(data);
      setHistoryItems((current) =>
        current
          .map((historyItem) => (historyItem.id === updatedItem.id ? updatedItem : historyItem))
          .filter((historyItem) => historyScope !== "favorite" || historyItem.isFavorite),
      );
    } catch {
      void loadHistory(historyScope, historySearch);
    }
  }

  async function deleteHistoryItem(event: MouseEvent<HTMLButtonElement>, item: HistoryItem) {
    event.preventDefault();
    event.stopPropagation();
    if (!window.confirm("删除后这条历史记录将不再显示，确认删除？")) return;

    try {
      const response = await fetch(`/api/conversations/${item.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      setHistoryItems((current) => current.filter((historyItem) => historyItem.id !== item.id));
      if (item.id === conversationId) {
        startNewChat();
      }
    } catch {
      void loadHistory(historyScope, historySearch);
    }
  }

  function applyStoredSession(session: StoredSession) {
    setPhase("active");
    setConversationId(session.conversationId);
    setBatchId(session.batchId);
    setSubmittedPrompt(session.prompt);
    setSubmittedFileNames(session.fileNames ?? []);
    setRuns(session.runs ?? []);
    setActiveModelKey(session.runs?.[0]?.modelKey ?? "");
    setFocusedTaskId(null);
    setSelectedFiles([]);
    setFileError("");
    setPrompt("");
    setThinkingExpanded(true);
    setRoundIndex(session.roundIndex ?? 0);
    setContinueBase(
      session.basePageId ? { pageId: session.basePageId, modelLabel: session.baseModelLabel ?? "上一结果" } : null,
    );
    if (session.selectedModelKeys?.length) setSelectedModelKeys(session.selectedModelKeys);
  }

  function startContinueFrom(run: RunState) {
    setContinueBase({ pageId: run.pageId, modelLabel: run.modelLabel });
    setPrompt("");
    window.requestAnimationFrame(() => {
      const textarea = document.querySelector<HTMLTextAreaElement>(".composer-wrap .prompt-card textarea");
      textarea?.focus();
    });
  }

  function handleFileChange(files: FileList | null) {
    const nextFiles = Array.from(files ?? []);
    const validationError = validateFiles(nextFiles);
    setSelectedFiles(validationError ? [] : nextFiles);
    setFileError(validationError);
    if (validationError && fileInputRef.current) fileInputRef.current.value = "";
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

  function handlePresetClick(preset: PromptPreset) {
    if (isGenerating) return;
    setPrompt(preset.prompt);
  }

  const isGenerating = runs.some((run) => run.status === "thinking" || run.status === "creating");
  const isLongPrompt = submittedPrompt.length > 260;
  const overallStatus = computeOverallStatus(runs);
  const isMulti = runs.length > 1;
  const visibleRuns = focusedTaskId ? runs.filter((run) => run.taskId === focusedTaskId) : runs;
  const activeRun = runs.find((run) => run.modelKey === activeModelKey) ?? runs[0] ?? null;
  const previewGridStyle = {
    gridTemplateColumns: focusedTaskId ? "1fr" : `repeat(${Math.min(Math.max(runs.length, 1), 2)}, minmax(0, 1fr))`,
  };
  const availableSelectableModels = availableModels.filter((model) => model.available);

  function renderModelPicker() {
    if (availableModels.length === 0) return null;
    return (
      <div className="model-picker" role="group" aria-label="选择生成模型" data-anim-stagger>
        <span className="model-picker-label">并行模型</span>
        <div className="model-picker-options">
          {availableModels.map((model) => {
            const active = selectedModelKeys.includes(model.key);
            return (
              <button
                key={model.key}
                type="button"
                className={`model-option ${active ? "is-active" : ""}`}
                onClick={() => model.available && toggleModel(model.key)}
                disabled={!model.available || isGenerating}
                aria-pressed={active}
                title={model.available ? model.label : `${model.label}（未配置密钥，暂不可用）`}
              >
                <span className="model-check" aria-hidden="true">{active ? "✓" : ""}</span>
                {model.label}
                {!model.available && <span className="model-unavailable">未配置</span>}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  function renderHistorySidebar() {
    const isOnNewChat = phase === "idle" && !conversationId;
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
        <button
          className={`favorite-filter-button ${historyScope === "favorite" ? "is-active" : ""}`}
          type="button"
          onClick={() => switchHistoryScope(historyScope === "favorite" ? "all" : "favorite")}
          title={historyScope === "favorite" ? "显示全部历史" : "只看收藏"}
          aria-label={historyScope === "favorite" ? "显示全部历史" : "只看收藏"}
          aria-pressed={historyScope === "favorite"}
        >
          <span className="sidebar-icon"><StarIcon filled={historyScope === "favorite"} /></span>
          {!isSidebarCollapsed && <span className="sidebar-label">收藏</span>}
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
          <div className="history-title-row">
            <div className="history-title">{historyScope === "favorite" ? "收藏记录" : "历史创建"}</div>
            {historySearch && (
              <button className="history-clear-search" type="button" onClick={clearHistorySearch} aria-label="清空检索">
                <CloseIcon />
              </button>
            )}
          </div>
          <label className="history-search">
            <span className="history-search-icon"><SearchIcon /></span>
            <input
              type="search"
              value={historySearch}
              onChange={handleHistorySearchChange}
              placeholder={historyScope === "favorite" ? "检索收藏记录" : "检索历史记录"}
            />
          </label>
          <div className="history-list">
            {isHistoryLoading ? (
              <p className="history-empty">正在加载...</p>
            ) : historyItems.length === 0 ? (
              <p className="history-empty">{historySearch ? "未找到匹配记录" : historyScope === "favorite" ? "暂无收藏" : "暂无历史"}</p>
            ) : (
              historyItems.map((item) => (
                <div
                  className={`history-item ${item.id === conversationId ? "active" : ""} ${item.isFavorite ? "is-favorite" : ""}`}
                  key={item.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => restoreHistoryItem(item)}
                  onKeyDown={(event) => handleHistoryItemKeyDown(event, item)}
                  aria-current={item.id === conversationId ? "page" : undefined}
                >
                  <span className="history-item-main">
                    <span className="history-item-title">{item.title}</span>
                    <small>
                      {item.modelKeys.length > 1 ? `${item.modelKeys.length} 模型 · ` : ""}
                      {formatHistoryTime(item.updatedAt)}
                    </small>
                  </span>
                  <span className="history-item-actions">
                    <button
                      className={`history-action-button favorite ${item.isFavorite ? "is-active" : ""}`}
                      type="button"
                      onClick={(event) => toggleHistoryFavorite(event, item)}
                      title={item.isFavorite ? "取消收藏" : "收藏"}
                      aria-label={item.isFavorite ? `取消收藏 ${item.title}` : `收藏 ${item.title}`}
                      aria-pressed={item.isFavorite}
                    >
                      <StarIcon filled={item.isFavorite} />
                    </button>
                    <button
                      className="history-action-button danger"
                      type="button"
                      onClick={(event) => deleteHistoryItem(event, item)}
                      title="删除"
                      aria-label={`删除 ${item.title}`}
                    >
                      <TrashIcon />
                    </button>
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </nav>
    );
  }

  function renderPromptForm(compact = false) {
    return (
      <div className={`prompt-form-wrap ${compact ? "compact-wrap" : "hero-wrap"}`}>
        {compact && continueBase && (
          <div className="continue-indicator">
            <span className="continue-indicator-icon" aria-hidden="true"><BranchIcon /></span>
            基于「{continueBase.modelLabel}」结果分支继续
            <button type="button" onClick={() => setContinueBase(null)} aria-label="取消继续">
              <CloseIcon />
            </button>
          </div>
        )}
        {compact && !continueBase && runs.length > 0 && (
          <div className="continue-hint">
            发送将让 {runs.length} 个模型各自基于上一轮结果并行继续；如需以某个结果为基础，点其卡片上的「以此结果继续」。
          </div>
        )}
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
              disabled={isGenerating || Boolean(fileError) || selectedModelKeys.length === 0}
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
        {!compact && renderModelPicker()}
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
      {phase === "idle" ? (
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
                说说你的想法，<strong className="brand-inline">星页 StarPage</strong> 帮你同时调用多个模型生成可对比的网页。
              </p>

              {renderPromptForm()}
            </div>
          </section>
        </main>
      ) : (
        <main key="workspace" className="workspace-shell">
          <section className={`workspace-layout ${isSidebarCollapsed ? "sidebar-collapsed" : ""} ${isMulti ? "compare-mode" : ""}`}>
            {renderHistorySidebar()}
            <aside className="conversation-pane">
              <div className="conversation-scroll">
                <div className={`chat-message user-message ${isLongPrompt ? "long-message" : ""}`} data-anim-stagger>
                  <div className="user-message-meta">
                    <span>你的需求</span>
                    <span>
                      {submittedPrompt.length} 字
                      {submittedFileNames.length ? ` · ${submittedFileNames.length} 个文件` : ""}
                      {roundIndex > 0 ? ` · 第 ${roundIndex + 1} 轮` : ""}
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
                  <div className="assistant-label">本轮模型 · {runs.length} 个并行</div>
                  <div className="run-tabs" role="tablist" aria-label="本轮模型">
                    {runs.map((run) => (
                      <button
                        key={run.taskId || run.modelKey}
                        type="button"
                        role="tab"
                        className={`run-tab ${run.modelKey === activeModelKey ? "is-active" : ""} is-${run.status}`}
                        aria-selected={run.modelKey === activeModelKey}
                        onClick={() => setActiveModelKey(run.modelKey)}
                      >
                        <span className="run-tab-icon" aria-hidden="true">{getRunStatusIcon(run.status)}</span>
                        <span className="run-tab-label">{run.modelLabel}</span>
                      </button>
                    ))}
                  </div>

                  {activeRun && (
                    <div className="progress-list">
                      {activeRun.progressSteps.map((step, index) => (
                        <div
                          className={`progress-item ${step.status} ${index === activeRun.progressSteps.length - 1 ? "is-last" : ""}`}
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
                                {activeRun.reasoning ? (
                                  <pre>{activeRun.reasoning}</pre>
                                ) : (
                                  <p>模型开始思考后，会在这里展示 reasoning_content。</p>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {activeRun?.status === "failed" && (
                  <div className="chat-message assistant-message error-message">
                    <div className="assistant-label">生成失败</div>
                    <p>{activeRun.errorMessage}</p>
                  </div>
                )}
              </div>

              <div className="composer-wrap">{renderPromptForm(true)}</div>
            </aside>

            <section className="preview-pane">
              <article className="panel preview-panel workspace-preview" data-anim-stagger>
                <div className="panel-heading">
                  <span
                    className={`dot ${overallStatus === "completed" ? "ready" : overallStatus === "failed" ? "failed" : "loading"}`}
                    aria-hidden="true"
                  />
                  <h2>页面预览{isMulti ? ` · 对比 ${runs.length} 个模型` : ""}</h2>
                  <span className="preview-status-text">
                    {overallStatus === "completed" ? "已生成" : overallStatus === "failed" ? "生成失败" : "生成中"}
                  </span>
                  {focusedTaskId && (
                    <button className="exit-focus-button" type="button" onClick={() => setFocusedTaskId(null)}>
                      返回对比
                    </button>
                  )}
                </div>

                <div className="preview-grid" style={previewGridStyle}>
                  {visibleRuns.map((run) => (
                    <PreviewCell
                      key={run.taskId || run.modelKey}
                      run={run}
                      multi={isMulti}
                      focused={focusedTaskId === run.taskId}
                      canContinue={!isGenerating && Boolean(run.pageId)}
                      onToggleFocus={() => setFocusedTaskId((current) => (current === run.taskId ? null : run.taskId))}
                      onContinue={() => startContinueFrom(run)}
                    />
                  ))}
                </div>
              </article>
            </section>
          </section>
        </main>
      )}
    </div>
  );
}

function makePendingRun(modelKey: string, modelLabel: string, hasFile: boolean): RunState {
  return {
    taskId: "",
    pageId: "",
    modelKey,
    modelLabel,
    status: "thinking",
    reasoning: "",
    statusText: "正在提交你的需求...",
    pageUrl: "",
    errorMessage: "",
    progressSteps: createInitialProgressSteps(hasFile),
  };
}

function makeRunFromResponse(run: GenerationRunResponse, hasFile: boolean): RunState {
  return {
    taskId: run.task_id,
    pageId: run.page_id,
    modelKey: run.model_key,
    modelLabel: run.model_label,
    status: "thinking",
    reasoning: "",
    statusText: "正在理解你的页面需求...",
    pageUrl: run.page_url,
    errorMessage: "",
    progressSteps: createInitialProgressSteps(hasFile),
  };
}

function labelForModel(key: string, models: ModelInfo[]): string {
  return models.find((model) => model.key === key)?.label ?? key;
}

function computeOverallStatus(runs: RunState[]): GenerationStatus {
  if (runs.length === 0) return "idle";
  if (runs.some((run) => run.status === "thinking" || run.status === "creating")) return "creating";
  if (runs.some((run) => run.status === "completed")) return "completed";
  if (runs.every((run) => run.status === "failed")) return "failed";
  return "completed";
}

function toAbsoluteUrl(pageUrl: string): string {
  if (!pageUrl) return "";
  if (pageUrl.startsWith("http://") || pageUrl.startsWith("https://")) return pageUrl;
  if (typeof window === "undefined") return pageUrl;
  return `${window.location.origin}${pageUrl}`;
}

function readCurrentSession(): StoredSession | null {
  return readJson<StoredSession>(CURRENT_SESSION_KEY);
}

function writeCurrentSession(session: StoredSession): void {
  localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify(session));
}

function readSelectedModels(): string[] | null {
  return readJson<string[]>(SELECTED_MODELS_KEY);
}

function writeSelectedModels(keys: string[]): void {
  try {
    localStorage.setItem(SELECTED_MODELS_KEY, JSON.stringify(keys));
  } catch {
    // 忽略存储异常
  }
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

function mapConversationHistoryItem(item: ConversationListResponseItem): HistoryItem {
  return {
    id: item.id,
    title: item.title || "未命名页面",
    isFavorite: Boolean(item.is_favorite),
    modelKeys: item.model_keys ?? [],
    nodeCount: item.node_count,
    status: mapBatchStatus(item.latest_batch_status),
    updatedAt: item.updated_at,
  };
}

function mapBatchStatus(status?: string | null): GenerationStatus {
  if (status === "succeeded" || status === "partial") return "completed";
  if (status === "failed" || status === "cancelled") return "failed";
  if (status === "running" || status === "pending") return "thinking";
  return "idle";
}

function mapNodeStatus(pageStatus: string, generationStatus?: string | null): GenerationStatus {
  if (pageStatus === "ready" || generationStatus === "succeeded") return "completed";
  if (pageStatus === "failed" || generationStatus === "failed" || generationStatus === "cancelled") return "failed";
  if (pageStatus === "generating" || generationStatus === "pending" || generationStatus === "running") return "thinking";
  return "idle";
}

function buildSessionFromDetail(detail: ConversationDetailResponse): StoredSession {
  const batches = detail.batches ?? [];
  const latest = batches[batches.length - 1];
  const baseBatch = latest;
  const hasFile = (baseBatch?.file_names ?? []).length > 0;

  const runs: RunState[] = (baseBatch?.nodes ?? []).map((node) => {
    const status = mapNodeStatus(node.page_status, node.generation_status);
    return {
      taskId: node.task_id ?? "",
      pageId: node.page_id,
      modelKey: node.model_key ?? "",
      modelLabel: node.model_label ?? node.model_key ?? "模型",
      status: status === "idle" ? "thinking" : status,
      reasoning: "",
      statusText: getStoredStatusText(status),
      pageUrl: node.page_url,
      errorMessage: status === "failed" ? "页面生成失败，请重新创建。" : "",
      progressSteps: createInitialProgressSteps(hasFile).map((step) => ({
        ...step,
        status: status === "completed" ? "completed" : step.status,
      })),
    };
  });

  const baseNode = baseBatch?.base_page_id
    ? (batches.flatMap((batch) => batch.nodes).find((node) => node.page_id === baseBatch.base_page_id) ?? null)
    : null;

  const now = new Date().toISOString();
  return {
    conversationId: detail.id,
    batchId: baseBatch?.batch_id ?? "",
    title: detail.title,
    prompt: baseBatch?.user_prompt || baseBatch?.prompt || detail.title,
    fileNames: baseBatch?.file_names ?? [],
    selectedModelKeys: baseBatch?.selected_models ?? [],
    runs,
    roundIndex: Math.max(0, batches.length - 1),
    basePageId: baseBatch?.base_page_id ?? undefined,
    baseModelLabel: baseNode?.model_label ?? undefined,
    createdAt: detail.created_at ?? now,
    updatedAt: detail.updated_at ?? now,
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

function getRunStatusIcon(status: GenerationStatus): ReactNode {
  if (status === "completed") return "✓";
  if (status === "failed") return "!";
  return <span className="progress-spinner" aria-hidden="true" />;
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
