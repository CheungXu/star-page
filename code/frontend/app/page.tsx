"use client";

import type { FormEvent, SyntheticEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

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

type ProgressStepId = "model_output" | "database" | "upload";
type ProgressStepStatus = "pending" | "running" | "completed" | "failed";

type ProgressStep = {
  id: ProgressStepId;
  title: string;
  description: string;
  status: ProgressStepStatus;
  outputTokens?: number;
  tokenSource?: "actual" | "estimated";
};

function createInitialProgressSteps(): ProgressStep[] {
  return [
    {
      id: "model_output",
      title: "模型输出答案",
      description: "等待模型开始输出 HTML",
      status: "pending",
      outputTokens: 0,
      tokenSource: "estimated",
    },
    {
      id: "upload",
      title: "上传文件中",
      description: "等待 HTML 文件生成完成",
      status: "pending",
    },
    {
      id: "database",
      title: "记录数据库",
      description: "等待页面版本和任务状态写入",
      status: "pending",
    },
  ];
}

const PREVIEW_VIEWPORT_WIDTH = 1200;
const PREVIEW_DEFAULT_HEIGHT = 900;

export default function HomePage() {
  const [prompt, setPrompt] = useState("");
  const [submittedPrompt, setSubmittedPrompt] = useState("");
  const [status, setStatus] = useState<GenerationStatus>("idle");
  const [reasoning, setReasoning] = useState("");
  const [statusText, setStatusText] = useState("描述你想创建的页面");
  const [pageUrl, setPageUrl] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState("");
  const [previewMetrics, setPreviewMetrics] = useState({
    viewportWidth: PREVIEW_VIEWPORT_WIDTH,
    contentHeight: PREVIEW_DEFAULT_HEIGHT,
    scale: 0.5,
  });
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>(createInitialProgressSteps);
  const eventSourceRef = useRef<EventSource | null>(null);
  const previewStageRef = useRef<HTMLDivElement | null>(null);
  const previewIframeRef = useRef<HTMLIFrameElement | null>(null);

  const absolutePageUrl = useMemo(() => {
    if (!pageUrl) return "";
    if (pageUrl.startsWith("http://") || pageUrl.startsWith("https://")) {
      return pageUrl;
    }
    return `${window.location.origin}${pageUrl}`;
  }, [pageUrl]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPrompt = prompt.trim();

    if (!trimmedPrompt) {
      setStatusText("先描述你想创建的页面");
      return;
    }

    if (status === "thinking" || status === "creating") {
      return;
    }

    eventSourceRef.current?.close();
    setSubmittedPrompt(trimmedPrompt);
    setStatus("thinking");
    setReasoning("");
    setPageUrl("");
    setCopied(false);
    setCopyFeedback("");
    setPreviewMetrics({
      viewportWidth: PREVIEW_VIEWPORT_WIDTH,
      contentHeight: PREVIEW_DEFAULT_HEIGHT,
      scale: 0.5,
    });
    setErrorMessage("");
    setStatusText("正在提交你的需求...");
    setProgressSteps(createInitialProgressSteps());
    setPrompt("");

    try {
      const response = await fetch("/api/generations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: trimmedPrompt }),
      });

      if (!response.ok) {
        throw new Error("创建生成任务失败");
      }

      const data = (await response.json()) as CreateGenerationResponse;
      connectToEvents(data.task_id);
    } catch (error) {
      setStatus("failed");
      setErrorMessage(error instanceof Error ? error.message : "创建生成任务失败");
      setStatusText("生成失败");
    }
  }

  function connectToEvents(taskId: string) {
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
      }
    });

    source.addEventListener("answer_started", () => {
      setStatus("creating");
      setStatusText("页面创建中...");
    });

    source.addEventListener("progress", (event) => {
      const payload = parsePayload(event);
      if (!payload.step || !payload.status) return;

      setProgressSteps((current) =>
        current.map((step) =>
          step.id === payload.step
            ? {
                ...step,
                status: payload.status ?? step.status,
                description: payload.text ?? step.description,
                outputTokens:
                  payload.output_tokens !== undefined ? payload.output_tokens : step.outputTokens,
                tokenSource: payload.token_source ?? step.tokenSource,
              }
            : step,
        ),
      );
    });

    source.addEventListener("completed", (event) => {
      const payload = parsePayload(event);
      setStatus("completed");
      setStatusText("页面已创建完成");
      setPageUrl(payload.url ?? "");
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

      const root = doc.documentElement;
      const body = doc.body;
      const contentHeight = Math.max(
        root.scrollHeight,
        body?.scrollHeight ?? 0,
        root.offsetHeight,
        body?.offsetHeight ?? 0,
        PREVIEW_DEFAULT_HEIGHT,
      );
      const availableWidth = Math.max(stage.clientWidth - 28, 320);
      const scale = Math.min(1, availableWidth / PREVIEW_VIEWPORT_WIDTH);

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

  function renderPromptForm(compact = false) {
    return (
      <form className={`prompt-card ${compact ? "compact-prompt" : ""}`} onSubmit={handleSubmit}>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="例如：做一个面向独立开发者的 AI 工具产品介绍页，风格简洁、高级，带价格区块"
          rows={compact ? 2 : 3}
          disabled={isGenerating}
        />
        <div className="prompt-actions">
          <span>{statusText}</span>
          <button type="submit" disabled={isGenerating} aria-label="创建页面">
            {isGenerating ? "生成中" : "创建"}
          </button>
        </div>
      </form>
    );
  }

  if (status === "idle") {
    return (
    <main className="page-shell">
      <section className="hero">
        <div className="brand-mark">✦</div>
        <h1>想做什么页面？</h1>
        <p className="subtitle">描述你的想法，我会生成一个可以分享的 HTML 页面。</p>

        {renderPromptForm()}
      </section>
    </main>
    );
  }

  return (
    <main className="workspace-shell">
      <section className="workspace-layout">
        <aside className="conversation-pane">
          <div className="conversation-scroll">
            <div className={`chat-message user-message ${isLongPrompt ? "long-message" : ""}`}>
              <div className="user-message-meta">
                <span>你的需求</span>
                <span>{submittedPrompt.length} 字</span>
              </div>
              <p>{submittedPrompt}</p>
            </div>

            <div className="chat-message assistant-message reasoning-message">
              <div className="assistant-label">思考过程</div>
              {reasoning ? (
                <pre>{reasoning}</pre>
              ) : (
                <p className="muted inline-muted">模型开始思考后，会在这里展示 reasoning_content。</p>
              )}
            </div>

            <div className="chat-message assistant-message progress-message">
              <div className="assistant-label">创建节点</div>
              <div className="progress-list">
                {progressSteps.map((step) => (
                  <div className={`progress-item ${step.status}`} key={step.id}>
                    <span className="progress-icon">{getProgressIcon(step.status)}</span>
                    <div>
                      <div className="progress-title-row">
                        <strong>{step.title}</strong>
                        {step.id === "model_output" && (
                          <span className="token-pill">
                            输出 {step.outputTokens ?? 0} tokens
                            {step.tokenSource === "estimated" ? "（估算）" : ""}
                          </span>
                        )}
                      </div>
                      <p>{step.description}</p>
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
          <article className="panel preview-panel workspace-preview">
            <div className="panel-heading">
              <span className="dot accent" />
              <h2>页面预览</h2>
            </div>

            {status === "completed" && absolutePageUrl && (
              <>
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
                <div className="link-actions">
                  <a href={absolutePageUrl} target="_blank" rel="noreferrer">
                    打开页面
                  </a>
                  <button type="button" onClick={copyPageUrl}>
                    复制链接
                  </button>
                </div>
                {copyFeedback && (
                  <div className={`copy-feedback ${copied ? "success" : "error"}`} role="status">
                    {copyFeedback}
                  </div>
                )}
              </>
            )}

            {(status === "thinking" || status === "creating") && (
              <div className="preview-empty">
                <div className="preview-empty-icon">▧</div>
                <h3>页面生成后会出现在这里</h3>
                <p>左侧会持续展示模型思考和创建节点，右侧专注预览最终网页。</p>
              </div>
            )}

            {status === "failed" && <p className="error-text">{errorMessage}</p>}
          </article>
        </section>
      </section>
    </main>
  );
}

function parsePayload(event: Event): SsePayload {
  const message = event as MessageEvent<string>;
  return JSON.parse(message.data) as SsePayload;
}

function getProgressIcon(status: ProgressStepStatus): string {
  if (status === "completed") return "✓";
  if (status === "running") return "•";
  if (status === "failed") return "!";
  return "";
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
