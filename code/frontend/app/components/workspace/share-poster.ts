/**
 * 浏览器端绘制分享图：版式与后端 share_card._render_poster 一致（深色渐变、大标题、白底二维码卡、品牌落款），
 * 用户在分享浮层里改写标题 / 副标题 / 对齐时即时重绘；保存图片直接导出这张画布。
 * 改动同时保存到服务器，后端按同一版式重画公开页「发朋友圈」的海报，两处是同一张图。
 */

const FONT = `"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", "Hiragino Sans GB", sans-serif`;
const WIDTH = 720;
const HEIGHT = 1080;
const MARGIN = 64;

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

export type PosterAlign = "left" | "center" | "right";

export function renderPoster({
  title,
  subtitle,
  qr,
  align = "center",
}: {
  title: string;
  subtitle: string;
  qr: HTMLImageElement;
  align?: PosterAlign;
}): string {
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
  const textX = align === "center" ? WIDTH / 2 : align === "right" ? WIDTH - MARGIN : MARGIN;
  const tag = "邀你看看这个页面";
  ctx.fillStyle = "#a5b4fc";
  ctx.font = `500 21px ${FONT}`;
  const tagWidth = ctx.measureText(tag).width + 30;
  const tagStart = align === "center" ? (WIDTH - tagWidth) / 2 : align === "right" ? WIDTH - MARGIN - tagWidth : MARGIN;
  star(ctx, tagStart + 9, 112, 10);
  ctx.fillText(tag, tagStart + 30, 100);

  ctx.textAlign = align;
  let y = 152;
  ctx.fillStyle = "#ffffff";
  ctx.font = `600 60px ${FONT}`;
  for (const line of wrap(ctx, title || "我用星页做了一个页面", WIDTH - MARGIN * 2, 3)) {
    ctx.fillText(line, textX, y);
    y += 80;
  }
  y += 10;
  ctx.fillStyle = "#cbd5e1";
  ctx.font = `400 25px ${FONT}`;
  for (const line of wrap(ctx, subtitle, WIDTH - MARGIN * 2, 2)) {
    ctx.fillText(line, textX, y);
    y += 38;
  }

  // 二维码卡放在文字下沿与底部落款之间的正中，标题一到三行都不会上空下挤。
  const card = 346;
  const cardHeight = card + 60;
  const brandTop = HEIGHT - 78;
  const cardX = (WIDTH - card) / 2;
  const cardY = Math.round(y + Math.max(44, (brandTop - 28 - y - cardHeight) / 2));
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.roundRect(cardX, cardY, card, cardHeight, 24);
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
  ctx.fillText("星页 StarPage · 一份资料，做成能分享的网页", WIDTH / 2, brandTop);

  return canvas.toDataURL("image/png");
}

export function downloadDataUrl(dataUrl: string, filename: string) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
