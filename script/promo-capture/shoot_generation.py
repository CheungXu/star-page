"""在线上用匿名身份真实跑一次「网页简历」生成，截取工作区各阶段。

每次运行都会消耗一次匿名免费额度、产生一条真实生成记录。

用法：
    cd code/backend && . .venv/bin/activate
    python ../../script/promo-capture/shoot_generation.py
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = "/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
ORIGIN = "https://stars-page.com"

# 虚构人物，避免出现真实个人信息
RESUME = """陈一然｜2026 届应届生｜意向：产品运营
学校：西南交通大学 · 信息管理与信息系统 · 本科（2022-2026）
电话：13800001234  邮箱：yiran.chen@example.com  城市：成都

实习经历
1. 某本地生活平台 · 用户运营实习生（2025.07-2025.12）
- 负责成都区域新人券活动，3 个月新客首单转化率从 11% 提到 17%
- 搭建社群 SOP，管理 12 个商家群、4,000+ 用户，周活跃率保持 35% 以上
2. 校园新媒体中心 · 内容负责人（2023.09-2025.06）
- 带 6 人小组运营学校公众号，单篇最高阅读 3.2 万
- 策划「毕业季 100 张笑脸」专题，全网曝光 50 万+

项目
- 二手教材小程序（独立负责需求与运营）：上线一学期，注册用户 2,300，成交 1,100 单

技能：SQL、Excel 数据透视、Figma、剪映；英语 CET-6"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/文案/202610/00-界面截图")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    out = root / args.out
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale="zh-CN")
        page = ctx.new_page()
        page.goto(ORIGIN + "/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        page.locator(".prompt-inspiration-chip", has_text="网页简历").first.click()
        page.wait_for_timeout(1500)
        box = page.locator("textarea").first
        prefill = box.input_value()
        box.fill("")
        box.type(prefill + "\n\n" + RESUME, delay=4)
        page.wait_for_timeout(800)
        page.screenshot(path=str(out / "工作区-1-填写.png"))

        page.locator("button[aria-label^='生成']").first.click()
        start = time.time()
        page.wait_for_timeout(15000)
        page.screenshot(path=str(out / "工作区-2-生成中.png"))

        shot_mid = False
        while time.time() - start < 600:
            ready = page.locator(".preview-grid .dot.ready").count()
            failed = page.locator(".preview-grid .dot.failed").count()
            total = page.locator(".preview-grid .preview-cell-head").count()
            print(f"已完成 {ready}/{total}，失败 {failed}，用时 {int(time.time() - start)}s")
            if ready == 1 and not shot_mid:
                page.screenshot(path=str(out / "工作区-3-一版完成.png"))
                shot_mid = True
            if total and ready + failed >= total:
                break
            page.wait_for_timeout(10000)

        page.wait_for_timeout(6000)
        page.screenshot(path=str(out / "工作区-4-并排对比.png"))
        print("当前地址", page.url)
        ctx.close()
        browser.close()
    print("截图目录", out)


if __name__ == "__main__":
    main()
