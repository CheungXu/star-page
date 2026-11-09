"""用本机已登录的小红书会话，按收藏量再收一轮公开笔记。

先在本机启动 xiaohongshu-mcp（默认 http://127.0.0.1:18060），并已完成扫码登录。

    python3 script/growth/fetch_xhs_login_search.py

同一个「关键词 + 排序」成功后会跳过，可以断点续跑。只把有标题的笔记写入文件。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jsonlines
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "growth" / "xhs"
STATE_PATH = DATA_DIR / "state.json"
OUT_PATH = DATA_DIR / "login-search.jsonl"

DEFAULT_QUERIES = ["作品集网站", "网页简历", "交互式简历", "个人网站"]


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def search(base: str, keyword: str, sort_by: str) -> list[dict]:
    body = json.dumps(
        {"keyword": keyword, "filters": {"sort_by": sort_by}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        base.rstrip("/") + "/api/v1/feeds/search",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"搜索失败 {exc.code}：{detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"连不上本机小红书服务：{exc.reason}") from exc
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "搜索没有成功")
    data = payload.get("data") or {}
    return data.get("feeds") or []


def to_row(keyword: str, sort_by: str, feed: dict) -> dict | None:
    card = feed.get("noteCard") or {}
    title = (card.get("displayTitle") or "").strip()
    note_id = feed.get("id")
    if not title or not note_id:
        return None
    user = card.get("user") or {}
    info = card.get("interactInfo") or {}
    return {
        "source": "xiaohongshu_login_search",
        "query": keyword,
        "sort_by": sort_by,
        "note_id": note_id,
        "title": title,
        "note_type": card.get("type"),
        "author_name": user.get("nickname") or user.get("nickName"),
        "likes": info.get("likedCount"),
        "saves": info.get("collectedCount"),
        "comments": info.get("commentCount"),
        "shares": info.get("sharedCount"),
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="用小红书登录态按收藏量搜索公开笔记")
    parser.add_argument("--base", default="http://127.0.0.1:18060")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--sort", default="最多收藏")
    args = parser.parse_args()

    queries = args.query or DEFAULT_QUERIES
    state = load_state()
    done = state.setdefault("login_searches", {})
    pending = [q for q in queries if f"{q}|{args.sort}" not in done]
    if not pending:
        print("这批关键词已经搜过，跳过")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for keyword in tqdm(pending, desc="登录态搜索"):
        feeds = search(args.base, keyword, args.sort)
        rows = [row for feed in feeds if (row := to_row(keyword, args.sort, feed))]
        if rows:
            with jsonlines.open(OUT_PATH, mode="a") as writer:
                writer.write_all(rows)
        done[f"{keyword}|{args.sort}"] = {
            "count": len(rows),
            "sort_by": args.sort,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
        print(f"{keyword}：{len(rows)} 条")


if __name__ == "__main__":
    main()
