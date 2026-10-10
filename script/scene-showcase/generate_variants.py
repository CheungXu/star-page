"""为四个场景各生成四份候选页（四个模型并行），结果写入 JSON，供截图挑选。

用法（测试环境）：
    cd code/backend && . .venv/bin/activate
    python ../../script/scene-showcase/generate_variants.py --token-file /tmp/sp-test-session.txt

已经成功的场景会写进输出文件，重跑时跳过（断点续跑）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

MODELS = ["qwen", "doubao", "glm-5.3", "kimi-k3"]

SCENES: dict[str, dict] = {
    "resume": {
        "style_key": "bigtech",
        "prompt": (
            "请把下面这份简历做成网页简历。\n"
            "林晓，产品经理，6 年互联网产品经验，现居上海。\n"
            "2022.03 - 至今  云栖科技 · 高级产品经理（会员增长）\n"
            "- 从 0 到 1 搭建付费会员体系，上线 12 个月付费会员 38 万，续费率 61%。\n"
            "- 主导新人 7 日激活链路改版，次日留存从 31% 提升到 42%。\n"
            "- 带领 2 名产品、协同 14 名研发和 3 名设计，按双周节奏交付。\n"
            "2019.07 - 2022.02  橙子出行 · 产品经理（用户增长）\n"
            "- 负责拉新裂变活动，单季度带来 120 万新注册用户，获客成本下降 27%。\n"
            "- 建立 A/B 实验平台需求规范，一年完成 86 次实验。\n"
            "2017.07 - 2019.06  橙子出行 · 产品助理\n"
            "- 负责订单详情与客服工单系统，工单处理时长缩短 35%。\n"
            "教育：复旦大学 · 信息管理与信息系统 · 本科 · 2013-2017\n"
            "技能：会员体系设计、增长实验、用户研究、SQL、Axure、Figma\n"
            "联系方式：13800138000，linxiao@example.com"
        ),
        "guide": {"name": "林晓", "target_role": "高级产品经理（会员增长）", "highlight": "付费会员体系从 0 到 1，12 个月 38 万付费会员"},
    },
    "creator-home": {
        "prompt": (
            "请做一个博主个人主页。\n"
            "昵称：星野。定位：帮独立开发者把想法做成能发出去的页面。\n"
            "简介：前大厂设计师，现在全职做独立产品，记录从 0 到 1 的每一步。\n"
            "平台：小红书「星野手记」1.8 万关注；公众号「星野手记」；B 站「星野造物」。\n"
            "代表作品：《三分钟网页简历》模板（热门）、《独立开发者落地页》课程、每周一期《造物周报》。\n"
            "合作：品牌合作和咨询请私信，微信二维码放在页面下方。"
        ),
        "guide": {
            "name": "星野",
            "positioning": "帮独立开发者把想法做成能发出去的页面",
            "links": "小红书：星野手记\n公众号：星野手记\nB 站：星野造物",
            "works": "三分钟网页简历模板\n独立开发者落地页课程\n造物周报",
        },
    },
    "landing": {
        "prompt": (
            "请为产品「星页」做一个产品落地页。\n"
            "一句话：一份文档，变成一个能发出去的网页。\n"
            "目标用户：求职者、知识博主、独立开发者、需要做活动页的运营。\n"
            "核心能力：上传 Word / PDF / PPT 自动排版成网页；同一份资料并排对比多个模型的设计；"
            "网页简历、博主主页、邀请函等场景模板；发布后链接固定不变，可生成朋友圈海报。\n"
            "使用步骤：上传资料 → 选择场景和模型 → 挑一版发布。\n"
            "不要编造用户数、百分比、价格和客户 logo。主按钮文案：免费做一个。"
        ),
        "guide": {"product_name": "星页", "one_liner": "一份文档，变成一个能发出去的网页", "audience": "求职者、博主和独立开发者"},
    },
    "invitation": {
        "prompt": (
            "请做一张活动邀请函。\n"
            "活动：周六设计沙龙 · 第 12 期「界面里的日常」。\n"
            "时间：2026 年 10 月 18 日（周六）14:00 - 17:30，13:30 开始签到。\n"
            "地点：广州 · 海珠区 · 拾间书店二楼。\n"
            "议程：14:00 开场；14:20 分享「把日常做进界面」；15:10 圆桌「设计师的副业」；16:10 作品互评；17:00 自由交流。\n"
            "着装：舒适即可，欢迎带一件你的作品。\n"
            "报名：名额 40 人，回复「沙龙」报名。这场活动还没有举办。"
        ),
        "guide": {"title": "周六设计沙龙 · 第 12 期", "when_where": "2026 年 10 月 18 日 14:00 · 广州海珠区拾间书店", "rsvp": "回复「沙龙」报名，名额 40 人"},
    },
}


async def consume(client: httpx.AsyncClient, base: str, cookies: dict, task_id: str) -> str:
    event = None
    async with client.stream("GET", f"{base}/api/generations/{task_id}/events", cookies=cookies, timeout=None) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event in {"completed", "failed"}:
                return event
    return "disconnected"


async def run_scene(client: httpx.AsyncClient, base: str, cookies: dict, key: str, spec: dict) -> dict:
    body = {"scene_key": key, "models": MODELS, **spec}
    created = await client.post(f"{base}/api/generations", json=body, cookies=cookies, timeout=60)
    created.raise_for_status()
    payload = created.json()
    runs = payload["runs"]
    print(f"已创建 {key}：{len(runs)} 个模型", flush=True)
    statuses = await asyncio.gather(*(consume(client, base, cookies, run["task_id"]) for run in runs))
    variants = []
    for run, status in zip(runs, statuses):
        print(f"  {key} · {run['model_label']}：{status}", flush=True)
        variants.append({"model": run["model_key"], "label": run["model_label"], "page_id": run["page_id"], "page_url": run["page_url"], "status": status})
    return {"conversation_id": payload["conversation_id"], "variants": variants}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default="/tmp/scene-audit/variants.json")
    parser.add_argument("--models", default=",".join(MODELS), help="逗号分隔的模型 key")
    args = parser.parse_args()
    MODELS[:] = [item.strip() for item in args.models.split(",") if item.strip()]

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    cookies = {"sp_session": token}
    out = Path(args.out)
    done: dict = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    todo = {key: spec for key, spec in SCENES.items() if key not in done}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(run_scene(client, args.base, cookies, key, spec) for key, spec in todo.items()),
            return_exceptions=True,
        )
    for key, result in zip(todo, results):
        if isinstance(result, Exception):
            print(f"{key} 失败：{result}", flush=True)
            continue
        if any(item["status"] == "completed" for item in result["variants"]):
            done[key] = result
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ALL_DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
