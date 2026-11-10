/**
 * 浏览器端绘制分享图：版式与后端 share_card._render_poster 一致（深色渐变、大标题、白底二维码卡、品牌落款），
 * 标题和副标题由用户在分享浮层里改写，改完即时重绘；保存图片直接导出这张画布。
 */

const FONT = `"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", "Hiragino Sans GB", sans-serif`;
const WIDTH = 720;
const HEIGHT = 1080;
const MARGIN = 64;

export const DEFAULT_SUBTITLE = "由星页 StarPage 生成";

export function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = src;
  });
}

/** 按像素宽度逐字折行（中文没有空格）；超出行数时最后一行加省略号。 */
function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number): string[] {
  const clean = text.replace(/\s+/g, " ").trim();
  const lines: string[] = [];
  let current = "";
  for (const char of clean) {
    if (ctx.measureText(current + char).width <= maxWidth) {
      current += char;
      continue;
    }
    lines.push(current);
    current = char;
    if (lines.length === maxLines) break;
  }
  if (current && lines.length < maxLines) lines.push(current);
  if (lines.length === maxLines && lines.join("") !== clean) {
    let last = lines[maxLines - 1];
    while (last && ctx.measureText(`${last}…`).width > maxWidth) last = last.slice(0, -1);
    lines[maxLines - 1] = `${last}…`;
  }
  return lines;
}

function glow(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number, color: string) {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
  gradient.addColorStop(0, color);
  gradient.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
}

function star(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number) {
  const k = r * 0.28;
  ctx.beginPath();
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx + k, cy - k);
  ctx.lineTo(cx + r, cy);
  ctx.lineTo(cx + k, cy + k);
  ctx.lineTo(cx, cy + r);
  ctx.lineTo(cx - k, cy + k);
  ctx.lineTo(cx - r, cy);
  ctx.lineTo(cx - k, cy - k);
  ctx.closePath();
  ctx.fill();
}

export function renderPoster({ title, subtitle, qr }: { title: string; subtitle: string; qr: HTMLImageElement }): string {
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";

  const background = ctx.createLinearGradient(0, 0, 0, HEIGHT);
  background.addColorStop(0, "#111827");
  background.addColorStop(1, "#312e81");
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  glow(ctx, WIDTH * 0.86, HEIGHT * 0.06, 460, "rgba(99, 102, 241, 0.42)");
  glow(ctx, WIDTH * 0.04, HEIGHT * 0.92, 400, "rgba(56, 189, 248, 0.26)");

  ctx.textBaseline = "top";
  ctx.fillStyle = "#a5b4fc";
  star(ctx, MARGIN + 9, 112, 10);
  ctx.font = `500 21px ${FONT}`;
  ctx.fillText("邀你看看这个页面", MARGIN + 30, 100);

  let y = 152;
  ctx.fillStyle = "#ffffff";
  ctx.font = `600 60px ${FONT}`;
  for (const line of wrap(ctx, title || "我用星页做了一个页面", WIDTH - MARGIN * 2, 3)) {
    ctx.fillText(line, MARGIN, y);
    y += 80;
  }
  y += 10;
  ctx.fillStyle = "#cbd5e1";
  ctx.font = `400 25px ${FONT}`;
  for (const line of wrap(ctx, subtitle, WIDTH - MARGIN * 2, 2)) {
    ctx.fillText(line, MARGIN, y);
    y += 38;
  }

  const card = 346;
  const cardX = (WIDTH - card) / 2;
  const cardY = Math.max(y + 56, 548);
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.roundRect(cardX, cardY, card, card + 60, 24);
  ctx.fill();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(qr, cardX + 26, cardY + 26, card - 52, card - 52);
  ctx.imageSmoothingEnabled = true;
  ctx.fillStyle = "#475569";
  ctx.font = `400 20px ${FONT}`;
  ctx.textAlign = "center";
  ctx.fillText("长按或扫码打开", WIDTH / 2, cardY + card + 12);

  ctx.fillStyle = "#c7d2fe";
  ctx.font = `400 22px ${FONT}`;
  ctx.fillText("星页 StarPage · 一份资料，做成能分享的网页", WIDTH / 2, HEIGHT - 78);

  return canvas.toDataURL("image/png");
}

/** 读生成页自己的 <title> 作为默认标题（同源请求，不跨域）。 */
export async function fetchPageTitle(url: string): Promise<string> {
  if (!url) return "";
  try {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) return "";
    const html = await response.text();
    return new DOMParser().parseFromString(html, "text/html").title.replace(/\s+/g, " ").trim();
  } catch {
    return "";
  }
}

export function downloadDataUrl(dataUrl: string, filename: string) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
