"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { apiFetch } from "../../lib/api";
import { copyTextToClipboard, toAbsoluteUrl } from "../../lib/links";
import { isDemoRun, publishDemo, type SharePublication } from "./demo";
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

/** 单个结果的操作：同一套行为，按 layout 摆成底栏、顶栏、控制轨或焦点卡。 */
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
  const [copied, setCopied] = useState(false);
  const [share, setShare] = useState<ShareState | null>(null);
  const [publishedUrl, setPublishedUrl] = useState("");
  const copyTimerRef = useRef<number | null>(null);
  const shareSeqRef = useRef(0);

  useEffect(
    () => () => {
      if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
    },
    [],
  );

  async function copyUrl() {
    if (!ready) return;
    if (!demo && shareGate()) return;
    try {
      await copyTextToClipboard(absoluteUrl);
      setCopied(true);
      if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => setCopied(false), 1800);
    } catch {
      onNotice("复制失败，请手动复制地址");
    }
  }

  async function openShare() {
    if (!ready) return;
    if (!demo && shareGate()) return;
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
      setPublishedUrl(data.url);
      setShare({ kind: "ready", data });
    } catch {
      if (seq === shareSeqRef.current) setShare({ kind: "error" });
    }
  }

  function closeShare() {
    shareSeqRef.current += 1;
    setShare(null);
  }

  function exportPdf() {
    const target = publishedUrl || absoluteUrl;
    if (!target) return;
    const join = target.includes("?") ? "&" : "?";
    const opened = window.open(`${target}${join}print=1`, "_blank");
    if (!opened) {
      onNotice("浏览器拦住了打印窗口，请允许弹窗后再试");
      return;
    }
    opened.opener = null;
  }

  const continueText = selectedAsBase ? "已选为基础" : "以此结果继续";
  let body: ReactNode;

  if (layout === "chrome") {
    const address = splitAddress(absoluteUrl);
    body = (
      <div className="tb-chrome-actions">
        <div className={`tb-address ${copied ? "is-copied" : ""} ${ready ? "" : "is-disabled"}`}>
          <GlobeIcon />
          <button type="button" className="tb-address-text" onClick={() => void copyUrl()} disabled={!ready} title={ready ? "点击复制链接" : undefined}>
            {ready ? (
              <>
                <span className="tb-address-host">{address.host}</span>
                <span className="tb-address-path">{address.path}</span>
              </>
            ) : (
              <span className="tb-address-host">{run.status === "failed" ? "这个版本没有生成成功" : "页面生成中…"}</span>
            )}
          </button>
          <button type="button" className="tb-address-btn is-copy" onClick={() => void copyUrl()} disabled={!ready} aria-label="复制链接">
            {copied ? <CheckIcon /> : <CopyIcon />}
            <span>{copied ? "已复制" : "复制"}</span>
          </button>
          {ready && (
            <a className="tb-address-btn" href={absoluteUrl} target="_blank" rel="noreferrer" aria-label="在新标签页打开" title="在新标签页打开">
              <OpenIcon />
            </a>
          )}
        </div>
        <button type="button" className="tb-btn is-share" onClick={() => void openShare()} disabled={!ready}>
          <ShareIcon />
          <span>分享</span>
        </button>
        {printable && ready && (
          <button type="button" className="tb-btn is-icon" onClick={exportPdf} title="打印或存为 PDF" aria-label="打印或存为 PDF">
            <PrintIcon />
          </button>
        )}
        {canContinue && ready && (
          <button type="button" className="tb-btn is-primary" onClick={onContinue} aria-pressed={selectedAsBase} title={continueText}>
            <BranchIcon />
            <span>{continueText}</span>
          </button>
        )}
        <button type="button" className="tb-btn is-icon" onClick={onOpenPreview} disabled={!ready} title="放大预览" aria-label="放大预览">
          <ExpandIcon />
        </button>
      </div>
    );
  } else if (layout === "rail") {
    body = (
      <div className="tb-rail-actions">
        {ready ? (
          <a className="tb-tile" href={absoluteUrl} target="_blank" rel="noreferrer" title="在新标签页打开">
            <OpenIcon />
            <span>打开</span>
          </a>
        ) : (
          <button type="button" className="tb-tile" disabled>
            <OpenIcon />
            <span>打开</span>
          </button>
        )}
        <button type="button" className={`tb-tile ${copied ? "is-copied" : ""}`} onClick={() => void copyUrl()} disabled={!ready} title="复制链接">
          {copied ? <CheckIcon /> : <CopyIcon />}
          <span>{copied ? "已复制" : "复制"}</span>
        </button>
        <button type="button" className="tb-tile is-share" onClick={() => void openShare()} disabled={!ready} title="分享">
          <ShareIcon />
          <span>分享</span>
        </button>
        <button type="button" className="tb-tile" onClick={onOpenPreview} disabled={!ready} title="放大预览">
          <ExpandIcon />
          <span>放大</span>
        </button>
        {printable && ready && (
          <button type="button" className="tb-tile" onClick={exportPdf} title="打印或存为 PDF">
            <PrintIcon />
            <span>PDF</span>
          </button>
        )}
        {canContinue && ready && (
          <>
            <i className="tb-rail-sep" aria-hidden="true" />
            <button type="button" className="tb-tile is-primary" onClick={onContinue} aria-pressed={selectedAsBase} title={continueText}>
              <BranchIcon />
              <span>{selectedAsBase ? "已选" : "继续改"}</span>
            </button>
          </>
        )}
      </div>
    );
  } else if (layout === "card") {
    body = (
      <div className="tb-card-actions">
        <a className="tb-ico" href={absoluteUrl} target="_blank" rel="noreferrer" title="在新标签页打开" aria-label="在新标签页打开">
          <OpenIcon />
        </a>
        <button type="button" className={`tb-ico ${copied ? "is-copied" : ""}`} onClick={() => void copyUrl()} title={copied ? "已复制" : "复制链接"} aria-label="复制链接">
          {copied ? <CheckIcon /> : <CopyIcon />}
        </button>
        <button type="button" className="tb-ico" onClick={onOpenPreview} title="放大预览" aria-label="放大预览">
          <ExpandIcon />
        </button>
        {printable && (
          <button type="button" className="tb-ico" onClick={exportPdf} title="打印或存为 PDF" aria-label="打印或存为 PDF">
            <PrintIcon />
          </button>
        )}
        <button type="button" className="tb-chip is-share" onClick={() => void openShare()}>
          <ShareIcon />
          分享
        </button>
        {canContinue && (
          <button type="button" className="tb-chip is-primary" onClick={onContinue} aria-pressed={selectedAsBase} title={continueText}>
            <BranchIcon />
            {selectedAsBase ? "已选" : "继续改"}
          </button>
        )}
      </div>
    );
  } else {
    body = (
      <div className="link-actions">
        <div className="link-secondary-actions">
          <a className="link-icon-button" href={absoluteUrl} target="_blank" rel="noreferrer" aria-label={`打开 ${run.modelLabel} 生成页面`} title="打开页面">
            <OpenIcon />
            <span>打开</span>
          </a>
          <button className={`link-icon-button ${copied ? "copied" : ""}`} type="button" onClick={() => void copyUrl()} aria-label={`复制 ${run.modelLabel} 页面链接`} title="复制链接">
            <CopyIcon />
            <span>{copied ? "已复制" : "复制"}</span>
          </button>
          <button className="link-icon-button is-share" type="button" onClick={() => void openShare()}>
            <ShareIcon />
            <span>分享</span>
          </button>
          {printable && (
            <button className="link-icon-button" type="button" onClick={exportPdf}>
              <span>打印或存为 PDF</span>
            </button>
          )}
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
      {share && <ShareDialog run={run} state={share} onRetry={() => void openShare()} onClose={closeShare} onNotice={onNotice} />}
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
    poster_url: string;
    republished?: boolean;
    external_view_count?: number;
  };
  return {
    url: data.url,
    openUrl: data.url,
    posterUrl: `${toAbsoluteUrl(data.poster_url)}?v=${Date.now()}`,
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

function ShareDialog({
  run,
  state,
  onRetry,
  onClose,
  onNotice,
}: {
  run: WorkspaceRun;
  state: ShareState;
  onRetry: () => void;
  onClose: () => void;
  onNotice: (message: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [posterReady, setPosterReady] = useState(false);
  const closeRef = useRef(onClose);
  const copyRef = useRef<HTMLButtonElement | null>(null);
  const data = state.kind === "ready" ? state.data : null;
  const link = data ? splitShareLink(data.url) : null;

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  useEffect(() => {
    if (data) copyRef.current?.focus({ preventScroll: true });
  }, [data]);

  async function copy() {
    if (!data) return;
    try {
      await copyTextToClipboard(data.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      onNotice("复制失败，请选中链接手动复制");
    }
  }

  const showPoster = Boolean(data && posterReady);

  return createPortal(
    <div className="share-backdrop" role="presentation" onClick={onClose}>
      <section
        className={`share-dialog ${data ? "is-ready" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-title"
        onClick={(event) => event.stopPropagation()}
        style={{ "--accent": modelAccent(run.modelKey) } as CSSProperties}
      >
        <div className="share-visual">
          {data && <span className="share-ambient" style={{ backgroundImage: `url("${data.posterUrl}")` }} aria-hidden="true" />}
          <figure className={`share-poster ${showPoster ? "is-loaded" : ""}`}>
            {data && <img src={data.posterUrl} alt={`${run.modelLabel} 版本的分享图`} onLoad={() => setPosterReady(true)} />}
            {!showPoster && (
              <span className="share-poster-skeleton" aria-hidden="true">
                <i />
                <i />
                <i />
                <b />
              </span>
            )}
          </figure>
          <p className="share-visual-tip">
            <PhoneIcon />
            {showPoster ? "手机上长按图片，可直接保存" : "正在生成分享图…"}
          </p>
        </div>

        <div className="share-body">
          <button type="button" className="share-close" onClick={onClose} aria-label="关闭">
            <CloseIcon />
          </button>
          <header className="share-head">
            <span className="share-eyebrow">
              <ShareIcon />
              分享
            </span>
            <h2 id="share-title">把这个页面发给朋友</h2>
            <p className="share-source">
              <i aria-hidden="true" />
              {run.modelLabel} 的版本
            </p>
          </header>

          {state.kind === "error" ? (
            <div className="share-error" role="alert">
              <strong>分享链接没有生成成功</strong>
              <span>网络可能抖了一下，再试一次就好。</span>
              <button type="button" onClick={onRetry}>
                重新生成
              </button>
            </div>
          ) : (
            <>
              <div className="share-field">
                <span className="share-label">分享链接</span>
                <div className={`share-link ${copied ? "is-copied" : ""} ${data ? "" : "is-loading"}`}>
                  <span className="share-link-icon" aria-hidden="true">
                    <LinkIcon />
                  </span>
                  {link && data ? (
                    <span className="share-link-text" title={data.url}>
                      <span>{link.prefix}</span>
                      <b>{link.slug}</b>
                    </span>
                  ) : (
                    <span className="share-link-text is-placeholder">正在生成固定链接…</span>
                  )}
                  <button ref={copyRef} type="button" className="share-copy" onClick={() => void copy()} disabled={!data}>
                    {copied ? (
                      <>
                        <CheckIcon />
                        已复制
                      </>
                    ) : (
                      "复制"
                    )}
                  </button>
                </div>
                <p className={`share-note ${data?.republished ? "is-updated" : ""}`}>
                  {data?.republished ? (
                    <>
                      <RefreshIcon />
                      链接没变，内容已换成「{run.modelLabel}」这一版
                    </>
                  ) : (
                    <>
                      <InfoIcon />
                      同一个会话共用这条链接，分享哪个版本，朋友打开的就是哪个版本
                    </>
                  )}
                </p>
              </div>

              <div className="share-tiles">
                <a
                  className={`share-tile ${data ? "" : "is-disabled"}`}
                  href={data?.posterUrl}
                  download={data ? "星页分享图.png" : undefined}
                  aria-disabled={!data}
                  onClick={(event) => {
                    if (!data) event.preventDefault();
                  }}
                >
                  <span className="share-tile-icon">
                    <DownloadIcon />
                  </span>
                  <span className="share-tile-text">
                    <strong>保存分享图</strong>
                    <small>发朋友圈、群聊都合适</small>
                  </span>
                </a>
                <a
                  className={`share-tile ${data ? "" : "is-disabled"}`}
                  href={data?.openUrl}
                  target="_blank"
                  rel="noreferrer"
                  aria-disabled={!data}
                  onClick={(event) => {
                    if (!data) event.preventDefault();
                  }}
                >
                  <span className="share-tile-icon">
                    <OpenIcon />
                  </span>
                  <span className="share-tile-text">
                    <strong>打开链接</strong>
                    <small>看看朋友看到的样子</small>
                  </span>
                </a>
              </div>
            </>
          )}

          <footer className="share-foot">
            {data && data.externalViews > 0 ? (
              <span className="share-views">
                <EyeIcon />
                别人打开过 {data.externalViews} 次
              </span>
            ) : (
              <span />
            )}
            <span>图里的二维码直达这条链接</span>
          </footer>
        </div>
      </section>
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

export const CopyIcon = () => (
  <svg {...svgProps}>
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
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

const GlobeIcon = () => (
  <svg {...svgProps} className="tb-address-globe">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18" />
    <path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" />
  </svg>
);

const LinkIcon = () => (
  <svg {...svgProps}>
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

const CloseIcon = () => (
  <svg {...svgProps}>
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);

const PhoneIcon = () => (
  <svg {...svgProps}>
    <rect x="6" y="2.5" width="12" height="19" rx="2.5" />
    <path d="M11 18.5h2" />
  </svg>
);

const EyeIcon = () => (
  <svg {...svgProps}>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const InfoIcon = () => (
  <svg {...svgProps}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 8h.01" />
  </svg>
);

const RefreshIcon = () => (
  <svg {...svgProps}>
    <path d="M20 11a8 8 0 0 0-14.6-4.5L4 8" />
    <path d="M4 4v4h4" />
    <path d="M4 13a8 8 0 0 0 14.6 4.5L20 16" />
    <path d="M20 20v-4h-4" />
  </svg>
);
