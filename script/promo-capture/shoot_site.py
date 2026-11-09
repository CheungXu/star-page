"""截取星页线上真实界面，给小红书配图和宣传视频用。

截图清单写在 SHOTS 里；已存在的图片会跳过，删掉对应文件即可重截。

用法：
    cd code/backend && . .venv/bin/activate
    python ../../script/promo-capture/shoot_site.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright
from tqdm import tqdm

CHROME = "/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
ORIGIN = "https://stars-page.com"

DESKTOP = {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2}
MOBILE = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
}

# (文件名, 路径, 设备, 是否整页)
SHOTS = [
    ("首页-桌面", "/", DESKTOP, False),
    ("首页-手机", "/", MOBILE, False),
    ("首页-手机-整页", "/", MOBILE, True),
    ("场景-简历-桌面", "/s/resume", DESKTOP, False),
    ("场景-简历-手机", "/s/resume", MOBILE, False),
    ("案例-简历-手机", "/u/p-5dbe5e19b2", MOBILE, False),
    ("案例-简历-手机-整页", "/u/p-5dbe5e19b2", MOBILE, True),
    ("案例-简历-桌面", "/u/p-5dbe5e19b2", DESKTOP, False),
    ("案例-简历-桌面-整页", "/u/p-5dbe5e19b2", DESKTOP, True),
    ("案例-博主主页-手机", "/u/p-8f9313b007", MOBILE, False),
    ("案例-博主主页-手机-整页", "/u/p-8f9313b007", MOBILE, True),
    ("案例-落地页-手机", "/u/p-8a661cb71b", MOBILE, False),
    ("案例-落地页-桌面", "/u/p-8a661cb71b", DESKTOP, False),
    ("案例-邀请函-手机", "/u/p-d4b6cc97f3", MOBILE, False),
    ("案例-邀请函-手机-整页", "/u/p-d4b6cc97f3", MOBILE, True),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/文案/202610/00-界面截图")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    out = root / args.out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [shot for shot in SHOTS if not (out / f"{shot[0]}.png").exists()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME)
        for name, path, device, full in tqdm(jobs, desc="截图"):
            ctx = browser.new_context(**device, locale="zh-CN")
            page = ctx.new_page()
            try:
                page.goto(ORIGIN + path, wait_until="networkidle", timeout=60000)
                # 首页案例卡片是 iframe 缩略图，等它们淡入
                page.wait_for_timeout(4000)
                if full:
                    # 触发懒加载与滚动入场动画后回到顶部
                    page.evaluate(
                        "async () => { for (let y = 0; y < document.body.scrollHeight; y += 300) "
                        "{ window.scrollTo(0, y); await new Promise(r => setTimeout(r, 400)); } window.scrollTo(0, 0); }"
                    )
                    # 屏幕外 iframe 不跑入场动画，把视口拉到整页高度再截
                    height = page.evaluate("document.documentElement.scrollHeight")
                    page.set_viewport_size({"width": device["viewport"]["width"], "height": min(height, 12000)})
                    page.wait_for_timeout(5000)
                page.screenshot(path=str(out / f"{name}.png"), full_page=full)
            except Exception as exc:  # noqa: BLE001
                print(f"截图失败 {name}: {exc}")
            finally:
                ctx.close()
        browser.close()
    print("截图目录", out)


if __name__ == "__main__":
    main()
