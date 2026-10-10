"""把候选页截成手机 / 桌面两张图，并按场景拼成一张对比图，方便挑选案例。

用法：
    cd code/backend && . .venv/bin/activate
    python ../../script/scene-showcase/shoot_variants.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright
from tqdm import tqdm

CHROME = "/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="/tmp/scene-audit/variants.json")
    parser.add_argument("--out", default="/tmp/scene-audit/variants")
    parser.add_argument("--origin", default="http://8.138.118.232:3001")
    args = parser.parse_args()

    data = json.loads(Path(args.variants).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [
        (scene, item)
        for scene, result in data.items()
        for item in result["variants"]
        if item["status"] == "completed"
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME)
        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        desktop = browser.new_context(viewport={"width": 1280, "height": 800})
        for scene, item in tqdm(jobs, desc="截图"):
            path = "/p/" + item["page_url"].split("/p/", 1)[1]
            url = args.origin + path
            for name, ctx in (("m", mobile), ("d", desktop)):
                target = out / f"{scene}-{item['model']}-{name}.png"
                if target.exists():
                    continue
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(2500)
                    page.screenshot(path=str(target))
                finally:
                    page.close()
        browser.close()

    font = ImageFont.truetype(FONT, 28)
    for scene in data:
        items = [item for item in data[scene]["variants"] if item["status"] == "completed"]
        if not items:
            continue
        tiles = []
        for item in items:
            m = Image.open(out / f"{scene}-{item['model']}-m.png").convert("RGB").resize((390, 844))
            d = Image.open(out / f"{scene}-{item['model']}-d.png").convert("RGB").resize((640, 400))
            tile = Image.new("RGB", (390 + 20 + 640, 900), "white")
            tile.paste(m, (0, 50))
            tile.paste(d, (410, 50))
            ImageDraw.Draw(tile).text((0, 4), f"{item['model']} · {item['label']}", fill="black", font=font)
            tiles.append(tile)
        sheet = Image.new("RGB", (tiles[0].width, 900 * len(tiles) + 30 * (len(tiles) - 1)), "#e5e7eb")
        for index, tile in enumerate(tiles):
            sheet.paste(tile, (0, index * 930))
        sheet.save(out / f"sheet-{scene}.png")
        print("已生成对比图", out / f"sheet-{scene}.png")


if __name__ == "__main__":
    main()
