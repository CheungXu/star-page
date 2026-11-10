"""固定链接的二维码、分享卡片和朋友圈海报。

二维码用 segno 生成，不依赖系统字体。分享卡片和海报优先用 Pillow 绘制；
没有 Pillow 或没有中文字体时，退回纯二维码图片，保证链接仍然可被微信抓取。

朋友圈海报的版式与前端 share-poster.ts 保持一致（前端画布 720×1080，这里 1080×1620，按 1.5 倍），
用户在分享浮层里看到的预览和公开页「发朋友圈」拿到的海报是同一张图。
"""

from __future__ import annotations

import io
from pathlib import Path

_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_BOLD_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
)
# Noto CJK 字体集合里第 3 个（索引 2）是简体中文字形；索引 0 是日文字形，部分汉字写法不同。
_NOTO_SC_INDEX = 2

CARD_ALIGNS = ("left", "center", "right")


def render_qr_png(url: str, scale: int = 8) -> bytes:
    import segno

    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="png", scale=scale, border=2)
    return buffer.getvalue()


def render_card_png(*, title: str, subtitle: str, qr_png: bytes, wide: bool, align: str = "center") -> bytes:
    """绘制横版分享卡（wide）或竖版朋友圈海报。失败时退回二维码本身。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return qr_png

    font_path = _find_font()
    if font_path is None:
        return _pad_png(qr_png, 320)

    if not wide:
        return _render_poster(title=title, subtitle=subtitle, qr_png=qr_png, font_path=font_path, align=align)

    width, height = 1200, 630
    image = Image.new("RGB", (width, height), "#f4f1ea")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(font_path, 64)
    sub_font = _load_font(font_path, 32)
    brand_font = _load_font(font_path, 28)

    draw.rectangle((0, 0, width, 16), fill="#3563e9")
    draw.text((72, 88), _clip(title, 18), fill="#1c1917", font=title_font)
    draw.text((72, 190), _clip(subtitle, 28), fill="#57534e", font=sub_font)
    draw.text((72, height - 88), "星页 StarPage", fill="#3563e9", font=brand_font)

    qr = Image.open(io.BytesIO(qr_png)).convert("RGB")
    qr_size = 280
    qr = qr.resize((qr_size, qr_size), Image.NEAREST)
    image.paste(qr, (width - qr_size - 72, (height - qr_size) // 2))

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _render_poster(*, title: str, subtitle: str, qr_png: bytes, font_path: str, align: str) -> bytes:
    """朋友圈竖版海报：渐变底、可左 / 中 / 右对齐的标题与副标题、白卡二维码（放在文字与落款正中）、底部品牌。"""
    from PIL import Image, ImageDraw, ImageFilter

    align = align if align in CARD_ALIGNS else "center"
    width, height = 1080, 1620
    margin = 96
    image = Image.new("RGB", (width, height), "#111827")
    draw = ImageDraw.Draw(image)

    top, bottom = (17, 24, 39), (49, 46, 129)
    for y in range(height):
        ratio = y / (height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((560, -220, 1320, 540), fill=(99, 102, 241, 110))
    glow_draw.ellipse((-260, 980, 420, 1660), fill=(56, 189, 248, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)

    bold_path = _find_bold_font() or font_path
    tag_font = _load_font(font_path, 32)
    title_font = _load_font(bold_path, 90)
    sub_font = _load_font(font_path, 38)
    small_font = _load_font(font_path, 30)
    brand_font = _load_font(font_path, 33)

    anchor = {"left": "la", "center": "ma", "right": "ra"}[align]
    text_x = {"left": margin, "center": width / 2, "right": width - margin}[align]

    tag = "邀你看看这个页面"
    tag_width = draw.textlength(tag, font=tag_font) + 45
    tag_x = {"left": margin, "center": (width - tag_width) / 2, "right": width - margin - tag_width}[align]
    _star(draw, tag_x + 14, 168, 15, (165, 180, 252))
    draw.text((tag_x + 45, 150), tag, fill=(165, 180, 252), font=tag_font, anchor="la")

    y = 228
    for line in _wrap(draw, title or "我用星页做了一个页面", title_font, width - margin * 2, max_lines=3):
        draw.text((text_x, y), line, fill="white", font=title_font, anchor=anchor)
        y += 120
    y += 15
    for line in _wrap(draw, subtitle or "", sub_font, width - margin * 2, max_lines=2):
        draw.text((text_x, y), line, fill=(203, 213, 225), font=sub_font, anchor=anchor)
        y += 57

    card = 519
    card_height = card + 90
    brand_top = height - 117
    card_x = (width - card) // 2
    card_y = round(y + max(66, (brand_top - 42 - y - card_height) / 2))
    draw.rounded_rectangle((card_x, card_y, card_x + card, card_y + card_height), radius=36, fill="white")
    qr_size = card - 78
    qr = Image.open(io.BytesIO(qr_png)).convert("RGB").resize((qr_size, qr_size), Image.NEAREST)
    image.paste(qr, (card_x + 39, card_y + 39))
    draw.text((width / 2, card_y + card + 18), "长按或扫码打开", fill=(71, 85, 105), font=small_font, anchor="ma")

    draw.text((width / 2, brand_top), "星页 StarPage · 一份资料，做成能分享的网页", fill=(199, 210, 254), font=brand_font, anchor="ma")

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _star(draw, cx: float, cy: float, r: float, fill) -> None:
    k = r * 0.28
    draw.polygon(
        [(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k), (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)],
        fill=fill,
    )


def _wrap(draw, text: str, font, max_width: int, *, max_lines: int) -> list[str]:
    """按像素宽度逐字折行（中文没有空格）。超出行数时最后一行加省略号。"""
    clean = " ".join((text or "").split())
    lines: list[str] = []
    current = ""
    for char in clean:
        if draw.textlength(current + char, font=font) <= max_width:
            current += char
            continue
        lines.append(current)
        current = char
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and "".join(lines) != clean:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _pad_png(raw: bytes, size: int) -> bytes:
    """把小图铺到不小于微信分享图要求的画布上。"""
    try:
        from PIL import Image
    except ImportError:
        return raw
    source = Image.open(io.BytesIO(raw)).convert("RGB")
    canvas = Image.new("RGB", (size, size), "#f4f1ea")
    fitted = source.resize((size - 48, size - 48))
    canvas.paste(fitted, (24, 24))
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


def _load_font(path: str, size: int):
    from PIL import ImageFont

    if path.endswith(".ttc") and "NotoSansCJK" in path:
        try:
            return ImageFont.truetype(path, size, index=_NOTO_SC_INDEX)
        except OSError:
            pass
    return ImageFont.truetype(path, size)


def _find_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _find_bold_font() -> str | None:
    for path in _BOLD_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _clip(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean or "星页"
    return clean[: limit - 1] + "…"
