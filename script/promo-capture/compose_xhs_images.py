"""把真实界面截图拼成小红书 3:4 配图（1080x1440），只用真实截图，不往图上加字。

依赖 shoot_site.py / shoot_generation.py 的输出。

用法：
    cd code/backend && . .venv/bin/activate
    python ../../script/promo-capture/compose_xhs_images.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "data/文案/202610/00-界面截图"
OUT = SHOTS / "小红书配图"
W, H = 1080, 1440


def background() -> Image.Image:
    """浅色渐变底，和星页首页的蓝紫氛围一致。"""
    top, bottom = (238, 242, 255), (247, 244, 255)
    bg = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return bg


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def paste_card(canvas: Image.Image, img: Image.Image, xy: tuple[int, int], radius: int, border: int = 0) -> None:
    """带阴影与（可选）黑色手机边框的圆角卡片。"""
    x, y = xy
    if border:
        frame = Image.new("RGB", (img.width + border * 2, img.height + border * 2), (24, 26, 32))
        frame = rounded(frame, radius + border)
        inner = rounded(img, radius)
        frame.alpha_composite(inner, (border, border))
        card = frame
    else:
        card = rounded(img, radius)
    shadow = Image.new("RGBA", (card.width + 80, card.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [40, 52, card.width + 40, card.height + 52], radius + border, fill=(40, 50, 120, 70)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas.paste(shadow, (x - 40, y - 40), shadow)
    canvas.paste(card, (x, y), card)


def phone(name: str, width: int) -> Image.Image:
    """手机首屏截图（390x844 视口），按宽度缩放。"""
    img = Image.open(SHOTS / f"{name}.png").convert("RGB")
    img = img.crop((0, 0, img.width, int(img.width * 844 / 390)))
    return img.resize((width, int(width * img.height / img.width)), Image.LANCZOS)


def two_phones(left: str, right: str, target: str) -> None:
    canvas = background().convert("RGBA")
    pw = 440
    a, b = phone(left, pw), phone(right, pw)
    top = (H - a.height) // 2
    paste_card(canvas, a, (60, top - 30), 40, border=12)
    paste_card(canvas, b, (W - 60 - pw - 24, top + 30), 40, border=12)
    canvas.convert("RGB").save(OUT / target, quality=95)


def three_phones(names: list[str], target: str) -> None:
    canvas = background().convert("RGBA")
    pw = 300
    imgs = [phone(n, pw) for n in names]
    gap = (W - pw * 3 - 24 * 3) // 4
    for i, img in enumerate(imgs):
        x = gap + i * (pw + 24 + gap)
        y = (H - img.height) // 2 + (-40 if i == 1 else 30)
        paste_card(canvas, img, (x, y), 30, border=12)
    canvas.convert("RGB").save(OUT / target, quality=95)


def desktop_crop(name: str, box_css: tuple[int, int, int, int], target: str) -> None:
    """截取桌面截图（2x）中的一块，居中放在 3:4 画布上。"""
    img = Image.open(SHOTS / f"{name}.png").convert("RGB")
    crop = img.crop(tuple(v * 2 for v in box_css))
    width = W - 80
    crop = crop.resize((width, int(width * crop.height / crop.width)), Image.LANCZOS)
    canvas = background().convert("RGBA")
    paste_card(canvas, crop, (40, (H - crop.height) // 2), 24)
    canvas.convert("RGB").save(OUT / target, quality=95)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    two_phones("首页-手机", "案例-简历-手机", "图2-首页与简历成品.png")
    # 右侧结果预览区：两个模型并排出的两版
    desktop_crop("工作区-4-并排对比", (584, 21, 1420, 865), "图3-两个模型并排出稿.png")
    three_phones(["案例-博主主页-手机", "案例-简历-手机", "案例-邀请函-手机"], "图4-手机上打开的成品.png")
    desktop_crop("工作区-1-填写", (368, 250, 1142, 635), "图1b-粘贴经历.png")
    print("配图目录", OUT)


if __name__ == "__main__":
    main()
