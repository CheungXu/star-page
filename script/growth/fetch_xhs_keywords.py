"""抓取小红书公开热词，并在有 DataXAPI Key 时按关键词搜公开笔记。

免费热词榜不需要注册账号：

    python3 script/growth/fetch_xhs_keywords.py

按关键词搜公开笔记（每个词约 0.19 元，失败不收费）。
Key 放在 config/dataxapi.env，变量名 DATAXAPI_API_KEY，不要提交到仓库：

    python3 script/growth/fetch_xhs_keywords.py --search
    python3 script/growth/fetch_xhs_keywords.py --search --query "作品集网站" --query "个人网站 简历"

已经成功写入的快照和关键词会跳过，可以断点续跑。
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jsonlines
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "growth" / "xhs"
STATE_PATH = DATA_DIR / "state.json"
HOT_PATH = DATA_DIR / "hot-inspiration.jsonl"
NOTES_PATH = DATA_DIR / "note-search.jsonl"
ENV_PATH = REPO_ROOT / "config" / "dataxapi.env"

HOT_URL = "https://api.dataxapi.com/v1/public/tools/xiaohongshu-inspiration"
SEARCH_URL = "https://api.dataxapi.com/v1/xiaohongshu/notes/search"

# 增长冷启动只看网页简历这一条线，控制付费搜索次数。
SEED_QUERIES = ["网页简历", "交互式简历", "AI简历", "个人主页", "求职简历"]


def load_env_file(path: Path) -> None:
    """读取本地 env。已有环境变量优先，避免把密钥写进命令行。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"hot_updated_at": None, "searches": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_json(url: str, *, payload: dict | None = None, api_key: str | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "User-Agent": "star-page-growth/1.0",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = str(uuid.uuid4())
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"接口返回 {exc.code}：{body[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络请求失败：{exc.reason}") from exc


def snapshot_name(updated_at: str) -> str:
    safe = updated_at.replace(":", "").replace("+", "")
    return f"hot-{safe}.jsonl"


def fetch_hot(state: dict, *, force: bool) -> list[dict]:
    """拉取免费公开热词榜。同一更新时间不重复落盘。"""
    payload = request_json(HOT_URL)
    updated_at = payload.get("updated_at")
    items = payload.get("items") or []
    if not updated_at or not isinstance(items, list):
        raise RuntimeError("热词榜返回结构不符合预期，未写入文件")

    if not force and state.get("hot_updated_at") == updated_at and HOT_PATH.exists():
        print(f"热词榜未更新（{updated_at}），沿用已有结果")
        return items

    rows = []
    for item in items:
        if not item.get("title"):
            continue
        rows.append(
            {
                "source": "dataxapi_public_hot_inspiration",
                "updated_at": updated_at,
                "next_refresh_at": payload.get("next_refresh_at"),
                "rank": item.get("rank"),
                "title": item.get("title"),
                "heat": item.get("heat"),
                "url": item.get("url"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    if not rows:
        raise RuntimeError("热词榜没有可写入的条目")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = DATA_DIR / "snapshots" / snapshot_name(updated_at)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    for path in (snapshot, HOT_PATH):
        with jsonlines.open(path, mode="w") as writer:
            writer.write_all(rows)

    state["hot_updated_at"] = updated_at
    state["hot_count"] = len(rows)
    state["next_refresh_at"] = payload.get("next_refresh_at")
    save_state(state)
    print(f"已写入 {len(rows)} 条热词：{HOT_PATH.relative_to(REPO_ROOT)}")
    return rows


def search_notes(state: dict, queries: list[str], api_key: str) -> None:
    """按关键词各搜一页公开笔记。成功的词写入 jsonl，失败的词留待下次重跑。"""
    done = state.setdefault("searches", {})
    pending = [query for query in queries if query not in done]
    if not pending:
        print("关键词搜索都已成功，跳过")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for query in tqdm(pending, desc="搜索公开笔记"):
        payload = request_json(
            SEARCH_URL,
            payload={
                "query": query,
                "sort": "most_liked",
                "note_type": "all",
                "published_within": "half_year",
            },
            api_key=api_key,
        )
        notes = ((payload.get("data") or {}).get("items")) or []
        meta = payload.get("meta") or {}
        rows = []
        for note in notes:
            title = note.get("title") or (note.get("text") or "")[:80]
            if not title and not note.get("id"):
                continue
            author = note.get("author") or {}
            metrics = note.get("metrics") or {}
            rows.append(
                {
                    "source": "dataxapi_notes_search",
                    "query": query,
                    "note_id": note.get("id"),
                    "title": note.get("title"),
                    "text": note.get("text"),
                    "url": note.get("url"),
                    "note_type": note.get("type"),
                    "author_name": author.get("name"),
                    "published_at": note.get("published_at"),
                    "likes": metrics.get("likes"),
                    "comments": metrics.get("comments"),
                    "saves": metrics.get("saves"),
                    "shares": metrics.get("shares"),
                    "charged_amount": meta.get("charged_amount"),
                    "request_id": payload.get("request_id"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        if rows:
            with jsonlines.open(NOTES_PATH, mode="a") as writer:
                writer.write_all(rows)
        done[query] = {
            "count": len(rows),
            "charged_amount": meta.get("charged_amount"),
            "request_id": payload.get("request_id"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
        print(f"{query}：{len(rows)} 条，扣费 {meta.get('charged_amount')} 元")


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取小红书公开热词，可选按关键词搜索笔记")
    parser.add_argument("--search", action="store_true", help="使用 DataXAPI Key 搜索简历相关公开笔记，会产生费用")
    parser.add_argument("--query", action="append", default=[], help="指定搜索词。可重复传入。传入后只搜这些词，不再搜默认词")
    parser.add_argument("--force", action="store_true", help="忽略已有快照，重新拉取热词榜")
    args = parser.parse_args()

    load_env_file(ENV_PATH)
    state = load_state()
    fetch_hot(state, force=args.force)

    if not args.search:
        print("未搜索具体关键词。需要时配置 config/dataxapi.env 后加 --search")
        return

    api_key = os.environ.get("DATAXAPI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("没有 DATAXAPI_API_KEY。把 Key 写到 config/dataxapi.env 后再加 --search")
    queries = args.query or SEED_QUERIES
    search_notes(state, queries, api_key)


if __name__ == "__main__":
    main()
