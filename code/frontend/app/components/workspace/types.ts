export type GenerationStatus = "idle" | "thinking" | "creating" | "completed" | "failed";

export type ProgressStepId =
  | "upload_file"
  | "parse_file"
  | "compress_document"
  | "model_thinking"
  | "model_output"
  | "deploy"
  | "database"
  | "upload";

export type ProgressStepStatus = "pending" | "running" | "completed" | "failed";

export type ProgressStep = {
  id: ProgressStepId;
  title: string;
  description: string;
  status: ProgressStepStatus;
  outputTokens?: number;
  tokenSource?: "actual" | "estimated";
};

export type UsageCostSummary = {
  inputTokens: number;
  outputTokens: number;
  totalTokens?: number;
  cachedInputTokens?: number;
  reasoningTokens?: number;
  creditsCharged?: number;
  discountCredits?: number;
};

/** 工作区插槽看到的单个模型运行。与页面里的 RunState 结构对齐。 */
export type WorkspaceRun = {
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
  /** 服务端记录的生成用时；有值时优先于前端计时（从历史还原的结果没有前端计时）。 */
  durationMs?: number;
};

/** 这一轮基于哪个结果修改。roundIndex 是被选结果所在的轮次（从 0 开始）。 */
export type RoundBase = { modelKey: string; modelLabel: string; roundIndex: number };

/** 会话里已经结束的一轮：用户这轮说了什么、基于哪个结果改、各模型的产出。 */
export type WorkspaceRound = {
  prompt: string;
  basedOn: RoundBase | null;
  runs: WorkspaceRun[];
};

/** 结果区的轮次切换。index 从 0 开始，最后一个是当前轮。 */
export type RoundNav = {
  items: { label: string; live: boolean }[];
  viewing: number;
  onView: (index: number) => void;
};

export type PreviewPresentation = "default" | "stage" | "thumb";

export type AppliedSkill = { key: string; name: string } | null;
