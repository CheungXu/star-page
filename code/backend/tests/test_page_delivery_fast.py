"""分享页交付的纯逻辑单测：头部快速插入分享信息（含各种不规整写法和兜底）、取标题、页面原文内存缓存。不需要数据库。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.page_cache import PageSourceCache  # noqa: E402
from app.services.share_meta import (  # noqa: E402
    extract_title,
    inject_share_meta,
    inject_share_meta_fast,
)

PAGE = (
    "<!DOCTYPE html><html><head>"
    '<meta charset="utf-8">'
    '<meta property="og:title" content="旧标题">'
    "<meta name='description' content='旧描述'>"
    '<meta name="viewport" content="width=device-width">'
    "<title>生成页面</title>"
    "</head><body><p>正文</p></body></html>"
)


def test_fast_replaces_share_metas_and_keeps_others():
    out = inject_share_meta_fast(PAGE, title="星空", description="一句话", page_url="https://x/p/1", image_url="https://x/i.png")
    assert out is not None
    head = out[: out.index("</head>")]
    assert "旧标题" not in head and "旧描述" not in head
    assert head.count('property="og:title"') == 1 and '<meta property="og:title" content="星空">' in head
    assert '<meta property="og:url" content="https://x/p/1">' in head
    assert '<meta property="og:image" content="https://x/i.png">' in head
    assert '<meta name="description" content="一句话">' in head
    assert 'name="viewport"' in head and 'charset="utf-8"' in head
    # 占位标题被换掉；正文不动。
    assert "<title>星空</title>" in head
    assert out.endswith("<body><p>正文</p></body></html>")


def test_fast_keeps_real_title_unless_forced():
    page = PAGE.replace("<title>生成页面</title>", "<title>我的主页</title>")
    assert "<title>我的主页</title>" in inject_share_meta_fast(page, title="别的", description="d")
    assert "<title>别的</title>" in inject_share_meta_fast(page, title="别的", description="d", force_title=True)


def test_fast_adds_title_when_missing_and_escapes_values():
    page = "<html><head></head><body></body></html>"
    out = inject_share_meta_fast(page, title='A&B "<x>"', description='引号"和<尖括号>')
    assert "<title>A&amp;B \"&lt;x&gt;\"</title>" in out
    assert 'content="引号&quot;和&lt;尖括号&gt;"' in out


def test_fast_ignores_head_markers_inside_raw_blocks():
    page = (
        "<html><head>"
        "<!-- </head> <meta property=\"og:title\" content=\"注释里的\"> -->"
        "<script>var s = '</head><meta name=\"description\" content=\"脚本里的\">';</script>"
        "<style>/* </head> */</style>"
        "<title>T</title>"
        "</head><body></body></html>"
    )
    out = inject_share_meta_fast(page, title="新", description="新描述")
    assert "注释里的" in out and "脚本里的" in out
    # 新标签插在真正的 </head> 前，而不是注释或脚本里那个。
    real_head_end = out.index("</head><body>")
    assert out.index('<meta property="og:title" content="新">') < real_head_end
    assert out.index('<meta property="og:title" content="新">') > out.index("</style>")


def test_fast_meta_with_gt_inside_quoted_attribute():
    page = '<html><head><meta property="og:description" content="a > b"><title>T</title></head><body></body></html>'
    out = inject_share_meta_fast(page, title="T", description="新")
    assert "a > b" not in out


def test_fast_badge_script_goes_before_last_body_close():
    page = "<html><head></head><body><script>document.write('</body>')</script><p>x</p></body></html>"
    out = inject_share_meta_fast(page, title="t", description="d", remix_url="https://x/remix")
    assert out.rindex("sp-made-with") < out.rindex("</body>")
    assert out.rindex("sp-made-with") > out.index("<p>x</p>")
    printed = inject_share_meta_fast(page, title="t", description="d", print_after_load=True, remix_url="https://x/remix")
    assert "window.print" in printed and "sp-made-with" not in printed


def test_falls_back_to_soup_without_head_close():
    page = "<html><body><p>没有 head</p></body></html>"
    assert inject_share_meta_fast(page, title="t", description="d") is None
    out = inject_share_meta(page, title="t", description="d")
    assert 'property="og:title"' in out and "<title>t</title>" in out
    # 原文块没闭合也退回兜底。
    assert inject_share_meta_fast("<html><head><script>var a;</head><body></body>", title="t", description="d") is None


def test_extract_title():
    assert extract_title("<html><head><title>  多个   空格 &amp; 实体 </title></head><body></body></html>") == "多个 空格 & 实体"
    assert extract_title("<html><head></head><body><title>正文里的不算</title></body></html>") == ""
    assert extract_title("<html><body><title>兜底</title></body></html>") == "兜底"


def test_page_cache_single_flight_and_lru():
    loads: list[str] = []

    async def loader(key: str) -> str:
        loads.append(key)
        await asyncio.sleep(0.01)
        return key * 1000

    async def run() -> None:
        cache = PageSourceCache(max_bytes=9000, loader=loader)
        # 同一个键并发只回源一次。
        results = await asyncio.gather(*(cache.get("a") for _ in range(5)))
        assert results == ["a" * 1000] * 5 and loads == ["a"]
        assert (cache.hits, cache.misses) == (0, 5)
        assert await cache.get("a") == "a" * 1000 and loads == ["a"] and cache.hits == 1
        # 超过上限时淘汰最久没用的。
        for key in "bcdefghij":
            await cache.get(key)
        assert cache.bytes <= 9000 and len(cache) < 10
        await cache.get("a")
        assert loads.count("a") == 2

    asyncio.run(run())


def test_page_cache_skips_oversized_and_failed_loads():
    calls = {"n": 0}

    async def loader(key: str) -> str:
        calls["n"] += 1
        if key == "bad":
            raise RuntimeError("下载失败")
        return "x" * 10_000

    async def run() -> None:
        cache = PageSourceCache(max_bytes=8000, loader=loader)
        # 单个页面超过上限的 1/8 不进缓存，每次回源。
        await cache.get("big")
        await cache.get("big")
        assert calls["n"] == 2 and len(cache) == 0
        # 回源失败不缓存，下次重试。
        for _ in range(2):
            try:
                await cache.get("bad")
            except RuntimeError:
                pass
        assert calls["n"] == 4

    asyncio.run(run())
