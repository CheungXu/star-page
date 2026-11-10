"use client";

import type { ChangeEvent, DragEvent, FormEvent, KeyboardEvent, MouseEvent, ReactNode } from "react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DataPrivacyModal } from "./components/DataPrivacyModal";
import { PolicyNoticeBar } from "./components/PolicyNoticeBar";
import { SiteFooter } from "./components/SiteFooter";
import { LEGAL_POLICY_VERSION } from "./legal/docs";
import { apiFetch, readBillingError, readErrorMessage } from "./lib/api";
import { track } from "./lib/analytics";
import { creditsToYuan, fetchAccount, formatCredits, type BillingAccount } from "./lib/billing";
import { toAbsoluteUrl } from "./lib/links";
import "./components/workspace/workspace.css";
import { ResultActions } from "./components/workspace/actions";
import { RoundSource } from "./components/workspace/bits";
import { WorkspaceProvider } from "./components/workspace/context";
import { GenerationProgress, GenerationResults } from "./components/workspace/panels";
import type { ActionLayout, PreviewPresentation, RoundBase, RoundNav, WorkspaceRound, WorkspaceRun } from "./components/workspace/types";

type GenerationStatus = "idle" | "thinking" | "creating" | "completed" | "failed";

type ModelInfo = {
  key: string;
  label: string;
  provider: string;
  is_default: boolean;
  available: boolean;
  anon_allowed?: boolean;
  anon_default?: boolean;
};

type AuthUser = {
  id: string;
  phone: string;
  display_name: string;
  phone_verified: boolean;
  has_password: boolean;
  is_admin?: boolean;
  allow_training?: boolean;
  needs_policy_notice?: boolean;
};

type AuthLoginResponse = {
  user: AuthUser;
};

type CreateGenerationResponse = {
  conversation_id: string;
  batch_id: string;
  kind: string;
  runs: GenerationRunResponse[];
  /** 非空表示这一轮从更早的轮次分叉，conversation_id 已是新会话。 */
  forked_from?: ForkSourceResponse | null;
};

type ForkSourceResponse = {
  conversation_id: string;
  title: string;
  round_number: number;
  available: boolean;
};

/** 分叉会话的来源：从哪条会话的第几轮（从 1 开始）分出来；来源被删后不可再跳回。 */
type ForkSource = {
  conversationId: string;
  title: string;
  roundNumber: number;
  available: boolean;
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
  billing?: { training_consent?: string; credits_charged?: number; discount_credits?: number };
};

type UsageCostSummary = {
  inputTokens: number;
  outputTokens: number;
  totalTokens?: number;
  cachedInputTokens?: number;
  reasoningTokens?: number;
  creditsCharged?: number;
  discountCredits?: number;
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
  durationMs?: number;
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
  /** 用户已选、还没提交的续改基础。 */
  basePageId?: string;
  baseModelLabel?: string;
  /** 当前轮之前已结束的轮次，以及当前轮基于哪个结果。 */
  pastRounds?: WorkspaceRound[];
  currentBasedOn?: RoundBase | null;
  forkSource?: ForkSource | null;
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
  forkedFrom: ForkSource | null;
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
  forked_from?: ForkSourceResponse | null;
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
  billing?: { credits_charged?: number; discount_credits?: number } | null;
  duration_ms?: number | null;
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
  /** 从来源会话继承来的轮次（分叉点及之前）。 */
  inherited?: boolean;
};

type ConversationDetailResponse = {
  id: string;
  title: string;
  origin: string;
  scene_key?: string | null;
  created_at: string;
  updated_at: string;
  batches: ConversationBatchResponse[];
  forked_from?: ForkSourceResponse | null;
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
const ANON_MAX_MODELS = 2;
const ACCEPTED_FILE_EXTENSIONS = [".docx", ".pptx", ".xlsx", ".xls", ".pdf", ".txt", ".md", ".markdown", ".html", ".htm"];
const ACCEPTED_FILE_TYPES = ACCEPTED_FILE_EXTENSIONS.join(",");
const SCENE_IMAGE_MAX_BYTES = 2 * 1024 * 1024;

function isSceneImageFile(file: File): boolean {
  return file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif)$/i.test(file.name);
}

function sceneImageProblem(file: File): string {
  if (!/\.(png|jpe?g|webp|gif)$/i.test(file.name) && !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) {
    return `${file.name} 不是 PNG、JPEG、WEBP 或 GIF`;
  }
  if (file.size > SCENE_IMAGE_MAX_BYTES) return `${file.name} 超过 2MB，请压缩后再上传`;
  return "";
}
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

const ExternalLinkIcon = () => (
  <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14 3h7v7" />
    <path d="M10 14 21 3" />
    <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
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

/* 场景卡片：来自 /api/scenes，点击后绑定 scene_key，而不只是回填一句话。 */
type SceneGuideField = { key: string; label: string; placeholder: string };
type SceneStyleOption = { key: string; label: string; hint: string };
type ImageSlot = { key: string; label: string };
type SceneDefinition = {
  key: string;
  name: string;
  emoji: string;
  priority: string;
  tagline: string;
  prompt_template: string;
  guide_fields: SceneGuideField[];
  styles: SceneStyleOption[];
  accepts_documents: boolean;
  accepts_images: boolean;
  image_slots: ImageSlot[];
  features: string[];
};
type SceneCase = { page_id: string; remix_prompt: string };
type ShowcaseCase = { sceneKey: string; sceneName: string; title: string; pageUrl: string };

/** 案例缩略图：按手机宽度渲染真实页面，再按卡片宽度等比缩小。 */
const CASE_THUMB_VIEWPORT = 390;

function CaseThumb({ url, title }: { url: string; title: string }) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0.45);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const update = () => setScale(box.clientWidth / CASE_THUMB_VIEWPORT);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  return (
    <div className={`case-thumb ${ready ? "is-ready" : ""}`} ref={boxRef} aria-hidden="true">
      <iframe
        title={title}
        src={toAbsoluteUrl(url)}
        sandbox="allow-scripts"
        tabIndex={-1}
        // 生成页大多带入场动画，加载完再等一会儿才显示，避免露出半截动画。
        onLoad={() => window.setTimeout(() => setReady(true), 900)}
        style={{ width: CASE_THUMB_VIEWPORT, height: CASE_THUMB_VIEWPORT * 1.6, transform: `scale(${scale})` }}
      />
    </div>
  );
}

/** 接口未返回前也要画出来，避免首屏空白。字段与 config/scenes.json 对齐。 */
const FALLBACK_SCENES: SceneDefinition[] = [
  {
    key: "resume",
    name: "网页简历",
    emoji: "👤",
    priority: "P0",
    tagline: "旧简历变成 HR 愿意点开的网页",
    prompt_template: "帮我把上传的简历做成一份网页简历：大厂极简风格，第一屏突出姓名、目标职位和三项最亮眼的成绩，电话和邮箱默认打码。",
    guide_fields: [
      { key: "name", label: "姓名", placeholder: "例如：林晓" },
      { key: "target_role", label: "目标职位", placeholder: "例如：前端工程师" },
      { key: "highlight", label: "最想被看到的一点", placeholder: "例如：独立负责过活动页改版" },
    ],
    styles: [
      { key: "bigtech", label: "大厂极简", hint: "风格：大厂极简。单栏或左栏加右主栏，主色用沉稳蓝或墨绿，信息清楚、留白干净。" },
      { key: "portfolio", label: "设计师作品集", hint: "风格：设计师作品集。项目用大卡片，视觉有辨识度，同时保证经历可读。" },
      { key: "graduate", label: "应届生", hint: "风格：应届生。教育和项目的权重大于工作经历，语气积极，结构清楚。" },
    ],
    accepts_documents: true,
    accepts_images: false,
    image_slots: [],
    features: ["print_pdf", "stable_link", "view_count"],
  },
  {
    key: "creator-home",
    name: "博主主页",
    emoji: "✦",
    priority: "P0",
    tagline: "放进简介的个人主页",
    prompt_template: "帮我做一个博主个人主页。昵称「」，一句话介绍「」。放上我的小红书、公众号等平台入口和代表作品，头像和微信二维码用我上传的图片，二维码放在最显眼的位置。",
    guide_fields: [
      { key: "name", label: "昵称", placeholder: "例如：星野" },
      { key: "positioning", label: "一句话定位", placeholder: "例如：帮独立开发者做增长" },
      { key: "links", label: "平台链接", placeholder: "小红书 / 公众号 / B站，每行一个" },
      { key: "works", label: "代表作品", placeholder: "作品或课程名称，每行一个" },
    ],
    styles: [],
    accepts_documents: true,
    accepts_images: true,
    image_slots: [
      { key: "avatar", label: "头像" },
      { key: "qrcode", label: "微信二维码" },
    ],
    features: ["stable_link", "view_count", "images"],
  },
  {
    key: "landing",
    name: "产品落地页",
    emoji: "🚀",
    priority: "P1",
    tagline: "一份资料，几种设计",
    prompt_template: "根据我上传的产品资料做一个产品落地页：首屏一句话讲清卖点并配一个主按钮，接着介绍核心功能和使用步骤。不要编造用户数和价格。",
    guide_fields: [
      { key: "product_name", label: "产品名", placeholder: "例如：星页" },
      { key: "one_liner", label: "一句话卖点", placeholder: "例如：一份文档，变成一个网页" },
      { key: "audience", label: "目标用户", placeholder: "例如：独立开发者" },
    ],
    styles: [],
    accepts_documents: true,
    accepts_images: true,
    image_slots: [{ key: "hero", label: "产品图" }],
    features: ["stable_link", "seo"],
  },
  {
    key: "invitation",
    name: "活动邀请函",
    emoji: "🎉",
    priority: "P1",
    tagline: "转进微信就能看",
    prompt_template: "帮我做一张竖屏活动邀请函。活动名「」，时间「」，地点「」，报名方式「」。包含议程和着装提示，背景用我上传的图片。",
    guide_fields: [
      { key: "title", label: "活动主题", placeholder: "例如：周六设计沙龙" },
      { key: "when_where", label: "时间地点", placeholder: "例如：5 月 16 日 14:00 · 广州" },
      { key: "rsvp", label: "报名方式", placeholder: "外部报名链接，或写“见二维码”" },
    ],
    styles: [],
    accepts_documents: true,
    accepts_images: true,
    image_slots: [
      { key: "hero", label: "背景图" },
      { key: "qrcode", label: "报名二维码" },
    ],
    features: ["stable_link", "poster"],
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
  selectedAsBase,
  dimmedByBase,
  sceneFeatures,
  actions,
  presentation = "default",
  onFrame,
  onReady,
}: {
  run: RunState;
  selectedAsBase: boolean;
  dimmedByBase: boolean;
  sceneFeatures: string[];
  /** 卡片底栏的操作；舞台和缩略图不带，操作由结果区按方案摆放。 */
  actions?: ReactNode;
  presentation?: PreviewPresentation;
  onFrame?: (node: HTMLIFrameElement | null) => void;
  onReady?: () => void;
}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [metrics, setMetrics] = useState({
    viewportWidth: PREVIEW_VIEWPORT_WIDTH,
    contentHeight: PREVIEW_DEFAULT_HEIGHT,
    scale: 0.5,
  });
  const [externalViews, setExternalViews] = useState<number | null>(null);
  const showViews = presentation === "default" && sceneFeatures.includes("view_count");

  const absoluteUrl = useMemo(() => toAbsoluteUrl(run.pageUrl), [run.pageUrl]);

  useEffect(() => {
    onFrame?.(iframeRef.current);
    return () => onFrame?.(null);
  }, [absoluteUrl, run.status, onFrame]);

  useEffect(() => {
    if (!showViews || run.status !== "completed" || !run.pageId) return;
    let cancelled = false;
    void apiFetch(`/api/pages/${run.pageId}/stats`).then(async (response) => {
      if (!response.ok || cancelled) return;
      const data = (await response.json()) as { external_view_count?: number };
      if (!cancelled) setExternalViews(data.external_view_count ?? 0);
    });
    return () => {
      cancelled = true;
    };
  }, [run.pageId, run.status, showViews]);

  function recomputeMetrics() {
    const stage = stageRef.current;
    if (!stage) return;

    const inset = presentation === "thumb" ? 0 : 16;
    const availableWidth = Math.max(stage.clientWidth - inset, presentation === "thumb" ? 60 : 280);
    const scale = Math.min(1, availableWidth / PREVIEW_VIEWPORT_WIDTH);
    const availableHeight = Math.max(stage.clientHeight - inset, PREVIEW_DEFAULT_HEIGHT * scale);
    const contentHeight = Math.max(PREVIEW_DEFAULT_HEIGHT, Math.round(availableHeight / scale));
    setMetrics({ viewportWidth: PREVIEW_VIEWPORT_WIDTH, contentHeight, scale });
  }

  useEffect(() => {
    if (run.status !== "completed") return;

    const handleResize = () => recomputeMetrics();
    window.addEventListener("resize", handleResize);
    const timer = window.setTimeout(handleResize, 0);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(handleResize);
    if (observer && stageRef.current) observer.observe(stageRef.current);
    return () => {
      window.removeEventListener("resize", handleResize);
      window.clearTimeout(timer);
      observer?.disconnect();
    };
  }, [run.status, absoluteUrl]);

  function handlePreviewLoad() {
    recomputeMetrics();
    onReady?.();
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
    <article className={`preview-cell presentation-${presentation} ${selectedAsBase ? "is-continue-base" : ""} ${dimmedByBase ? "is-dimmed-by-base" : ""}`}>
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
          </div>
          {presentation === "default" && actions}
          {showViews && externalViews !== null && (
            <p className="scene-view-count">{externalViews === 0 ? "还没有别人打开" : `别人打开 ${externalViews} 次`}</p>
          )}
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
  const [hasAgreed, setHasAgreed] = useState(false);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = window.setTimeout(() => setCountdown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  async function sendCode(event?: FormEvent) {
    event?.preventDefault();
    if (isSending || countdown > 0) return;
    if (!hasAgreed) {
      setErrorMessage("请先阅读并勾选同意《用户服务协议》和《隐私政策》");
      return;
    }
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
    if (!hasAgreed) {
      setErrorMessage("请先阅读并勾选同意《用户服务协议》和《隐私政策》");
      return;
    }
    setErrorMessage("");
    setIsLoggingIn(true);
    try {
      const response = await apiFetch("/api/auth/sms/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone, code, agreement_version: LEGAL_POLICY_VERSION }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "登录失败"));
      const payload = (await response.json()) as AuthLoginResponse;
      track("login_success");
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

          <label className="auth-consent">
            <input type="checkbox" checked={hasAgreed} onChange={(event) => setHasAgreed(event.target.checked)} />
            <span>
              我已阅读并同意
              <a href="/terms" target="_blank" rel="noopener noreferrer">《用户服务协议》</a>和
              <a href="/privacy" target="_blank" rel="noopener noreferrer">《隐私政策》</a>。
            </span>
          </label>

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
  const router = useRouter();
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [showPasswordPrompt, setShowPasswordPrompt] = useState(false);
  const [isPrivacyModalOpen, setIsPrivacyModalOpen] = useState(false);
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
  // 已结束的轮次与当前轮的来源：会话内提交新一轮时归档上一轮，打开历史时从会话详情的 batches 还原。
  const [pastRounds, setPastRounds] = useState<WorkspaceRound[]>([]);
  const [currentBasedOn, setCurrentBasedOn] = useState<RoundBase | null>(null);
  // 分叉会话的来源；前面 inherited 的轮次都来自它。
  const [forkSource, setForkSource] = useState<ForkSource | null>(null);
  const [viewedRound, setViewedRound] = useState<{ index: number; modelKey: string } | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isPromptAttention, setIsPromptAttention] = useState(false);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);
  const [account, setAccount] = useState<BillingAccount | null>(null);
  const [notice, setNotice] = useState("");
  const noticeTimerRef = useRef<number | null>(null);
  const [scenes, setScenes] = useState<SceneDefinition[]>(FALLBACK_SCENES);
  const [activeSceneKey, setActiveSceneKey] = useState<string | null>(null);
  const [sceneImages, setSceneImages] = useState<Record<string, File | null>>({});
  const [utmSource, setUtmSource] = useState("");
  const [showcaseCases, setShowcaseCases] = useState<ShowcaseCase[]>([]);
  const activeScene = scenes.find((item) => item.key === activeSceneKey) ?? null;

  const eventSourcesRef = useRef<EventSource[]>([]);
  const runStartedAtRef = useRef<Record<string, number>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dragCounterRef = useRef(0);
  const hasHydratedRef = useRef(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const motionRef = useRef<MotionLib | null>(null);
  const promptTrackedRef = useRef(false);

  // 运营埋点：落地访问（漏斗第一步），每次页面加载上报一次。
  useEffect(() => {
    track("landing_view");
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch("/api/scenes");
        if (!response.ok) return;
        const data = (await response.json()) as { scenes?: SceneDefinition[] };
        const list = data.scenes ?? [];
        if (!cancelled) setScenes(list);
        const details = await Promise.all(
          list.map(async (scene) => {
            try {
              const detailResponse = await apiFetch(`/api/scenes/${encodeURIComponent(scene.key)}`);
              if (!detailResponse.ok) return null;
              const detail = (await detailResponse.json()) as { cases?: { title: string; page_url: string }[] };
              const first = detail.cases?.[0];
              if (!first) return null;
              return { sceneKey: scene.key, sceneName: scene.name, title: first.title, pageUrl: first.page_url };
            } catch {
              return null;
            }
          }),
        );
        if (!cancelled) setShowcaseCases(details.filter((item): item is ShowcaseCase => item !== null));
      } catch {
        // 场景目录不可用时，首页仍可直接输入。
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sceneKey = params.get("scene");
    const source = params.get("utm_source") || "";
    const remix = params.get("remix");
    if (source) setUtmSource(source.slice(0, 64));
    if (!sceneKey) return;
    // 开发环境会把 effect 跑两遍，状态更新不能靠 ref 挡住第二次，否则深链场景选不中。
    setActiveSceneKey((current) => current ?? sceneKey);
    const linked = FALLBACK_SCENES.find((item) => item.key === sceneKey);
    if (linked) setPrompt((current) => current || linked.prompt_template);
    if (!remix) return;
    let cancelled = false;
    void (async () => {
      const response = await apiFetch(`/api/scenes/${encodeURIComponent(sceneKey)}`);
      if (!response.ok || cancelled) return;
      const detail = (await response.json()) as { cases?: SceneCase[] };
      const item = (detail.cases ?? []).find((entry) => entry.page_id === remix);
      if (item?.remix_prompt && !cancelled) setPrompt(item.remix_prompt);
    })();
    return () => {
      cancelled = true;
    };
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
      pastRounds,
      currentBasedOn,
      forkSource,
      createdAt: readCurrentSession()?.createdAt ?? now,
      updatedAt: now,
    };
    writeCurrentSession(session);
  }, [phase, conversationId, authUser?.id, batchId, runs, submittedPrompt, submittedFileNames, selectedModelKeys, appliedSkill, roundIndex, continueBase, pastRounds, currentBasedOn, forkSource]);

  async function initializeAuthState(): Promise<AuthUser | null> {
    const user = await loadMe();
    if (!user) {
      // 未登录也可能是已分配匿名 cookie 的访客：尝试拉取其历史。
      await loadHistory(historyScope, historySearch, null);
      const anonSession = readCurrentSession();
      if (anonSession && anonSession.userId) {
        localStorage.removeItem(CURRENT_SESSION_KEY);
      }
      hasHydratedRef.current = true;
      return null;
    }

    await loadHistory(historyScope, historySearch, user);
    const session = readCurrentSession();
    if (session && session.userId !== user.id) {
      localStorage.removeItem(CURRENT_SESSION_KEY);
    }
    hasHydratedRef.current = true;
    return user;
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
    void refreshAccount();
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
      setSelectedModelKeys(chooseSelectedModelKeys(availableModels, null));
      startNewChat();
    }
  }

  async function loadHistory(scope = historyScope, search = historySearch, _user = authUser): Promise<void> {
    // 登录用户与已分配 cookie 的匿名访客都可拉取自己的历史；后端按身份返回，无身份返回空。
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

  function showNotice(message: string) {
    setNotice(message);
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(""), 2800);
  }

  async function refreshAccount(): Promise<void> {
    const data = await fetchAccount();
    setAccount(data);
  }

  /** 老用户确认协议更新告知；点「知道了」或带着告知继续生成都算。 */
  async function acknowledgePolicyNotice(): Promise<void> {
    if (!authUser?.needs_policy_notice) return;
    setAuthUser((current) => (current ? { ...current, needs_policy_notice: false } : current));
    try {
      await apiFetch("/api/privacy/notice-ack", { method: "POST" });
    } catch {
      // 告知记录失败不影响使用，下次登录态刷新时会再次提示。
    }
  }

  async function loadModels(user: AuthUser | null): Promise<void> {
    try {
      const response = await apiFetch("/api/models");
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const data = (await response.json()) as ModelInfo[];
      setAvailableModels(data);
      const next = chooseSelectedModelKeys(data, user);
      if (next.length) setSelectedModelKeys(next);
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
      void (async () => {
        const user = await initializeAuthState();
        await loadModels(user);
        await refreshAccount();
      })();
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
    const model = availableModels.find((item) => item.key === key);
    // 匿名：高价模型可见但不可选，点击轻提示引导注册。
    if (!authUser && model && model.anon_allowed === false) {
      showNotice("该模型注册后可用，注册即可解锁全部模型");
      return;
    }
    setSelectedModelKeys((current) => {
      const isSelected = current.includes(key);
      // 匿名：单次最多生效 2 个模型，勾第 3 个时轻提示需注册。
      if (!isSelected && !authUser && current.length >= ANON_MAX_MODELS) {
        showNotice(`未登录最多同时使用 ${ANON_MAX_MODELS} 个模型，注册后可解锁更多`);
        return current;
      }
      const next = isSelected ? current.filter((item) => item !== key) : [...current, key];
      const ordered = availableModels.filter((m) => next.includes(m.key)).map((m) => m.key);
      const result = ordered.length ? ordered : current; // 至少保留一个
      writeSelectedModels(result);
      return result;
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitFiles = selectedFiles.length > 0 ? selectedFiles : Array.from(fileInputRef.current?.files ?? []);
    const inConversation = phase === "active" && Boolean(conversationId);
    const trimmedPrompt = prompt.trim();
    const effectivePrompt =
      trimmedPrompt ||
      (!inConversation && activeScene ? activeScene.prompt_template : "") ||
      (submitFiles.length > 0 ? "请根据我上传的文件生成一个网页。" : "");
    const validationError = validateFiles(submitFiles);

    if (!effectivePrompt) {
      return;
    }
    // 运营埋点：点击生成（捕获意图，含后续被登录/付费拦截的情况）。
    track("generate_click", {
      model_count: selectedModelKeys.length,
      has_file: submitFiles.length > 0,
      in_conversation: inConversation,
      scene_key: inConversation ? undefined : activeSceneKey ?? undefined,
      utm_source: utmSource || undefined,
    });
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
    // 告知仍在显示时继续生成，视为已知悉；先记下再建批次，这次生成才按新协议记授权。
    if (authUser?.needs_policy_notice) await acknowledgePolicyNotice();

    // 计费前置预判（后端仍为权威）：匿名免费次数用尽引导登录，登录用户余额不足引导充值。
    if (account) {
      if (account.is_anonymous && account.free_generations_remaining <= 0) {
        showNotice("免费体验已用完，登录后赠送 1000 积分");
        setIsAuthModalOpen(true);
        return;
      }
      if (!account.is_anonymous && account.total_balance <= 0) {
        showNotice("积分不足，正在前往充值…");
        router.push("/pricing");
        return;
      }
    }

    // 在工作区里提交即"在本会话内续写"：
    //  - 选中了某个结果节点(continueBase) -> 以该节点为基分支；基点在更早的轮次时，
    //    后端会分叉出一条新会话：这里先把历史截到基点那一轮，原会话保持不变；
    //  - 未选中 -> 每个模型各自接上自己上一轮结果，并行继续。
    // 首页(idle)提交则新建会话。
    const fileNames = submitFiles.map((file) => file.name);
    const hasFile = fileNames.length > 0;
    closeAllSources();
    setIsModelMenuOpen(false);

    const pendingRuns: RunState[] = selectedModelKeys.map((key) => makePendingRun(key, labelForModel(key, availableModels), hasFile));

    const shownPrompt = trimmedPrompt || effectivePrompt;
    const submittedAt = currentTimeMs();
    const finishedRound: WorkspaceRound | null =
      inConversation && runs.length > 0
        ? { prompt: submittedPrompt, basedOn: currentBasedOn, runs: runs.map((run) => ({ ...run, reasoning: "" })) }
        : null;
    const nextBasedOn = inConversation && continueBase ? locateRoundBase(continueBase.pageId) : null;
    const forkRoundIndex = nextBasedOn && nextBasedOn.roundIndex < pastRounds.length ? nextBasedOn.roundIndex : null;

    playTransition(() => {
      if (forkRoundIndex !== null) {
        setPastRounds((current) => current.slice(0, forkRoundIndex + 1).map((round) => ({ ...round, inherited: true })));
        setRoundIndex(forkRoundIndex + 1);
      } else if (finishedRound) {
        setPastRounds((current) => [...current, finishedRound]);
      } else if (!inConversation) {
        setPastRounds([]);
        setForkSource(null);
      }
      setCurrentBasedOn(nextBasedOn);
      setViewedRound(null);
      setPhase("active");
      setSubmittedPrompt(shownPrompt);
      setSubmittedFileNames(fileNames);
      setRuns(pendingRuns);
      setActiveModelKey(pendingRuns[0]?.modelKey ?? "");
      setFullscreenRun(null);
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
      } else if (activeScene) {
        formData.append("scene_key", activeScene.key);
        if (utmSource) formData.append("utm_source", utmSource);
        activeScene.image_slots.forEach((slot) => {
          const file = sceneImages[slot.key];
          if (!file) return;
          formData.append("images", file);
          formData.append("image_roles", slot.key);
        });
      }

      const response = await apiFetch("/api/generations", { method: "POST", body: formData });
      if (response.status === 401) {
        setIsAuthModalOpen(true);
      }
      if (response.status === 402) {
        const err = await readBillingError(response);
        const failMsg = err.message || "额度不足，无法创建";
        playTransition(() => {
          setPhase("idle");
          setRuns([]);
          setPrompt(effectivePrompt);
        });
        showNotice(failMsg);
        if (err.needLogin) {
          setIsAuthModalOpen(true);
        } else if (err.code === "insufficient_credits") {
          window.setTimeout(() => {
            router.push("/pricing");
          }, 900);
        }
        void refreshAccount();
        return;
      }
      if (response.status === 422) {
        const message = await readErrorMessage(response, "图片或资料没有通过检查");
        playTransition(() => {
          setPhase("idle");
          setRuns([]);
          setPrompt(trimmedPrompt);
        });
        setFileError(message);
        showNotice(message);
        return;
      }
      if (!response.ok) throw new Error(await readErrorMessage(response));

      const data = (await response.json()) as CreateGenerationResponse;
      setConversationId(data.conversation_id);
      setBatchId(data.batch_id);
      setAppliedSkill(null);
      if (data.forked_from) {
        setForkSource(mapForkSource(data.forked_from));
        showNotice(`已从第 ${data.forked_from.round_number} 轮分出新会话，原会话保持不变`);
      } else if (inConversation) {
        setRoundIndex((value) => value + 1);
      }
      setContinueBase(null);
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";

      const nextRuns = data.runs.map((run) => makeRunFromResponse(run, hasFile));
      nextRuns.forEach((run) => {
        runStartedAtRef.current[run.taskId] = submittedAt;
      });
      setRuns(nextRuns);
      setActiveModelKey(nextRuns[0]?.modelKey ?? "");
      void loadHistory();
      void refreshAccount();

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
    // 只有本次会话里发起的任务知道起点；从历史恢复后重连的任务交给工作区自己计时。
    const finishedDuration = (run: RunState) => {
      const startedAt = runStartedAtRef.current[taskId];
      return run.durationMs ?? (startedAt ? Date.now() - startedAt : undefined);
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
        durationMs: finishedDuration(run),
        progressSteps: run.progressSteps.map((step) => ({
          ...step,
          status: step.status === "pending" || step.status === "running" ? "completed" : step.status,
        })),
      }));
      void loadHistory();
      void refreshAccount();
      source.close();
    });

    source.addEventListener("failed", (event) => {
      const payload = parsePayload(event);
      patch((run) => ({
        ...run,
        status: "failed",
        statusText: "生成失败",
        errorMessage: payload.message ?? "页面生成失败，请稍后重试。",
        durationMs: finishedDuration(run),
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
    setIsModelMenuOpen(false);
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
      setActiveSceneKey(null);
      setSceneImages({});
      setActiveModelKey("");
      setFullscreenRun(null);
      setRoundIndex(0);
      setContinueBase(null);
      setPastRounds([]);
      setCurrentBasedOn(null);
      setForkSource(null);
      setViewedRound(null);
      setAppliedSkill(null);
    });
  }

  /** 按页面 id 找到续改基础所在的轮次和模型。 */
  function locateRoundBase(pageId: string): RoundBase | null {
    const rounds = [...pastRounds.map((round) => round.runs), runs];
    for (let index = rounds.length - 1; index >= 0; index -= 1) {
      const run = rounds[index].find((item) => item.pageId === pageId);
      if (run) return { modelKey: run.modelKey, modelLabel: run.modelLabel, roundIndex: index };
    }
    return null;
  }

  function restoreHistoryItem(item: HistoryItem) {
    return restoreConversation(item.id);
  }

  async function restoreConversation(id: string) {
    closeAllSources();
    try {
      const response = await apiFetch(`/api/conversations/${id}`);
      if (response.status === 401) {
        setIsAuthModalOpen(true);
        return;
      }
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const detail = (await response.json()) as ConversationDetailResponse;
      setActiveSceneKey(detail.scene_key ?? null);
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
    setIsModelMenuOpen(false);
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
    setRoundIndex(session.roundIndex ?? 0);
    setPastRounds(session.pastRounds ?? []);
    setCurrentBasedOn(session.currentBasedOn ?? null);
    setForkSource(session.forkSource ?? null);
    setViewedRound(null);
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
    window.requestAnimationFrame(() => {
      const textarea = document.querySelector<HTMLTextAreaElement>(".composer-wrap .prompt-card textarea");
      textarea?.focus();
    });
  }

  function handleFileChange(files: FileList | File[] | null) {
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
    // 运营埋点：本次会话首次产生输入意图，只上报一次。
    if (!promptTrackedRef.current && event.target.value.trim()) {
      promptTrackedRef.current = true;
      track("prompt_input");
    }
  }

  function isScenePrompt(text: string): boolean {
    const clean = text.trim();
    return !clean || scenes.some((item) => item.prompt_template.trim() === clean);
  }

  function handleSceneClick(scene: SceneDefinition, options: { keepActive?: boolean } = {}) {
    if (isGenerating) return;
    if (activeSceneKey === scene.key && !options.keepActive) {
      setActiveSceneKey(null);
      setSceneImages({});
      if (prompt.trim() === scene.prompt_template.trim()) setPrompt("");
      const url = new URL(window.location.href);
      url.searchParams.delete("scene");
      url.searchParams.delete("remix");
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
      return;
    }
    setActiveSceneKey(scene.key);
    setSceneImages({});
    // 只替换空输入或另一个场景的模板，用户自己写的字不覆盖。
    if (isScenePrompt(prompt)) setPrompt(scene.prompt_template);
    window.setTimeout(() => {
      const textarea = promptTextareaRef.current;
      if (!textarea) return;
      resizePromptTextarea(textarea);
      textarea.scrollIntoView({ behavior: "smooth", block: "center" });
      textarea.focus({ preventScroll: true });
    }, 60);
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
    const dropped = Array.from(event.dataTransfer.files);
    if (activeScene && activeScene.image_slots.length > 0) {
      const images = dropped.filter(isSceneImageFile);
      const documents = dropped.filter((file) => !isSceneImageFile(file));
      if (images.length > 0) {
        const problem = sceneImageProblem(images[0]);
        if (problem) {
          setFileError(problem);
        } else {
          const emptySlots = activeScene.image_slots.filter((slot) => !sceneImages[slot.key]);
          if (emptySlots.length > 1) {
            setFileError("请点下方对应的位置上传");
          } else if (emptySlots.length === 0) {
            setFileError("图片位置已经满了");
          } else {
            const slot = emptySlots[0];
            setSceneImages((current) => ({ ...current, [slot.key]: images[0] }));
            setFileError("");
          }
        }
      }
      if (documents.length > 0 && activeScene.accepts_documents !== false) {
        handleFileChange(documents);
      } else if (images.length === 0) {
        setFileError("这个场景请上传图片");
      }
      return;
    }
    handleFileChange(event.dataTransfer.files);
  }

  const isGenerating = runs.some((run) => run.status === "thinking" || run.status === "creating");
  const canSubmit = Boolean(prompt.trim()) || selectedFiles.length > 0 || (phase === "idle" && Boolean(activeScene));
  const isLongPrompt = submittedPrompt.length > 260;
  const isMulti = runs.length > 1;
  const fullscreenPreviewRun = fullscreenRun
    ? (runs.find((run) => (fullscreenRun.taskId && run.taskId === fullscreenRun.taskId) || (fullscreenRun.pageId && run.pageId === fullscreenRun.pageId)) ?? fullscreenRun)
    : null;
  const availableSelectableModels = availableModels.filter((model) => model.available);

  // 模型选择收成紧凑的弹出选择器：常态只占一个 pill，点击弹出浮层多选，
  // 让输入卡片保持轻盈，不被一整条模型列表撑臃肿。工作区里每轮都可以换模型。
  function renderModelPicker(compact = false) {
    if (availableModels.length === 0) return null;
    const selected = availableModels.filter((model) => selectedModelKeys.includes(model.key));
    const triggerLabel = selected.length === 1 ? selected[0].label : `${selected.length} 个模型`;
    const menuHint = !compact
      ? "可多选 · 并排生成便于对比"
      : continueBase
        ? `每轮可换 · 都基于「${continueBase.modelLabel}」的结果修改`
        : "每轮可换 · 新加入的模型接着上一轮的结果修改";
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
              <strong>{compact ? "这一轮的生成模型" : "生成模型"}</strong>
              <span>{menuHint}</span>
            </div>
            <div className="model-menu-list">
              {availableModels.map((model) => {
                const active = selectedModelKeys.includes(model.key);
                // 匿名：高价模型可见但置灰（不隐藏），点击轻提示引导注册。
                const anonLocked = !authUser && model.anon_allowed === false;
                return (
                  <button
                    key={model.key}
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={active}
                    className={`model-menu-item ${active ? "is-active" : ""} ${anonLocked ? "is-anon-locked" : ""}`}
                    onClick={() => model.available && toggleModel(model.key)}
                    disabled={!model.available}
                    title={
                      !model.available
                        ? `${model.label}（未配置密钥，暂不可用）`
                        : anonLocked
                          ? `${model.label}（注册后可用）`
                          : model.label
                    }
                  >
                    <span className="model-dot" aria-hidden="true" style={{ background: modelAccent(model.key) }} />
                    <span className="model-menu-item-label">{model.label}</span>
                    {!model.available && <span className="model-unavailable">未配置</span>}
                    {model.available && anonLocked && <span className="model-unavailable">登录可用</span>}
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

  function handleSidebarSurfaceClick(event: MouseEvent<HTMLElement>) {
    // 折叠态：点击侧边栏空白处也可展开；点到按钮/链接/输入等交互元素则交给它们各自处理。
    if (!isSidebarCollapsed) return;
    if ((event.target as HTMLElement).closest("button, a, input, label, [role='button']")) return;
    setIsSidebarCollapsed(false);
  }

  function renderHistorySidebar() {
    const isOnNewChat = phase === "idle" && !conversationId;
    return (
      <nav
        className={`history-sidebar ${isSidebarCollapsed ? "collapsed" : ""}`}
        aria-label="历史创建"
        onClick={handleSidebarSurfaceClick}
      >
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
                        {item.forkedFrom && (
                          <span
                            className="history-fork-tag"
                            title={`分叉自「${item.forkedFrom.title}」第 ${item.forkedFrom.roundNumber} 轮`}
                          >
                            <BranchIcon />
                            <span className="history-fork-tag-text">分叉自「{item.forkedFrom.title}」</span>
                            <span className="history-fork-tag-round">· 第 {item.forkedFrom.roundNumber} 轮</span>
                          </span>
                        )}
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
          {!isSidebarCollapsed && account && (
            <div className="sidebar-billing">
              <div className="billing-summary">
                {account.is_anonymous ? (
                  <>
                    <span className="billing-label">免费体验</span>
                    <span className="billing-amount">
                      {account.free_generations_remaining}
                      <span className="billing-amount-unit">/{account.free_generations_limit} 次</span>
                    </span>
                    <span className="billing-sub">登录后赠送 1000 积分</span>
                  </>
                ) : (
                  <>
                    <span className="billing-label">当前积分</span>
                    <span
                      className="billing-amount"
                      title={`充值积分 ${formatCredits(account.paid_balance)} · 赠送积分 ${formatCredits(account.gift_balance)}`}
                    >
                      {formatCredits(account.total_balance)}
                    </span>
                    <span className="billing-sub">≈ ¥{creditsToYuan(account.total_balance)}</span>
                  </>
                )}
              </div>
              <div className="billing-actions">
                <Link className="billing-link is-primary" href="/pricing">充值</Link>
                {authUser?.is_admin && (
                  <Link className="billing-link" href="/admin">财务后台</Link>
                )}
              </div>
            </div>
          )}
          <div className="sidebar-account-row">
            <button
              className="sidebar-user-button"
              type="button"
              onClick={() => {
                if (isSidebarCollapsed) {
                  setIsSidebarCollapsed(false);
                  return;
                }
                if (!authUser) setIsAuthModalOpen(true);
                else setIsPrivacyModalOpen(true);
              }}
              title={isSidebarCollapsed ? "展开侧边栏" : authUser ? `${authUser.phone} · 设置` : "登录 / 注册"}
              aria-label={isSidebarCollapsed ? "展开侧边栏" : authUser ? `当前用户 ${authUser.phone}` : "登录 / 注册"}
              aria-expanded={isSidebarCollapsed ? false : undefined}
            >
              <span className="sidebar-user-avatar"><UserIcon /></span>
              {!isSidebarCollapsed && (
                <span className="sidebar-user-text">
                  <strong>{authUser ? maskPhone(authUser.phone) : isAuthLoading ? "检查登录态" : "登录 / 注册"}</strong>
                  <small>{authUser ? "账号已登录 · 设置" : "登录后同步历史记录"}</small>
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
    const continueRound = compact && continueBase ? locateRoundBase(continueBase.pageId) : null;
    const continueForks = continueRound !== null && continueRound.roundIndex < pastRounds.length;

    return (
      <div className={promptWrapClassName}>
        {compact && continueBase && (
          <div className={`continue-indicator ${continueForks ? "is-fork" : ""}`}>
            <span className="continue-indicator-icon" aria-hidden="true"><BranchIcon /></span>
            {continueForks
              ? `将从第 ${continueRound.roundIndex + 1} 轮「${continueBase.modelLabel}」的结果分出新会话，原会话保持不变`
              : `将基于「${continueBase.modelLabel}」的结果进行修改`}
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
              {activeScene && activeScene.image_slots.length > 0 && activeScene.accepts_documents === false
                ? "松开即可放入图片"
                : "松开即可上传资料"}
            </div>
          )}
          {!compact && activeScene?.accepts_documents !== false && (
            <div className={`hero-dropzone ${selectedFiles.length > 0 ? "has-files" : ""}`}>
              {selectedFiles.length > 0 ? (
                <>
                  <div className="hero-dropzone-files" aria-label="已选择文件">
                    {selectedFiles.map((file, index) => (
                      <span className="hero-dropzone-file" key={`${file.name}-${file.size}-${index}`}>
                        <span className="hero-dropzone-file-icon" aria-hidden="true"><AttachmentIcon /></span>
                        <span className="hero-dropzone-file-name">{file.name}</span>
                        <span className="hero-dropzone-file-size">{formatFileSize(file.size)}</span>
                      </span>
                    ))}
                  </div>
                  <div className="hero-dropzone-actions">
                    <label className="hero-dropzone-link" title={FILE_UPLOAD_TITLE}>
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept={ACCEPTED_FILE_TYPES}
                        disabled={isGenerating}
                        onChange={(event) => handleFileChange(event.target.files)}
                      />
                      重新选择
                    </label>
                    <button className="hero-dropzone-link is-muted" type="button" onClick={clearSelectedFiles} disabled={isGenerating}>
                      清空
                    </button>
                  </div>
                </>
              ) : (
                <label className="hero-dropzone-empty" title={FILE_UPLOAD_TITLE} aria-label="上传文件">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept={ACCEPTED_FILE_TYPES}
                    disabled={isGenerating}
                    onChange={(event) => handleFileChange(event.target.files)}
                  />
                  <span className="hero-dropzone-icon" aria-hidden="true"><AttachmentIcon /></span>
                  <span className="hero-dropzone-text">
                    <strong>{activeScene ? `上传资料，做成${activeScene.name}` : "上传文档，一键转为网页"}</strong>
                    <small>支持 Word · Excel · PDF · PPT</small>
                  </span>
                  <span className="hero-dropzone-cta">选择文件</span>
                </label>
              )}
            </div>
          )}
          {!compact && activeScene && activeScene.image_slots.length > 0 && (
            <div className="hero-asset-row" aria-label="上传图片">
              {activeScene.image_slots.map((slot) => {
                const file = sceneImages[slot.key];
                return file ? (
                  <span className="hero-asset-chip is-filled" key={slot.key}>
                    <span className="hero-asset-chip-label">{slot.label}</span>
                    <span className="hero-asset-chip-name">{file.name}</span>
                    <button
                      type="button"
                      aria-label={`移除${slot.label}`}
                      onClick={() => setSceneImages((current) => ({ ...current, [slot.key]: null }))}
                      disabled={isGenerating}
                    >
                      <CloseIcon />
                    </button>
                  </span>
                ) : (
                  <label className="hero-asset-chip" key={slot.key}>
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      disabled={isGenerating}
                      onChange={(event) => {
                        const picked = event.target.files?.[0] ?? null;
                        event.target.value = "";
                        if (!picked) return;
                        const problem = sceneImageProblem(picked);
                        if (problem) {
                          setFileError(problem);
                          return;
                        }
                        setFileError("");
                        setSceneImages((current) => ({ ...current, [slot.key]: picked }));
                      }}
                    />
                    <span aria-hidden="true">＋</span>
                    上传{slot.label}
                  </label>
                );
              })}
            </div>
          )}
          <textarea
            ref={promptTextareaRef}
            value={prompt}
            onChange={handlePromptChange}
            placeholder={
              compact
                ? "继续描述你想调整的方向…"
                : activeScene
                  ? `说说你想要的${activeScene.name}`
                  : selectedFiles.length > 0
                    ? "补充说明（选填），例如页面用途、风格偏好或重点内容"
                    : "或直接说说你想做的页面，例如「面向客户的产品介绍页」"
            }
            rows={1}
            disabled={isGenerating}
          />
          {compact && selectedFiles.length > 0 && (
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
              {compact && (
                <label className="composer-upload is-compact" title={FILE_UPLOAD_TITLE} aria-label="上传文件">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept={ACCEPTED_FILE_TYPES}
                    disabled={isGenerating}
                    onChange={(event) => handleFileChange(event.target.files)}
                  />
                  <span className="composer-upload-icon" aria-hidden="true"><AttachmentIcon /></span>
                  <span className="composer-upload-text">上传</span>
                </label>
              )}
              {renderModelPicker(compact)}
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
              aria-label={isGenerating ? "正在生成" : compact ? "发送修改" : activeScene ? `生成${activeScene.name}` : "创建页面"}
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
        {!compact && scenes.length > 0 && (
          <div className="prompt-inspirations" role="list" aria-label="场景">
            <span className="prompt-inspirations-lead" aria-hidden="true">试试</span>
            {scenes.map((scene) => (
              <button
                key={scene.key}
                type="button"
                role="listitem"
                className={`prompt-inspiration-chip ${activeSceneKey === scene.key ? "is-active" : ""}`}
                onClick={() => handleSceneClick(scene)}
                disabled={isGenerating}
                aria-pressed={activeSceneKey === scene.key}
              >
                <span className="preset-emoji" aria-hidden="true">{scene.emoji}</span>
                {scene.name}
              </button>
            ))}
          </div>
        )}
        {!compact && showcaseCases.length > 0 && (
          <section className="case-gallery" aria-label="别人做的页面">
            <p className="case-gallery-lead">别人做的页面</p>
            <div className="case-gallery-grid">
              {showcaseCases.map((item) => {
                const scene = scenes.find((entry) => entry.key === item.sceneKey);
                return (
                  <article
                    key={item.sceneKey}
                    className={`case-gallery-card ${activeSceneKey === item.sceneKey ? "is-active" : ""}`}
                  >
                    <a
                      className="case-gallery-hit"
                      href={toAbsoluteUrl(item.pageUrl)}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`打开${item.title}`}
                    >
                      <CaseThumb url={item.pageUrl} title={item.title} />
                      <span className="case-gallery-meta">
                        <strong>{item.sceneName}</strong>
                        <em>{item.title}</em>
                      </span>
                    </a>
                    <button
                      className="case-gallery-open"
                      type="button"
                      onClick={() => scene && handleSceneClick(scene, { keepActive: true })}
                      disabled={isGenerating || !scene}
                    >
                      做同款
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        )}
        {fileError && (
          <div className="prompt-meta-row">
            <span className="file-error">{fileError}</span>
          </div>
        )}
      </div>
    );
  }

  const viewedPastRound = viewedRound ? pastRounds[viewedRound.index] ?? null : null;
  const roundNav: RoundNav | null =
    pastRounds.length > 0
      ? {
          items: [
            ...pastRounds.map((_, index) => ({ label: `第 ${index + 1} 轮`, live: false })),
            { label: `第 ${pastRounds.length + 1} 轮`, live: isGenerating },
          ],
          viewing: viewedRound?.index ?? pastRounds.length,
          onView: (index) => setViewedRound(index >= pastRounds.length ? null : { index, modelKey: "" }),
        }
      : null;

  const renderActions = (run: WorkspaceRun, layout: ActionLayout) => (
    <ResultActions
      run={run}
      layout={layout}
      canContinue={!isGenerating && Boolean(run.pageId)}
      selectedAsBase={continueBase?.pageId === run.pageId}
      printable={(activeScene?.features ?? []).includes("print_pdf")}
      onContinue={() => startContinueFrom(run as RunState)}
      onOpenPreview={() => setFullscreenRun(run as RunState)}
      onRequireLogin={(message) => {
        showNotice(message);
        setIsAuthModalOpen(true);
      }}
      onNotice={showNotice}
      shareGate={() => {
        // 匿名访客复制或分享时拦截并引导登录（避免未登录散播无归属链接）。
        if (!authUser) {
          showNotice("登录后即可复制并分享你的作品链接");
          setIsAuthModalOpen(true);
          return true;
        }
        return false;
      }}
    />
  );

  const renderPreview = (
    run: WorkspaceRun,
    options?: { presentation?: PreviewPresentation; onFrame?: (node: HTMLIFrameElement | null) => void; onReady?: () => void },
  ) => {
    const presentation = options?.presentation ?? "default";
    return (
      <PreviewCell
        run={run as RunState}
        presentation={presentation}
        onFrame={options?.onFrame}
        onReady={options?.onReady}
        selectedAsBase={continueBase?.pageId === run.pageId}
        dimmedByBase={runs.length <= 2 && continueBase ? continueBase.pageId !== run.pageId : false}
        sceneFeatures={activeScene?.features ?? []}
        actions={presentation === "default" ? renderActions(run, "bar") : undefined}
      />
    );
  };

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
            <SiteFooter onOpenDataSettings={() => setIsPrivacyModalOpen(true)} />
          </section>
        </main>
      ) : (
        <main key="workspace" className="workspace-shell">
          <WorkspaceProvider
            runs={runs as unknown as WorkspaceRun[]}
            activeModelKey={activeModelKey}
            onSelectModel={(key) => {
              setViewedRound(null);
              setActiveModelKey(key);
            }}
            appliedSkill={appliedSkill}
            isGenerating={isGenerating}
            continueBasePageId={continueBase?.pageId ?? ""}
            renderPreview={renderPreview}
            renderActions={renderActions}
            roundNav={roundNav}
          >
          <section className={`workspace-layout ${isSidebarCollapsed ? "sidebar-collapsed" : ""} ${isMulti ? "compare-mode" : ""} layout-gallery`}>
            {renderHistorySidebar()}
            <aside className="conversation-pane">
              <div className="conversation-scroll">
                {pastRounds.map((round, index) => {
                  const viewing = viewedRound?.index === index;
                  const laterBases = [...pastRounds.slice(index + 1).map((item) => item.basedOn), currentBasedOn];
                  const pickedBy = laterBases.findIndex((base) => base?.roundIndex === index);
                  const picked = pickedBy >= 0 ? laterBases[pickedBy] : null;
                  const endsInherited = Boolean(round.inherited) && !pastRounds[index + 1]?.inherited;
                  return (
                    <Fragment key={`round-${index}`}>
                      <div className={`chat-round is-past ${round.inherited ? "is-inherited" : ""}`}>
                        <div className="chat-message user-message">
                          <div className="user-message-meta">
                            <span>第 {index + 1} 轮</span>
                            <RoundSource roundIndex={index} basedOn={round.basedOn} runs={round.runs} />
                          </div>
                          <p>{round.prompt}</p>
                        </div>
                        <WorkspaceProvider
                          runs={round.runs}
                          activeModelKey=""
                          onSelectModel={(key) => setViewedRound({ index, modelKey: key })}
                          appliedSkill={null}
                          isGenerating={false}
                          continueBasePageId=""
                          renderPreview={renderPreview}
                          renderActions={renderActions}
                          pinnedModelKey={viewing ? viewedRound?.modelKey : undefined}
                          trackUnseen={false}
                        >
                          <GenerationProgress
                            history={{
                              viewing,
                              pickedKey: picked?.modelKey ?? "",
                              nextLabel: `第 ${index + pickedBy + 2} 轮`,
                              onView: () => setViewedRound((current) => (current?.index === index ? current : { index, modelKey: "" })),
                            }}
                          />
                        </WorkspaceProvider>
                      </div>
                      {endsInherited && forkSource && (
                        <div className="fork-divider" role="note">
                          <span className="fork-divider-label">
                            <BranchIcon />
                            <span className="fork-divider-text">
                              以上 {index + 1} 轮来自{forkSource.available ? "" : "已删除的会话"}「{forkSource.title}」
                            </span>
                          </span>
                          {forkSource.available && (
                            <button type="button" onClick={() => void restoreConversation(forkSource.conversationId)}>
                              查看原会话
                            </button>
                          )}
                        </div>
                      )}
                    </Fragment>
                  );
                })}
                <div className="chat-round is-current">
                <div className={`chat-message user-message ${isLongPrompt ? "long-message" : ""}`} data-anim-stagger>
                  <div className="user-message-meta">
                    <span>{pastRounds.length > 0 ? `第 ${roundIndex + 1} 轮 · 当前` : "你的需求"}</span>
                    {pastRounds.length > 0 ? (
                      <RoundSource roundIndex={roundIndex} basedOn={currentBasedOn} runs={runs as unknown as WorkspaceRun[]} />
                    ) : (
                      <span>
                        {submittedPrompt.length} 字
                        {submittedFileNames.length ? ` · ${submittedFileNames.length} 个文件` : ""}
                        {roundIndex > 0 ? ` · 第 ${roundIndex + 1} 轮` : ""}
                      </span>
                    )}
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

                <GenerationProgress />
                </div>
              </div>
              <div className="composer-wrap">{renderPromptForm(true)}</div>
            </aside>

            <section className="preview-pane">
              {viewedPastRound ? (
                <WorkspaceProvider
                  key={`viewed-round-${viewedRound?.index}`}
                  runs={viewedPastRound.runs}
                  activeModelKey=""
                  onSelectModel={(key) => setViewedRound((current) => (current ? { ...current, modelKey: key } : current))}
                  appliedSkill={null}
                  isGenerating={false}
                  continueBasePageId={continueBase?.pageId ?? ""}
                  renderPreview={renderPreview}
                  renderActions={renderActions}
                  pinnedModelKey={viewedRound?.modelKey}
                  roundNav={roundNav}
                  trackUnseen={false}
                >
                  <GenerationResults />
                </WorkspaceProvider>
              ) : (
                <GenerationResults />
              )}
            </section>
          </section>
          </WorkspaceProvider>
        </main>
      )}
      {notice && <div className="app-toast" role="status">{notice}</div>}
      {fullscreenPreviewRun && <FullscreenPreviewModal run={fullscreenPreviewRun} onClose={() => setFullscreenRun(null)} />}
      {isAuthModalOpen && <AuthModal onClose={() => setIsAuthModalOpen(false)} onLogin={handleLogin} />}
      {isPrivacyModalOpen && (
        <DataPrivacyModal
          onClose={() => setIsPrivacyModalOpen(false)}
          onChanged={(allow) => setAuthUser((current) => (current ? { ...current, allow_training: allow } : current))}
        />
      )}
      {authUser?.needs_policy_notice && (
        <PolicyNoticeBar
          onAcknowledge={() => void acknowledgePolicyNotice()}
          onOpenSettings={() => {
            void acknowledgePolicyNotice();
            setIsPrivacyModalOpen(true);
          }}
        />
      )}
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

function currentTimeMs(): number {
  return Date.now();
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

function readCurrentSession(): StoredSession | null {
  return readJson<StoredSession>(CURRENT_SESSION_KEY);
}

function writeCurrentSession(session: StoredSession): void {
  localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify(session));
}

function chooseSelectedModelKeys(data: ModelInfo[], user: AuthUser | null): string[] {
  const availableKeys = new Set(data.filter((model) => model.available).map((model) => model.key));
  const restored = (readSelectedModels() ?? []).filter((key) => availableKeys.has(key));
  // 未登录：本机记录里只要混有注册后才可用的模型，就改用匿名默认，而不是留下半套勾选。
  if (!user) {
    const allowed = new Set(
      data.filter((model) => model.available && model.anon_allowed !== false).map((model) => model.key),
    );
    const anonRestored = restored.filter((key) => allowed.has(key));
    if (restored.length > 0 && anonRestored.length === restored.length) {
      return anonRestored.slice(0, ANON_MAX_MODELS);
    }
    const anonDefaults = data.filter((model) => model.available && model.anon_default).map((model) => model.key);
    if (anonDefaults.length) return anonDefaults;
  }
  if (restored.length) return restored;
  const defaults = data.filter((model) => model.available && model.is_default).map((model) => model.key);
  if (defaults.length) return defaults;
  const firstAvailable = data.find((model) => model.available);
  return firstAvailable ? [firstAvailable.key] : [];
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
    forkedFrom: item.forked_from ? mapForkSource(item.forked_from) : null,
  };
}

function mapForkSource(source: ForkSourceResponse): ForkSource {
  return {
    conversationId: source.conversation_id,
    title: source.title || "未命名页面",
    roundNumber: source.round_number,
    available: source.available,
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
  const roundOfPage = new Map<string, number>();
  batches.forEach((batch, index) => batch.nodes.forEach((node) => roundOfPage.set(node.page_id, index)));

  const roundBase = (batch: ConversationBatchResponse): RoundBase | null => {
    if (!batch.base_page_id) return null;
    const roundIndex = roundOfPage.get(batch.base_page_id);
    if (roundIndex === undefined) return null;
    const node = batches[roundIndex].nodes.find((item) => item.page_id === batch.base_page_id);
    if (!node) return null;
    return { modelKey: node.model_key ?? "", modelLabel: node.model_label ?? node.model_key ?? "上一结果", roundIndex };
  };

  const pastRounds: WorkspaceRound[] = batches.slice(0, -1).map((batch) => ({
    prompt: batch.user_prompt || batch.prompt,
    basedOn: roundBase(batch),
    runs: buildRunsFromBatch(batch) as WorkspaceRun[],
    inherited: Boolean(batch.inherited),
  }));

  const now = new Date().toISOString();
  return {
    conversationId: detail.id,
    batchId: latest?.batch_id ?? "",
    title: detail.title,
    prompt: latest?.user_prompt || latest?.prompt || detail.title,
    fileNames: latest?.file_names ?? [],
    selectedModelKeys: latest?.selected_models ?? [],
    runs: latest ? buildRunsFromBatch(latest) : [],
    roundIndex: Math.max(0, batches.length - 1),
    pastRounds,
    currentBasedOn: latest ? roundBase(latest) : null,
    forkSource: detail.forked_from ? mapForkSource(detail.forked_from) : null,
    createdAt: detail.created_at ?? now,
    updatedAt: detail.updated_at ?? now,
  };
}

function buildRunsFromBatch(batch: ConversationBatchResponse): RunState[] {
  const hasFile = (batch.file_names ?? []).length > 0;
  return batch.nodes.map((node) => {
    const status = mapNodeStatus(node.page_status, node.generation_status);
    const usageSummary = buildUsageSummaryFromDetail(node.usage, node.billing);
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
      durationMs: node.duration_ms ?? undefined,
    };
  });
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
    creditsCharged: payload.billing?.credits_charged,
    discountCredits: payload.billing?.discount_credits,
  };
}

function buildUsageSummaryFromDetail(
  usage?: ApiUsagePayload | null,
  billing?: { credits_charged?: number; discount_credits?: number } | null,
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
    creditsCharged: billing?.credits_charged,
    discountCredits: billing?.discount_credits,
  };
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
