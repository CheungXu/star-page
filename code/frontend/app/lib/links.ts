/** 页面链接与剪贴板：预览、复制、分享共用。 */

export function toAbsoluteUrl(pageUrl: string): string {
  if (!pageUrl) return "";
  if (typeof window === "undefined") return pageUrl;

  try {
    const url = new URL(pageUrl, window.location.origin);
    if (url.pathname.startsWith("/p/") || url.pathname.startsWith("/u/")) {
      return `${window.location.origin}${url.pathname}${url.search}${url.hash}`;
    }
    return url.toString();
  } catch {
    if (pageUrl.startsWith("/")) return `${window.location.origin}${pageUrl}`;
    return pageUrl;
  }
}

export async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // 剪贴板权限被拒（如部分内嵌浏览器）时退回 execCommand。
    }
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
