"use client";

import type { ChangeEvent, DragEvent, FormEvent, KeyboardEvent, MouseEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { SiteFooter } from "./components/SiteFooter";

type GenerationStatus = "idle" | "thinking" | "creating" | "completed" | "failed";

type ModelInfo = {
  key: string;
  label: string;
  provider: string;
  is_default: boolean;
  available: boolean;
};

type AuthUser = {
  id: string;
  phone: string;
  display_name: string;
  phone_verified: boolean;
  has_password: boolean;
};

type AuthLoginResponse = {
  user: AuthUser;
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

type ApiUsagePayload = {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cached_input_tokens?: number;
  reasoning_tokens?: number;
};

type ApiCostPayload = {
  currency?: string;
  tier_label?: string;
  input?: number;
  output?: number;
  total?: number;
};

type SsePayload = {
  type: string;
  text?: string;
  page_id?: string;
  url?: string;
  message?: string;
  model_key?: string;
  skill_key?: string;
  skill_name?: string;
  step?: ProgressStepId;
  status?: ProgressStepStatus;
  output_tokens?: number;
  input_tokens?: number;
  total_tokens?: number;
  cached_input_tokens?: number;
  reasoning_tokens?: number;
  token_source?: "actual" | "estimated";
  usage?: ApiUsagePayload;
  cost?: ApiCostPayload;
};

type UsageCostSummary = {
  inputTokens: number;
  outputTokens: number;
  totalTokens?: number;
  cachedInputTokens?: number;
  reasoningTokens?: number;
  costTotalCny: number;
  costInputCny?: number;
  costOutputCny?: number;
  tierLabel?: string;
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
  usageSummary?: UsageCostSummary;
};

type StoredSession = {
  userId?: string;
  conversationId: string;
  batchId: string;
  title: string;
  prompt: string;
  fileNames: string[];
  selectedModelKeys: string[];
  appliedSkillKey?: string;
  appliedSkillName?: string | null;
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
  usage?: ApiUsagePayload | null;
  cost?: ApiCostPayload | null;
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
    ...(hasFile ? [createProgressStep("upload_file", "running"), createProgressStep("parse_file"), createProgressStep("compress_document")] : []),
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
const FILE_UPLOAD_TITLE = "支持 docx · pptx · xlsx · pdf · txt · md · html，最多 3 个，总计 ≤ 50MB";

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

/* 小三角：模型选择器收/展指示 */
const ChevronIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

/* 精致细勾：模型选中态用，比裸 ✓ 更克制现代 */
const CheckIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <polyline points="20 6 9 17 4 12" />
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

const ExpandIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M8 3H3v5" />
    <path d="M16 3h5v5" />
    <path d="M21 16v5h-5" />
    <path d="M3 16v5h5" />
    <path d="M3 3l6 6" />
    <path d="M21 3l-6 6" />
    <path d="M21 21l-6-6" />
    <path d="M3 21l6-6" />
  </svg>
);

const ExternalLinkIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14 3h7v7" />
    <path d="M10 14 21 3" />
    <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
  </svg>
);

const CopyIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);

const UserIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M20 21a8 8 0 0 0-16 0" />
    <circle cx="12" cy="8" r="4" />
  </svg>
);

const LogoutIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    <polyline points="16 17 21 12 16 7" />
    <line x1="21" y1="12" x2="9" y2="12" />
  </svg>
);

/* 模型标识色：给每个模型一个稳定的品牌色圆点，提升识别度与精致感。
   通过 key 做哈希映射到一组精选高级色，保证同一模型颜色稳定。 */
const MODEL_ACCENTS = ["#3563e9", "#8b5cf6", "#0ea5e9", "#f97316", "#14b8a6", "#ec4899"];

function modelAccent(key: string): string {
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) >>> 0;
  }
  return MODEL_ACCENTS[hash % MODEL_ACCENTS.length];
}

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
  onOpenPreview,
  onContinue,
  canContinue,
  selectedAsBase,
  dimmedByBase,
}: {
  run: RunState;
  onOpenPreview: () => void;
  onContinue: () => void;
  canContinue: boolean;
  selectedAsBase: boolean;
  dimmedByBase: boolean;
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

    const availableWidth = Math.max(stage.clientWidth - 16, 280);
    const scale = Math.min(1, availableWidth / PREVIEW_VIEWPORT_WIDTH);
    const availableHeight = Math.max(stage.clientHeight - 16, PREVIEW_DEFAULT_HEIGHT * scale);
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
  }, [run.status, absoluteUrl]);

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

  const statusLabel = run.status === "failed" ? "生成失败" : run.status === "creating" ? "创建中" : "思考中";

  return (
    <article className={`preview-cell ${selectedAsBase ? "is-continue-base" : ""} ${dimmedByBase ? "is-dimmed-by-base" : ""}`}>
      <header className="preview-cell-head">
        <span
          className={`dot ${run.status === "completed" ? "ready" : run.status === "failed" ? "failed" : "loading"}`}
          aria-hidden="true"
        />
        <strong className="preview-cell-model" title={run.modelLabel}>{run.modelLabel}</strong>
        {run.status !== "completed" && <span className={`run-status-chip is-${run.status}`}>{statusLabel}</span>}
      </header>

      {run.status === "completed" && absoluteUrl ? (
        <>
          <div className="preview-window">
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
            <button className="preview-hover-zoom" type="button" onClick={onOpenPreview} aria-label={`放大预览 ${run.modelLabel} 生成页面`}>
              <span className="button-icon" aria-hidden="true"><ExpandIcon /></span>
              放大预览
            </button>
          </div>
          <div className="link-actions">
            <div className="link-secondary-actions">
              <a className="link-icon-button" href={absoluteUrl} target="_blank" rel="noreferrer" aria-label={`打开 ${run.modelLabel} 生成页面`} title="打开页面">
                <ExternalLinkIcon />
                <span>打开</span>
              </a>
              <button className={`link-icon-button ${copied ? "copied" : ""}`} type="button" onClick={copyUrl} aria-label={`复制 ${run.modelLabel} 页面链接`} title="复制链接">
                <CopyIcon />
                <span>{copied ? "已复制" : copyFeedback || "复制"}</span>
              </button>
            </div>
            {canContinue && (
              <button className="continue-button" type="button" onClick={onContinue} aria-pressed={selectedAsBase}>
                <span className="button-icon" aria-hidden="true"><BranchIcon /></span>
                {selectedAsBase ? "已选为基础" : "以此结果继续"}
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

function FullscreenPreviewModal({ run, onClose }: { run: RunState; onClose: () => void }) {
  const absoluteUrl = useMemo(() => toAbsoluteUrl(run.pageUrl), [run.pageUrl]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    <div className="preview-modal-backdrop" role="presentation" onClick={onClose}>
      <section className="preview-modal" role="dialog" aria-modal="true" aria-labelledby="preview-modal-title" onClick={(event) => event.stopPropagation()}>
        <header className="preview-modal-head">
          <div className="preview-modal-title">
            <span className="dot ready" aria-hidden="true" />
            <div>
              <h2 id="preview-modal-title">{run.modelLabel} · 全屏预览</h2>
              <p>{absoluteUrl}</p>
            </div>
          </div>
          <div className="preview-modal-actions">
            <a href={absoluteUrl} target="_blank" rel="noreferrer">
              <ExternalLinkIcon />
              打开页面
            </a>
            <button type="button" onClick={onClose}>
              关闭
            </button>
          </div>
        </header>
        <div className="preview-modal-body">
          <iframe
            title={`${run.modelLabel} 生成页面全屏预览`}
            src={absoluteUrl}
            sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
          />
        </div>
      </section>
    </div>
  );
}

function AuthModal({
  onClose,
  onLogin,
}: {
  onClose: () => void;
  onLogin: (user: AuthUser) => void;
}) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [countdown, setCountdown] = useState(0);
  const [isSending, setIsSending] = useState(false);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = window.setTimeout(() => setCountdown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  async function sendCode(event?: FormEvent) {
    event?.preventDefault();
    if (isSending || countdown > 0) return;
    setErrorMessage("");
    setIsSending(true);
    try {
      const response = await apiFetch("/api/auth/sms/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "验证码发送失败"));
      const payload = (await response.json()) as { cooldown_seconds?: number };
      setStep("code");
      setCountdown(payload.cooldown_seconds ?? 60);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "验证码发送失败");
    } finally {
      setIsSending(false);
    }
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    setErrorMessage("");
    setIsLoggingIn(true);
    try {
      const response = await apiFetch("/api/auth/sms/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone, code }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "登录失败"));
      const payload = (await response.json()) as AuthLoginResponse;
      onLogin(payload.user);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "登录失败");
    } finally {
      setIsLoggingIn(false);
    }
  }

  return (
    <div className="auth-modal-backdrop" role="presentation">
      <section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-modal-title">
        <button className="auth-modal-close" type="button" onClick={onClose} aria-label="关闭登录窗口">
          <CloseIcon />
        </button>
        <div className="auth-modal-icon" aria-hidden="true"><UserIcon /></div>
        <h2 id="auth-modal-title">手机号登录 / 注册</h2>
        <p>输入手机号获取验证码，未注册手机号会自动创建账号。</p>

        <form className="auth-form" onSubmit={step === "phone" ? sendCode : login}>
          <label>
            手机号
            <input
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              inputMode="tel"
              autoComplete="tel"
              placeholder="请输入手机号"
              disabled={isSending || isLoggingIn}
            />
          </label>

          {step === "code" && (
            <label>
              验证码
              <input
                value={code}
                onChange={(event) => setCode(event.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="请输入短信验证码"
                disabled={isLoggingIn}
              />
            </label>
          )}

          {errorMessage && <div className="auth-error">{errorMessage}</div>}

          <div className="auth-actions">
            {step === "code" && (
              <button className="auth-secondary-button" type="button" onClick={() => void sendCode()} disabled={isSending || countdown > 0}>
                {countdown > 0 ? `${countdown}s 后重发` : "重新发送"}
              </button>
            )}
            <button className="auth-primary-button" type="submit" disabled={isSending || isLoggingIn}>
              {step === "phone" ? (isSending ? "发送中..." : "获取验证码") : isLoggingIn ? "登录中..." : "登录 / 注册"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function PasswordPromptModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (user: AuthUser) => void;
}) {
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    setErrorMessage("");
    setIsSaving(true);
    try {
      const response = await apiFetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "密码设置失败"));
      const user = (await response.json()) as AuthUser;
      onSaved(user);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "密码设置失败");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="auth-modal-backdrop" role="presentation">
      <section className="auth-modal password-modal" role="dialog" aria-modal="true" aria-labelledby="password-modal-title">
        <button className="auth-modal-close" type="button" onClick={onClose} aria-label="稍后设置密码">
          <CloseIcon />
        </button>
        <h2 id="password-modal-title">建议设置登录密码</h2>
        <p>你已经可以用手机号验证码登录。设置密码后，后续可以扩展更多账号安全能力。</p>
        <form className="auth-form" onSubmit={savePassword}>
          <label>
            新密码
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
              placeholder="至少 8 位"
              disabled={isSaving}
            />
          </label>
          {errorMessage && <div className="auth-error">{errorMessage}</div>}
          <div className="auth-actions">
            <button className="auth-secondary-button" type="button" onClick={onClose}>
              稍后再说
            </button>
            <button className="auth-primary-button" type="submit" disabled={isSaving}>
              {isSaving ? "保存中..." : "设置密码"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function HomePage() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [showPasswordPrompt, setShowPasswordPrompt] = useState(false);
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
  const [fullscreenRun, setFullscreenRun] = useState<RunState | null>(null);
  const [thinkingExpanded, setThinkingExpanded] = useState(true);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(true);
  const [historyItems, setHistoryItems] = useState<HistoryItem[]>([]);
  const [historyScope, setHistoryScope] = useState<HistoryScope>("all");
  const [historySearch, setHistorySearch] = useState("");
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [selectedModelKeys, setSelectedModelKeys] = useState<string[]>([]);
  const [appliedSkill, setAppliedSkill] = useState<{ key: string; name: string } | null>(null);
  const [roundIndex, setRoundIndex] = useState(0);
  const [continueBase, setContinueBase] = useState<{ pageId: string; modelLabel: string } | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isPromptAttention, setIsPromptAttention] = useState(false);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);

  const eventSourcesRef = useRef<EventSource[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dragCounterRef = useRef(0);
  const hasHydratedRef = useRef(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const motionRef = useRef<MotionLib | null>(null);

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
      userId: authUser?.id,
      batchId,
      title: buildHistoryTitle(submittedPrompt),
      prompt: submittedPrompt,
      fileNames: submittedFileNames,
      selectedModelKeys,
      appliedSkillKey: appliedSkill?.key,
      appliedSkillName: appliedSkill?.name ?? null,
      runs,
      roundIndex,
      basePageId: continueBase?.pageId,
      baseModelLabel: continueBase?.modelLabel,
      createdAt: readCurrentSession()?.createdAt ?? now,
      updatedAt: now,
    };
    writeCurrentSession(session);
  }, [phase, conversationId, authUser?.id, batchId, runs, submittedPrompt, submittedFileNames, selectedModelKeys, appliedSkill, roundIndex, continueBase]);

  async function initializeAuthState(): Promise<void> {
    const user = await loadMe();
    if (!user) {
      localStorage.removeItem(CURRENT_SESSION_KEY);
      setHistoryItems([]);
      hasHydratedRef.current = true;
      return;
    }

    await loadHistory(historyScope, historySearch, user);
    const session = readCurrentSession();
    if (session && session.userId !== user.id) {
      localStorage.removeItem(CURRENT_SESSION_KEY);
    }
    hasHydratedRef.current = true;
  }

  async function loadMe(): Promise<AuthUser | null> {
    setIsAuthLoading(true);
    try {
      const response = await apiFetch("/api/auth/me");
      if (response.status === 401) {
        setAuthUser(null);
        return null;
      }
      if (!response.ok) throw new Error(await readErrorMessage(response, "读取登录态失败"));
      const user = (await response.json()) as AuthUser;
      setAuthUser(user);
      return user;
    } catch {
      setAuthUser(null);
      return null;
    } finally {
      setIsAuthLoading(false);
    }
  }

  function handleLogin(user: AuthUser) {
    setAuthUser(user);
    setIsAuthModalOpen(false);
    setFileError("");
    localStorage.removeItem(CURRENT_SESSION_KEY);
    startNewChat();
    void loadHistory(historyScope, historySearch, user);
    if (!user.has_password) setShowPasswordPrompt(true);
  }

  async function handleLogout() {
    closeAllSources();
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } finally {
      setAuthUser(null);
      setHistoryItems([]);
      setShowPasswordPrompt(false);
      startNewChat();
    }
  }

  async function loadHistory(scope = historyScope, search = historySearch, user = authUser): Promise<void> {
    if (!user) {
      setHistoryItems([]);
      setIsHistoryLoading(false);
      return;
    }
    const params = new URLSearchParams();
    if (scope === "favorite") params.set("favorite_only", "true");
    const keyword = search.trim();
    if (keyword) params.set("q", keyword);
    const query = params.toString();

    setIsHistoryLoading(true);
    try {
      const response = await apiFetch(`/api/conversations${query ? `?${query}` : ""}`);
      if (response.status === 401) {
        setAuthUser(null);
        setHistoryItems([]);
        return;
      }
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
      const response = await apiFetch("/api/models");
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

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void initializeAuthState();
      void loadModels();
    }, 0);
    return () => window.clearTimeout(timer);
    // 首屏初始化只执行一次，内部函数会读当前存储和服务端登录态。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => closeAllSources();
  }, []);

  // 点击浮层外部或按 Esc 关闭模型选择 popover
  useEffect(() => {
    if (!isModelMenuOpen) return;
    const handlePointer = (event: globalThis.MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(event.target as Node)) {
        setIsModelMenuOpen(false);
      }
    };
    const handleKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setIsModelMenuOpen(false);
    };
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [isModelMenuOpen]);

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
    const submitFiles = selectedFiles.length > 0 ? selectedFiles : Array.from(fileInputRef.current?.files ?? []);
    const trimmedPrompt = prompt.trim();
    const effectivePrompt = trimmedPrompt || (submitFiles.length > 0 ? "请根据我上传的文件生成一个网页。" : "");
    const validationError = validateFiles(submitFiles);

    if (!effectivePrompt) {
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
    if (!authUser) {
      setFileError("请先登录后再创建页面");
      setIsAuthModalOpen(true);
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
    const fileNames = submitFiles.map((file) => file.name);
    const hasFile = fileNames.length > 0;
    closeAllSources();

    const pendingRuns: RunState[] = selectedModelKeys.map((key) => makePendingRun(key, labelForModel(key, availableModels), hasFile));

    playTransition(() => {
      setPhase("active");
      setSubmittedPrompt(effectivePrompt);
      setSubmittedFileNames(fileNames);
      setRuns(pendingRuns);
      setActiveModelKey(pendingRuns[0]?.modelKey ?? "");
      setFullscreenRun(null);
      setThinkingExpanded(true);
      setFileError("");
      setPrompt("");
    });

    try {
      const formData = new FormData();
      formData.append("prompt", effectivePrompt);
      submitFiles.forEach((file) => formData.append("files", file));
      selectedModelKeys.forEach((key) => formData.append("models", key));
      if (inConversation) {
        formData.append("conversation_id", conversationId);
        if (continueBase) formData.append("base_page_id", continueBase.pageId);
      }

      const response = await apiFetch("/api/generations", { method: "POST", body: formData });
      if (response.status === 401) {
        setIsAuthModalOpen(true);
      }
      if (!response.ok) throw new Error(await readErrorMessage(response));

      const data = (await response.json()) as CreateGenerationResponse;
      setConversationId(data.conversation_id);
      setBatchId(data.batch_id);
      setAppliedSkill(null);
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
    const source = new EventSource(`/api/generations/${taskId}/events`, { withCredentials: true });
    eventSourcesRef.current.push(source);

    const patch = (updater: (run: RunState) => RunState) => {
      setRuns((current) => current.map((run) => (run.taskId === taskId ? updater(run) : run)));
    };

    source.addEventListener("status", (event) => {
      const payload = parsePayload(event);
      if (payload.text) patch((run) => ({ ...run, statusText: payload.text ?? run.statusText }));
    });

    source.addEventListener("skill_selected", (event) => {
      const payload = parsePayload(event);
      if (payload.skill_key) {
        setAppliedSkill({
          key: payload.skill_key,
          name: payload.skill_name ?? payload.skill_key,
        });
      }
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
      patch((run) => {
        const nextRun = { ...run, progressSteps: updateProgressSteps(run.progressSteps, payload) };
        const summary = buildUsageSummary(payload);
        return summary ? { ...nextRun, usageSummary: summary } : nextRun;
      });
    });

    source.addEventListener("completed", (event) => {
      const payload = parsePayload(event);
      patch((run) => ({
        ...run,
        status: "completed",
        statusText: "页面已创建完成",
        pageUrl: payload.url ?? run.pageUrl,
        pageId: payload.page_id ?? run.pageId,
        usageSummary: buildUsageSummary(payload) ?? run.usageSummary,
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
      setFullscreenRun(null);
      setThinkingExpanded(true);
      setRoundIndex(0);
      setContinueBase(null);
      setAppliedSkill(null);
    });
  }

  async function restoreHistoryItem(item: HistoryItem) {
    closeAllSources();
    try {
      const response = await apiFetch(`/api/conversations/${item.id}`);
      if (response.status === 401) {
        setIsAuthModalOpen(true);
        return;
      }
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
      const response = await apiFetch(`/api/conversations/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_favorite: nextFavorite }),
      });
      if (response.status === 401) {
        setIsAuthModalOpen(true);
        return;
      }
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
      const response = await apiFetch(`/api/conversations/${item.id}`, { method: "DELETE" });
      if (response.status === 401) {
        setIsAuthModalOpen(true);
        return;
      }
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
    setFullscreenRun(null);
    setSelectedFiles([]);
    setFileError("");
    setPrompt("");
    setThinkingExpanded(true);
    setRoundIndex(session.roundIndex ?? 0);
    setContinueBase(
      session.basePageId ? { pageId: session.basePageId, modelLabel: session.baseModelLabel ?? "上一结果" } : null,
    );
    if (session.selectedModelKeys?.length) setSelectedModelKeys(session.selectedModelKeys);
    setAppliedSkill(
      session.appliedSkillName
        ? { key: session.appliedSkillKey ?? "", name: session.appliedSkillName }
        : null,
    );
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

  function handleEmptySubmitHint() {
    promptTextareaRef.current?.focus();
    setIsPromptAttention(true);
    window.setTimeout(() => setIsPromptAttention(false), 320);
  }

  function handlePromptDragEnter(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragCounterRef.current += 1;
    if (event.dataTransfer.types.includes("Files")) setIsDragOver(true);
  }

  function handlePromptDragOver(event: DragEvent<HTMLElement>) {
    event.preventDefault();
  }

  function handlePromptDragLeave(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragOver(false);
    }
  }

  function handlePromptDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragCounterRef.current = 0;
    setIsDragOver(false);
    if (isGenerating) return;
    handleFileChange(event.dataTransfer.files);
  }

  const isGenerating = runs.some((run) => run.status === "thinking" || run.status === "creating");
  const canSubmit = Boolean(prompt.trim()) || selectedFiles.length > 0;
  const isLongPrompt = submittedPrompt.length > 260;
  const overallStatus = computeOverallStatus(runs);
  const isMulti = runs.length > 1;
  const activeRun = runs.find((run) => run.modelKey === activeModelKey) ?? runs[0] ?? null;
  const fullscreenPreviewRun = fullscreenRun
    ? (runs.find((run) => (fullscreenRun.taskId && run.taskId === fullscreenRun.taskId) || (fullscreenRun.pageId && run.pageId === fullscreenRun.pageId)) ?? fullscreenRun)
    : null;
  const previewGridStyle = {
    gridTemplateColumns: `repeat(${Math.min(Math.max(runs.length, 1), 2)}, minmax(0, 1fr))`,
  };
  const availableSelectableModels = availableModels.filter((model) => model.available);

  // 模型选择收成紧凑的弹出选择器：常态只占一个 pill，点击向上弹出浮层多选，
  // 让输入卡片保持轻盈，不被一整条模型列表撑臃肿。
  function renderModelPicker() {
    if (availableModels.length === 0) return null;
    const selected = availableModels.filter((model) => selectedModelKeys.includes(model.key));
    const triggerLabel = selected.length === 1 ? selected[0].label : `${selected.length} 个模型`;
    return (
      <div className={`model-picker ${isModelMenuOpen ? "is-open" : ""}`} ref={modelMenuRef}>
        <button
          type="button"
          className="model-picker-trigger"
          onClick={() => setIsModelMenuOpen((value) => !value)}
          disabled={isGenerating}
          aria-haspopup="menu"
          aria-expanded={isModelMenuOpen}
          title="选择生成模型"
        >
          <span className="model-picker-dots" aria-hidden="true">
            {selected.slice(0, 3).map((model) => (
              <span key={model.key} className="model-dot" style={{ background: modelAccent(model.key) }} />
            ))}
          </span>
          <span className="model-picker-text">{triggerLabel}</span>
          <span className="model-picker-caret" aria-hidden="true"><ChevronIcon /></span>
        </button>

        {isModelMenuOpen && (
          <div className="model-menu" role="menu" aria-label="选择生成模型">
            <div className="model-menu-head">
              <strong>生成模型</strong>
              <span>可多选 · 并排生成便于对比</span>
            </div>
            <div className="model-menu-list">
              {availableModels.map((model) => {
                const active = selectedModelKeys.includes(model.key);
                return (
                  <button
                    key={model.key}
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={active}
                    className={`model-menu-item ${active ? "is-active" : ""}`}
                    onClick={() => model.available && toggleModel(model.key)}
                    disabled={!model.available}
                    title={model.available ? model.label : `${model.label}（未配置密钥，暂不可用）`}
                  >
                    <span className="model-dot" aria-hidden="true" style={{ background: modelAccent(model.key) }} />
                    <span className="model-menu-item-label">{model.label}</span>
                    {!model.available && <span className="model-unavailable">未配置</span>}
                    <span className="model-menu-check" aria-hidden="true">{active && <CheckIcon />}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
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
          {authUser ? (
            <>
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
            </>
          ) : (
            <div className="history-login-empty">
              <strong>{isAuthLoading ? "正在检查登录态" : "登录后查看历史"}</strong>
              <p>历史创建会按手机号账号保存，换设备也能继续查看。</p>
              <button type="button" onClick={() => setIsAuthModalOpen(true)}>登录 / 注册</button>
            </div>
          )}
        </div>
        <div className="sidebar-user-footer">
          <button
            className="sidebar-user-button"
            type="button"
            onClick={() => (authUser ? undefined : setIsAuthModalOpen(true))}
            title={authUser ? authUser.phone : "登录 / 注册"}
            aria-label={authUser ? `当前用户 ${authUser.phone}` : "登录 / 注册"}
          >
            <span className="sidebar-user-avatar"><UserIcon /></span>
            {!isSidebarCollapsed && (
              <span className="sidebar-user-text">
                <strong>{authUser ? maskPhone(authUser.phone) : isAuthLoading ? "检查登录态" : "登录 / 注册"}</strong>
                <small>{authUser ? "已登录" : "同步历史记录"}</small>
              </span>
            )}
          </button>
          {authUser && !isSidebarCollapsed && (
            <button
              className="sidebar-logout-button"
              type="button"
              onClick={() => void handleLogout()}
              title="退出登录"
              aria-label="退出登录"
            >
              <LogoutIcon />
            </button>
          )}
        </div>
      </nav>
    );
  }

  function renderPromptForm(compact = false) {
    const submitBlocked = isGenerating || Boolean(fileError) || selectedModelKeys.length === 0;
    const submitDisabled = submitBlocked || !canSubmit;
    const isEmptySubmit = !compact && !canSubmit && !isGenerating && !submitBlocked;
    const promptWrapClassName = compact
      ? "prompt-form-wrap compact-wrap"
      : `prompt-form-wrap hero-wrap ${isModelMenuOpen ? "model-menu-open" : ""}`;

    return (
      <div className={promptWrapClassName}>
        {compact && continueBase && (
          <div className="continue-indicator">
            <span className="continue-indicator-icon" aria-hidden="true"><BranchIcon /></span>
            将基于「{continueBase.modelLabel}」的结果进行修改
            <button type="button" onClick={() => setContinueBase(null)} aria-label="取消继续">
              <CloseIcon />
            </button>
          </div>
        )}
        <form
          className={`prompt-card ${compact ? "compact-prompt" : "hero-prompt"} ${isDragOver ? "is-drag-over" : ""} ${isPromptAttention ? "is-attention" : ""}`}
          onSubmit={handleSubmit}
          onDragEnter={handlePromptDragEnter}
          onDragOver={handlePromptDragOver}
          onDragLeave={handlePromptDragLeave}
          onDrop={handlePromptDrop}
        >
          {isDragOver && (
            <div className="prompt-drag-overlay" aria-hidden="true">
              松开即可上传资料
            </div>
          )}
          <textarea
            ref={promptTextareaRef}
            value={prompt}
            onChange={handlePromptChange}
            placeholder={compact ? "继续描述你想调整的方向…" : "说说你想做的页面，例如「面向客户的产品介绍页」"}
            rows={compact ? 1 : 2}
            disabled={isGenerating}
          />
          {selectedFiles.length > 0 && (
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
          )}
          <div className={`composer-toolbar ${compact ? "is-compact" : ""}`}>
            <div className="composer-tools">
              <label
                className={`composer-upload ${compact ? "is-compact" : ""}`}
                title={FILE_UPLOAD_TITLE}
                aria-label="上传文件"
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept={ACCEPTED_FILE_TYPES}
                  disabled={isGenerating}
                  onChange={(event) => handleFileChange(event.target.files)}
                />
                <span className="composer-upload-icon" aria-hidden="true"><AttachmentIcon /></span>
                {compact ? <span className="composer-upload-text">上传</span> : <span className="composer-upload-text">上传文件</span>}
              </label>
              {!compact && renderModelPicker()}
            </div>
            <button
              className={`composer-send ${compact ? "is-secondary" : ""} ${isGenerating ? "is-loading" : ""} ${isEmptySubmit ? "is-empty" : ""}`}
              type={isEmptySubmit ? "button" : "submit"}
              disabled={compact ? submitDisabled : submitBlocked}
              onClick={
                isEmptySubmit
                  ? (event) => {
                      event.preventDefault();
                      handleEmptySubmitHint();
                    }
                  : undefined
              }
              aria-label={isGenerating ? "正在生成" : compact ? "发送修改" : "创建页面"}
              aria-busy={isGenerating}
            >
              {isGenerating ? (
                <SpinnerIcon />
              ) : compact ? (
                <SendIcon />
              ) : (
                <ArrowUpIcon />
              )}
            </button>
          </div>
        </form>
        {/* 灵感建议：置于输入卡片下方并居中，与整体居中布局一致，作为"兜底引导"；
            一旦开始输入文本即自动隐藏，把焦点还给内容。 */}
        {!compact && !prompt.trim() && (
          <div className="prompt-inspirations" role="list" aria-label="灵感建议" data-anim-stagger>
            <span className="prompt-inspirations-lead" aria-hidden="true">试试</span>
            {PROMPT_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                role="listitem"
                className="prompt-inspiration-chip"
                onClick={() => handlePresetClick(preset)}
                disabled={isGenerating}
              >
                <span className="preset-emoji" aria-hidden="true">{preset.emoji}</span>
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
                说说你的想法，或上传文件，<strong className="brand-inline">星页 StarPage</strong> 帮你生成可对比的网页。
              </p>

              {renderPromptForm()}
            </div>
            <SiteFooter />
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
                  <div className="assistant-label">
                    本轮模型 · {runs.length} 个并行
                    {appliedSkill && (
                      <span className="applied-skill-badge" title="本轮应用的网页技能">
                        技能 · {appliedSkill.name}
                      </span>
                    )}
                  </div>
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
                                {activeRun.usageSummary && step.status === "completed" ? (
                                  <>
                                    输入 {formatTokenCount(activeRun.usageSummary.inputTokens)} / 输出{" "}
                                    {formatTokenCount(activeRun.usageSummary.outputTokens)} tokens
                                  </>
                                ) : (
                                  <>
                                    输出 {step.outputTokens ?? 0} tokens
                                    {step.tokenSource === "estimated" ? "（估算）" : ""}
                                  </>
                                )}
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
                      {activeRun.usageSummary && (
                        <div className="usage-summary">
                          <span className="token-meta-icon" aria-hidden="true"><BoltIcon /></span>
                          <div className="usage-summary-body">
                            <span>
                              输入 {formatTokenCount(activeRun.usageSummary.inputTokens)} / 输出{" "}
                              {formatTokenCount(activeRun.usageSummary.outputTokens)} tokens
                              {activeRun.usageSummary.cachedInputTokens
                                ? ` · 缓存命中 ${formatTokenCount(activeRun.usageSummary.cachedInputTokens)}`
                                : ""}
                              {activeRun.usageSummary.reasoningTokens
                                ? ` · 思考 ${formatTokenCount(activeRun.usageSummary.reasoningTokens)}`
                                : ""}
                            </span>
                            <span className="usage-summary-cost">
                              {activeRun.usageSummary.costTotalCny > 0 || activeRun.usageSummary.tierLabel ? (
                                <>
                                  花费 {formatCostCny(activeRun.usageSummary.costTotalCny)}
                                  {activeRun.usageSummary.tierLabel ? `（${activeRun.usageSummary.tierLabel}）` : ""}
                                </>
                              ) : (
                                "费用未配置"
                              )}
                            </span>
                          </div>
                        </div>
                      )}
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
                <div className="panel-heading preview-heading">
                  <h2>结果预览</h2>
                  {isMulti && <span className="preview-count">{runs.length} 个版本</span>}
                  {overallStatus !== "completed" && (
                    <span className="preview-status-text">
                      {overallStatus === "failed" ? "生成失败" : "生成中"}
                    </span>
                  )}
                </div>

                <div className="preview-grid" style={previewGridStyle}>
                  {runs.map((run) => (
                    <PreviewCell
                      key={run.taskId || run.modelKey}
                      run={run}
                      canContinue={!isGenerating && Boolean(run.pageId)}
                      selectedAsBase={continueBase?.pageId === run.pageId}
                      dimmedByBase={continueBase ? continueBase.pageId !== run.pageId : false}
                      onOpenPreview={() => setFullscreenRun(run)}
                      onContinue={() => startContinueFrom(run)}
                    />
                  ))}
                </div>
              </article>
            </section>
          </section>
        </main>
      )}
      {fullscreenPreviewRun && <FullscreenPreviewModal run={fullscreenPreviewRun} onClose={() => setFullscreenRun(null)} />}
      {isAuthModalOpen && <AuthModal onClose={() => setIsAuthModalOpen(false)} onLogin={handleLogin} />}
      {showPasswordPrompt && authUser && !authUser.has_password && (
        <PasswordPromptModal
          onClose={() => setShowPasswordPrompt(false)}
          onSaved={(user) => {
            setAuthUser(user);
            setShowPasswordPrompt(false);
          }}
        />
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
  if (typeof window === "undefined") return pageUrl;

  try {
    const url = new URL(pageUrl, window.location.origin);
    if (url.pathname.startsWith("/p/")) {
      return `${window.location.origin}${url.pathname}${url.search}${url.hash}`;
    }
    return url.toString();
  } catch {
    if (pageUrl.startsWith("/")) return `${window.location.origin}${pageUrl}`;
    return pageUrl;
  }
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
    const usageSummary = buildUsageSummaryFromDetail(node.usage, node.cost);
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
        outputTokens:
          step.id === "model_output" && usageSummary
            ? usageSummary.outputTokens
            : step.outputTokens,
        tokenSource:
          step.id === "model_output" && usageSummary
            ? "actual"
            : step.tokenSource,
      })),
      usageSummary,
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

function maskPhone(phone: string): string {
  if (phone.length < 7) return phone;
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`;
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

function buildUsageSummary(payload: SsePayload): UsageCostSummary | undefined {
  const inputTokens = payload.usage?.input_tokens ?? payload.input_tokens;
  const outputTokens = payload.usage?.output_tokens ?? payload.output_tokens;
  if (inputTokens === undefined || outputTokens === undefined) {
    return undefined;
  }

  return {
    inputTokens,
    outputTokens,
    totalTokens: payload.usage?.total_tokens ?? payload.total_tokens,
    cachedInputTokens: payload.usage?.cached_input_tokens ?? payload.cached_input_tokens,
    reasoningTokens: payload.usage?.reasoning_tokens ?? payload.reasoning_tokens,
    costTotalCny: payload.cost?.total ?? 0,
    costInputCny: payload.cost?.input,
    costOutputCny: payload.cost?.output,
    tierLabel: payload.cost?.tier_label,
  };
}

function buildUsageSummaryFromDetail(
  usage?: ApiUsagePayload | null,
  cost?: ApiCostPayload | null,
): UsageCostSummary | undefined {
  if (usage?.input_tokens === undefined || usage.output_tokens === undefined) {
    return undefined;
  }

  return {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    totalTokens: usage.total_tokens,
    cachedInputTokens: usage.cached_input_tokens,
    reasoningTokens: usage.reasoning_tokens,
    costTotalCny: cost?.total ?? 0,
    costInputCny: cost?.input,
    costOutputCny: cost?.output,
    tierLabel: cost?.tier_label,
  };
}

function formatTokenCount(value: number): string {
  return value.toLocaleString("zh-CN");
}

function formatCostCny(value: number): string {
  if (value >= 1) return `¥${value.toFixed(4)}`;
  if (value >= 0.01) return `¥${value.toFixed(4)}`;
  return `¥${value.toFixed(6)}`;
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

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: init.credentials ?? "include",
  });
}

async function readErrorMessage(response: Response, fallback = "请求失败"): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
  } catch {
    return fallback;
  }

  return fallback;
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
