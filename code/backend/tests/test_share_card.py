"""分享卡片：海报三种对齐都能画出来，标题 / 副标题的生效规则（用户改写优先、清空即恢复默认）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import publications  # noqa: E402
from app.services.share_card import CARD_ALIGNS, render_card_png, render_qr_png  # noqa: E402

PIL = pytest.importorskip("PIL.Image")


@pytest.mark.parametrize("align", CARD_ALIGNS)
def test_poster_renders_every_alignment(align):
    qr = render_qr_png("https://stars-page.com/u/p-test000001")
    png = render_card_png(title="星尘回响 · 实时粒子观测站", subtitle="由星页 StarPage 生成", qr_png=qr, wide=False, align=align)
    image = PIL.open(io.BytesIO(png))
    assert image.size in {(1080, 1620), (320, 320)}


def test_unknown_alignment_falls_back_to_center():
    qr = render_qr_png("https://stars-page.com/u/p-test000002")
    assert render_card_png(title="标题", subtitle="副标题", qr_png=qr, wide=False, align="justify")


def test_wide_card_keeps_working():
    qr = render_qr_png("https://stars-page.com/u/p-test000003")
    image = PIL.open(io.BytesIO(render_card_png(title="标题", subtitle="副标题", qr_png=qr, wide=True)))
    assert image.size in {(1200, 630), (320, 320)}


def test_clean_card_text_collapses_whitespace_and_treats_blank_as_default():
    assert publications.clean_card_text("  星尘   回响 \n", 60) == "星尘 回响"
    assert publications.clean_card_text("   ", 60) is None
    assert publications.clean_card_text(None, 60) is None
    assert publications.clean_card_text("一" * 80, 60) == "一" * 60


def _publication(**overrides):
    values = {"custom_title": None, "custom_subtitle": None, "card_align": "center", "scene_key": None, "title": "", "og_description": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_apply_card_texts_prefers_custom_and_falls_back_to_defaults():
    publication = _publication()
    publications.apply_card_texts(publication, default_title="星尘回响")
    assert (publication.title, publication.og_description) == ("星尘回响", publications.DEFAULT_SUBTITLE)
    assert not publications.is_card_customized(publication)

    publication.custom_title = "两万颗粒子的引力之舞"
    publication.custom_subtitle = "鼠标拖动，看它们如何回应你"
    publications.apply_card_texts(publication, default_title="换了一版的标题")
    assert publication.title == "两万颗粒子的引力之舞"
    assert publication.og_description == "鼠标拖动，看它们如何回应你"
    assert publications.is_card_customized(publication)


def test_custom_title_replaces_page_title_only_when_forced():
    from app.services.share_meta import inject_share_meta

    html = "<html><head><title>星尘回响</title></head><body></body></html>"
    kept = inject_share_meta(html, title="我的星空主页", description="副标题")
    assert "<title>星尘回响</title>" in kept and 'content="我的星空主页"' in kept
    forced = inject_share_meta(html, title="我的星空主页", description="副标题", force_title=True)
    assert "<title>我的星空主页</title>" in forced


def test_alignment_alone_counts_as_customized():
    assert publications.is_card_customized(_publication(card_align="left"))
    assert not publications.is_card_customized(_publication(card_align="center"))
