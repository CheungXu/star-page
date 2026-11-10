"use client";

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { apiFetch } from "../../lib/api";
import { copyTextToClipboard, toAbsoluteUrl } from "../../lib/links";
import { isDemoRun, publishDemo, type SharePublication } from "./demo";
import { DEFAULT_SUBTITLE, downloadDataUrl, fetchPageTitle, loadImage, renderPoster, type PosterAlign } from "./share-poster";
import { modelAccent } from "./progress-utils";
import type { ActionLayout, WorkspaceRun } from "./types";
import "./actions.css";

export type ResultActionsProps = {
  run: WorkspaceRun;
  layout: ActionLayout;
  canContinue: boolean;
  selectedAsBase: boolean;
  printable: boolean;
  onContinue: () => void;
  onOpenPreview: () => void;
  /** 返回 true 表示被拦下（匿名访客需要先登录）。 */
  shareGate: () => boolean;
  onRequireLogin: (message: string) => void;
  onNotice: (message: string) => void;
};

type ShareState = { kind: "loading" } | { kind: "ready"; data: SharePublication } | { kind: "error" };

/** 单个结果的四个操作：新标签页打开、放大预览、分享、继续改。按 layout 摆成卡片底栏、舞台顶栏或胶片焦点卡。 */
export function ResultActions({
  run,
  layout,
  canContinue,
  selectedAsBase,
  printable,
  onContinue,
  onOpenPreview,
  shareGate,
  onRequireLogin,
  onNotice,
}: ResultActionsProps) {
  const absoluteUrl = useMemo(() => toAbsoluteUrl(run.pageUrl), [run.pageUrl]);
  const ready = run.status === "completed" && Boolean(absoluteUrl);
  const demo = isDemoRun(run);
  const [share, setShare] = useState<ShareState | null>(null);
  const [shareAnchor, setShareAnchor] = useState<HTMLButtonElement | null>(null);
  const shareSeqRef = useRef(0);

  async function publish() {
    const seq = ++shareSeqRef.current;
    setShare({ kind: "loading" });
    try {
      const data = demo ? await publishDemo(run) : await publishPage(run.pageId);
      if (seq !== shareSeqRef.current) return;
      if (data === "need_login") {
        setShare(null);
        onRequireLogin("登录后才能分享");
        return;
      }
      setShare({ kind: "ready", data });
    } catch {
      if (seq === shareSeqRef.current) setShare({ kind: "error" });
    }
  }

  function toggleShare() {
    if (share) {
      closeShare();
      return;
    }
    if (!ready) return;
    if (!demo && shareGate()) return;
    void publish();
  }

  function closeShare() {
    shareSeqRef.current += 1;
    setShare(null);
  }

  function exportPdf(target: string) {
    const join = target.includes("?") ? "&" : "?";
    const opened = window.open(`${target}${join}print=1`, "_blank");
    if (!opened) {
      onNotice("浏览器拦住了打印窗口，请允许弹窗后再试");
      return;
    }
    opened.opener = null;
  }

  const shareOpen = Boolean(share);
  const continueText = selectedAsBase ? "已选为基础" : "以此结果继续";
  const shareProps = {
    ref: setShareAnchor,
    type: "button" as const,
    onClick: toggleShare,
    disabled: !ready,
    "aria-expanded": shareOpen,
    "aria-haspopup": "dialog" as const,
  };
  let body: ReactNode;

  if (layout === "chrome") {
    const address = splitAddress(absoluteUrl);
    body = (
      <div className="tb-chrome-actions">
        {ready ? (
          <a className="tb-address" href={absoluteUrl} target="_blank" rel="noreferrer" title="在新标签页打开">
            <LockIcon />
            <span className="tb-address-text">
              <b>{address.host}</b>
              <span>{address.path}</span>
            </span>
            <span className="tb-address-open">
              <span>新标签页打开</span>
              <OpenIcon />
            </span>
          </a>
        ) : (
          <span className="tb-address is-disabled">
            <LockIcon />
            <span className="tb-address-text">
              <b>{run.status === "failed" ? "这个版本没有生成成功" : "页面生成中…"}</b>
            </span>
          </span>
        )}
        <span className="tb-chrome-tools">
          <button type="button" className="tb-btn is-icon" onClick={onOpenPreview} disabled={!ready} title="放大预览" aria-label="放大预览">
            <ExpandIcon />
          </button>
          <i className="tb-chrome-sep" aria-hidden="true" />
          <button {...shareProps} className={`tb-btn is-share ${shareOpen ? "is-open" : ""}`}>
            <ShareIcon />
            <span>分享</span>
          </button>
          {canContinue && ready && (
            <button type="button" className="tb-btn is-primary" onClick={onContinue} aria-pressed={selectedAsBase} title={continueText}>
              <BranchIcon />
              <span>{continueText}</span>
            </button>
          )}
        </span>
      </div>
    );
  } else if (layout === "continue" || layout === "tab") {
    if (!canContinue || !ready) return null;
    body = (
      <button
        type="button"
        className={`${layout === "tab" ? "tb-continue-tab" : "tb-thumb-cta"} ${selectedAsBase ? "is-picked" : ""}`}
        onClick={(event) => {
          event.stopPropagation();
          onContinue();
        }}
        aria-pressed={selectedAsBase}
        title={selectedAsBase ? "下一轮将基于这个结果修改" : "以此结果继续修改"}
      >
        {selectedAsBase ? <CheckIcon /> : <BranchIcon />}
        <span>{selectedAsBase ? (layout === "tab" ? "已选" : "已选为基础") : "继续改"}</span>
      </button>
    );
  } else if (layout === "card") {
    body = (
      <div className="tb-card-actions">
        <a className="tb-act" href={absoluteUrl} target="_blank" rel="noreferrer" title="在新标签页打开">
          <OpenIcon />
          <span>打开</span>
        </a>
        <button type="button" className="tb-act" onClick={onOpenPreview} title="放大预览">
          <ExpandIcon />
          <span>放大</span>
        </button>
        <button {...shareProps} className={`tb-act is-share ${shareOpen ? "is-open" : ""}`} title="分享">
          <ShareIcon />
          <span>分享</span>
        </button>
      </div>
    );
  } else if (layout === "cardRow" || layout === "spot") {
    const prefix = layout === "spot" ? "tb-spot" : "tb-row";
    body = (
      <div className={`${prefix}-actions`}>
        <span className={`${prefix}-tools`}>
          <a className={`${prefix}-ico`} href={absoluteUrl} target="_blank" rel="noreferrer" aria-label="在新标签页打开" data-tip="新标签页打开" onClick={(event) => event.stopPropagation()}>
            <OpenIcon />
          </a>
          <button
            type="button"
            className={`${prefix}-ico`}
            onClick={(event) => {
              event.stopPropagation();
              onOpenPreview();
            }}
            aria-label="放大预览"
            data-tip="放大预览"
          >
            <ExpandIcon />
          </button>
          <button
            {...shareProps}
            onClick={(event) => {
              event.stopPropagation();
              toggleShare();
            }}
            className={`${prefix}-ico is-share ${shareOpen ? "is-open" : ""}`}
            aria-label="分享"
            data-tip="分享"
          >
            <ShareIcon />
          </button>
        </span>
        {canContinue && (
          <button
            type="button"
            className={`${prefix}-continue ${selectedAsBase ? "is-picked" : ""}`}
            onClick={(event) => {
              event.stopPropagation();
              onContinue();
            }}
            aria-pressed={selectedAsBase}
            title={selectedAsBase ? "下一轮将基于这个结果修改" : "以此结果继续修改"}
          >
            {selectedAsBase ? <CheckIcon /> : <BranchIcon />}
            {selectedAsBase ? "已选" : "继续改"}
          </button>
        )}
      </div>
    );
  } else {
    body = (
      <div className="link-actions">
        <div className="link-secondary-actions">
          <a className="link-icon-button" href={absoluteUrl} target="_blank" rel="noreferrer" aria-label={`在新标签页打开 ${run.modelLabel} 生成页面`} title="在新标签页打开">
            <OpenIcon />
            <span>打开</span>
          </a>
          <button className="link-icon-button" type="button" onClick={onOpenPreview} title="放大预览">
            <ExpandIcon />
            <span>放大</span>
          </button>
          <button {...shareProps} className={`link-icon-button is-share ${shareOpen ? "is-open" : ""}`}>
            <ShareIcon />
            <span>分享</span>
          </button>
        </div>
        {canContinue && (
          <button className="continue-button" type="button" onClick={onContinue} aria-pressed={selectedAsBase}>
            <span className="button-icon" aria-hidden="true"><BranchIcon /></span>
            {continueText}
          </button>
        )}
      </div>
    );
  }

  return (
    <>
      {body}
      {share && shareAnchor && (
        <SharePopover
          run={run}
          anchor={shareAnchor}
          state={share}
          printable={printable}
          onPrint={exportPdf}
          onRetry={() => void publish()}
          onClose={closeShare}
          onNotice={onNotice}
        />
      )}
    </>
  );
}

async function publishPage(pageId: string): Promise<SharePublication | "need_login"> {
  const response = await apiFetch("/api/publications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_id: pageId }),
  });
  if (response.status === 401) return "need_login";
  if (!response.ok) throw new Error("发布失败");
  const data = (await response.json()) as {
    url: string;
    qr_url: string;
    republished?: boolean;
    external_view_count?: number;
  };
  const version = Date.now();
  return {
    url: data.url,
    openUrl: data.url,
    qrUrl: `${toAbsoluteUrl(data.qr_url)}?v=${version}`,
    republished: Boolean(data.republished),
    externalViews: data.external_view_count ?? 0,
  };
}

function splitAddress(url: string): { host: string; path: string } {
  try {
    const parsed = new URL(url);
    return { host: parsed.host, path: `${parsed.pathname}${parsed.search}` };
  } catch {
    return { host: url, path: "" };
  }
}

/** 把固定链接拆成「域名与前缀」和「末段」，末段加粗，一眼看出这是哪条链接。 */
function splitShareLink(url: string): { prefix: string; slug: string } {
  try {
    const parsed = new URL(url);
    const segments = parsed.pathname.split("/").filter(Boolean);
    const slug = segments.pop() ?? "";
    return { prefix: `${parsed.host}/${segments.length ? `${segments.join("/")}/` : ""}`, slug };
  } catch {
    return { prefix: "", slug: url };
  }
}

type PopoverPosition = { top: number; left: number; placement: "top" | "bottom"; arrow: number };

/** 浮层贴着分享按钮出现：下方放得下就朝下，否则朝上；水平居中对齐按钮并夹在视口内。 */
function usePopoverPosition(anchor: HTMLElement, popover: HTMLElement | null) {
  const [position, setPosition] = useState<PopoverPosition | null>(null);

  useLayoutEffect(() => {
    if (!popover) return;
    const update = () => {
      const rect = anchor.getBoundingClientRect();
      const width = popover.offsetWidth;
      const height = popover.offsetHeight;
      const gap = 10;
      const margin = 12;
      const below = window.innerHeight - rect.bottom - gap - margin;
      const placement = below >= height || below >= rect.top - gap - margin ? "bottom" : "top";
      const top = placement === "bottom" ? rect.bottom + gap : rect.top - gap - height;
      const center = rect.left + rect.width / 2;
      const left = Math.max(margin, Math.min(center - width / 2, window.innerWidth - width - margin));
      const arrow = Math.max(18, Math.min(width - 18, center - left));
      setPosition((current) =>
        current && current.top === top && current.left === left && current.placement === placement && current.arrow === arrow
          ? current
          : { top, left, placement, arrow },
      );
    };
    const frame = requestAnimationFrame(update);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(popover);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      observer?.disconnect();
    };
  }, [anchor, popover]);

  return position;
}

const SHARE_META_PREFIX = "star-page-share-meta:";
const TITLE_MAX = 28;
const SUBTITLE_MAX = 36;

type ShareMetaDraft = { title?: string; subtitle?: string; align?: PosterAlign };

function readShareMeta(pageId: string): ShareMetaDraft {
  try {
    const raw = localStorage.getItem(SHARE_META_PREFIX + pageId);
    return raw ? (JSON.parse(raw) as ShareMetaDraft) : {};
  } catch {
    return {};
  }
}

/** 分享图的标题 / 副标题：默认取生成页自己的 <title>，用户改过的按页面记在本机。 */
function useShareMeta(run: WorkspaceRun) {
  const [defaultTitle, setDefaultTitle] = useState<string | null>(null);
  const [draft, setDraft] = useState<ShareMetaDraft>(() => readShareMeta(run.pageId));

  useEffect(() => {
    let cancelled = false;
    void fetchPageTitle(toAbsoluteUrl(run.pageUrl)).then((title) => {
      if (!cancelled) setDefaultTitle(title || "我用星页做了一个页面");
    });
    return () => {
      cancelled = true;
    };
  }, [run.pageUrl]);

  function update(patch: ShareMetaDraft) {
    setDraft((current) => {
      const next = { ...current, ...patch };
      try {
        localStorage.setItem(SHARE_META_PREFIX + run.pageId, JSON.stringify(next));
      } catch {
        // 本机存储不可用时只在本次生效
      }
      return next;
    });
  }

  function reset() {
    setDraft({});
    try {
      localStorage.removeItem(SHARE_META_PREFIX + run.pageId);
    } catch {
      // 忽略
    }
  }

  return {
    ready: defaultTitle !== null,
    title: draft.title ?? defaultTitle ?? "",
    subtitle: draft.subtitle ?? DEFAULT_SUBTITLE,
    align: draft.align ?? "center",
    customized: draft.title !== undefined || draft.subtitle !== undefined || draft.align !== undefined,
    update,
    reset,
  };
}

/** 标题 / 副标题一改就重绘分享图（稍作防抖，连续输入时不每个字都重画）。 */
function usePosterImage(qrUrl: string | undefined, title: string, subtitle: string, align: PosterAlign, enabled: boolean) {
  const [qr, setQr] = useState<HTMLImageElement | null>(null);
  const [src, setSrc] = useState("");

  useEffect(() => {
    if (!qrUrl) return;
    let cancelled = false;
    void loadImage(qrUrl)
      .then((image) => {
        if (!cancelled) setQr(image);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [qrUrl]);

  useEffect(() => {
    if (!qr || !enabled) return;
    const timer = window.setTimeout(() => setSrc(renderPoster({ title, subtitle, qr, align })), 90);
    return () => window.clearTimeout(timer);
  }, [qr, title, subtitle, align, enabled]);

  return src;
}

const POSTER_WIDTH = 216;

function SharePopover({
  run,
  anchor,
  state,
  printable,
  onPrint,
  onRetry,
  onClose,
  onNotice,
}: {
  run: WorkspaceRun;
  anchor: HTMLButtonElement;
  state: ShareState;
  printable: boolean;
  onPrint: (url: string) => void;
  onRetry: () => void;
  onClose: () => void;
  onNotice: (message: string) => void;
}) {
  const [popover, setPopover] = useState<HTMLDivElement | null>(null);
  const [copied, setCopied] = useState(false);
  const closeRef = useRef(onClose);
  const position = usePopoverPosition(anchor, popover);
  const data = state.kind === "ready" ? state.data : null;
  const meta = useShareMeta(run);
  const posterSrc = usePosterImage(data?.qrUrl, meta.title, meta.subtitle, meta.align, meta.ready);
  const titleId = useId();

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!popover) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && (popover.contains(target) || anchor.contains(target))) return;
      closeRef.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    // 点进页面预览（iframe）时外层收不到点击，只能从窗口失焦判断。
    const onBlur = () => {
      window.setTimeout(() => {
        if (document.activeElement?.tagName === "IFRAME") closeRef.current();
      }, 0);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", onBlur);
    };
  }, [anchor, popover]);

  async function copy() {
    if (!data) return;
    try {
      await copyTextToClipboard(data.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      onNotice("复制失败，请稍后再试");
    }
  }

  const placement = position?.placement ?? "bottom";
  const arrowOnPoster = (position?.arrow ?? POSTER_WIDTH + 1) < POSTER_WIDTH;
  const style = {
    top: position?.top ?? 0,
    left: position?.left ?? 0,
    visibility: position ? "visible" : "hidden",
    "--arrow-x": `${position?.arrow ?? 0}px`,
    "--accent": modelAccent(run.modelKey),
  } as CSSProperties;

  return createPortal(
    <div
      ref={setPopover}
      className={`share-pop is-${placement} ${data ? "is-ready" : ""} ${arrowOnPoster ? "is-arrow-dark" : ""}`}
      role="dialog"
      aria-label={`分享 ${run.modelLabel} 的版本`}
      style={style}
    >
      <span className="share-pop-arrow" aria-hidden="true" />
      {state.kind === "error" ? (
        <div className="share-pop-error" role="alert">
          <strong>分享链接没有生成成功</strong>
          <span>网络可能抖了一下，再试一次就好。</span>
          <button type="button" onClick={onRetry}>
            重新生成
          </button>
        </div>
      ) : (
        <div className="share-pop-grid">
          <figure className={`share-pop-poster ${posterSrc ? "is-loaded" : ""}`}>
            {posterSrc && <img src={posterSrc} alt={`${meta.title} 的分享图`} />}
            {!posterSrc && <span className="share-pop-shimmer" aria-hidden="true" />}
          </figure>
          <div className="share-pop-side">
            <header className="share-pop-head">
              <strong>分享这个版本</strong>
              <span className="share-pop-model">
                <i aria-hidden="true" />
                {run.modelLabel}
              </span>
            </header>

            <div className="share-pop-field">
              <span className="share-pop-label">
                <label htmlFor={titleId}>标题</label>
                <span className="share-pop-label-tools">
                  {meta.customized && (
                    <button type="button" className="share-pop-reset" onClick={meta.reset}>
                      恢复默认
                    </button>
                  )}
                  <span className="share-pop-align" role="radiogroup" aria-label="分享图文字对齐">
                    {(["center", "left"] as const).map((value) => (
                      <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={meta.align === value}
                        className={meta.align === value ? "is-on" : ""}
                        onClick={() => meta.update({ align: value })}
                        data-tip={value === "center" ? "居中" : "左对齐"}
                        aria-label={value === "center" ? "居中" : "左对齐"}
                      >
                        {value === "center" ? <AlignCenterIcon /> : <AlignLeftIcon />}
                      </button>
                    ))}
                  </span>
                </span>
              </span>
              <span className="share-pop-input">
                <input
                  id={titleId}
                  value={meta.title}
                  maxLength={TITLE_MAX}
                  placeholder={meta.ready ? "给分享图起个标题" : "正在读取页面标题…"}
                  disabled={!meta.ready}
                  onChange={(event) => meta.update({ title: event.target.value })}
                />
                <em>{meta.title.length}/{TITLE_MAX}</em>
              </span>
            </div>
            <label className="share-pop-field">
              <span className="share-pop-label">副标题</span>
              <span className="share-pop-input">
                <input
                  value={meta.subtitle}
                  maxLength={SUBTITLE_MAX}
                  placeholder="一句话介绍这个页面"
                  disabled={!meta.ready}
                  onChange={(event) => meta.update({ subtitle: event.target.value })}
                />
                <em>{meta.subtitle.length}/{SUBTITLE_MAX}</em>
              </span>
            </label>

            <div className="share-pop-actions">
              <button
                type="button"
                className={`share-pop-copy ${copied ? "is-copied" : ""}`}
                onClick={() => void copy()}
                disabled={!data}
                title={data?.url}
              >
                {copied ? <CheckIcon /> : <LinkIcon />}
                {copied ? "已复制链接" : data ? "复制链接" : "生成链接中…"}
              </button>
              <button type="button" className="share-pop-save" onClick={() => downloadDataUrl(posterSrc, "星页分享图.png")} disabled={!posterSrc}>
                <DownloadIcon />
                保存图片
              </button>
              <a
                className={`share-pop-open ${data ? "" : "is-disabled"}`}
                href={data?.openUrl}
                target="_blank"
                rel="noreferrer"
                aria-label="打开链接"
                data-tip="打开链接"
                onClick={(event) => {
                  if (!data) event.preventDefault();
                }}
              >
                <OpenIcon />
              </a>
            </div>

            <p className={`share-pop-note ${data?.republished ? "is-updated" : ""}`}>
              {data?.republished && <span>链接没变，内容已换成「{run.modelLabel}」这一版</span>}
              {data && data.externalViews > 0 && (
                <span className="share-pop-views">
                  <EyeIcon />
                  别人打开过 {data.externalViews} 次
                </span>
              )}
              {printable && data && (
                <button type="button" className="share-pop-print" onClick={() => onPrint(data.openUrl)}>
                  <PrintIcon />
                  存为 PDF
                </button>
              )}
            </p>
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}

const svgProps = {
  width: "1em",
  height: "1em",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const OpenIcon = () => (
  <svg {...svgProps}>
    <path d="M14 3h7v7" />
    <path d="M10 14 21 3" />
    <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
  </svg>
);

const CheckIcon = () => (
  <svg {...svgProps} strokeWidth={2.4}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);

export const ShareIcon = () => (
  <svg {...svgProps}>
    <path d="M12 15V3" />
    <path d="m7.5 7.5 4.5-4.5 4.5 4.5" />
    <path d="M8 11H6a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-2" />
  </svg>
);

export const ExpandIcon = () => (
  <svg {...svgProps}>
    <path d="M15 3h6v6" />
    <path d="M9 21H3v-6" />
    <path d="M21 3l-7 7" />
    <path d="M3 21l7-7" />
  </svg>
);

export const BranchIcon = () => (
  <svg {...svgProps}>
    <circle cx="6" cy="6" r="2.4" />
    <circle cx="6" cy="18" r="2.4" />
    <circle cx="18" cy="9" r="2.4" />
    <path d="M6 8.4v7.2" />
    <path d="M18 11.4c0 3-3 3.6-6 3.6" />
    <path d="M15.8 7l2.2 2 2.2-2" transform="translate(-2.2 -1)" />
  </svg>
);

const PrintIcon = () => (
  <svg {...svgProps}>
    <path d="M6 9V3h12v6" />
    <rect x="3" y="9" width="18" height="8" rx="2" />
    <path d="M7 14h10v7H7z" />
  </svg>
);

const LockIcon = () => (
  <svg {...svgProps} className="tb-address-lock">
    <rect x="5" y="11" width="14" height="10" rx="2.5" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);

const LinkIcon = () => (
  <svg {...svgProps} className="share-pop-link-icon">
    <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
    <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
  </svg>
);

const DownloadIcon = () => (
  <svg {...svgProps}>
    <path d="M12 3v12" />
    <path d="m7 10 5 5 5-5" />
    <path d="M5 21h14" />
  </svg>
);

const AlignCenterIcon = () => (
  <svg {...svgProps}>
    <path d="M4 6h16M7 12h10M5 18h14" />
  </svg>
);

const AlignLeftIcon = () => (
  <svg {...svgProps}>
    <path d="M4 6h16M4 12h10M4 18h14" />
  </svg>
);

const EyeIcon = () => (
  <svg {...svgProps}>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);
