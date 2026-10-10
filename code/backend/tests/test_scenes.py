"""场景目录、分享 meta 和发布 slug 的纯函数测试。"""

from app.services.publications import allocate_slug, normalize_slug
from app.services.scenes.catalog import compose_scene_prompt, format_image_block, get_scene, get_scene_catalog
from app.services.share_meta import inject_share_meta


def test_scene_catalog_has_p0_and_p1() -> None:
    get_scene_catalog.cache_clear()
    keys = set(get_scene_catalog())
    assert {"resume", "creator-home", "landing", "invitation"} <= keys
    landing = get_scene("landing")
    assert landing is not None
    assert landing.skill_key == "landing"
    assert "distinctive-design" in landing.extra_skill_keys


def test_compose_resume_masks_contact_and_appends_style() -> None:
    scene = get_scene("resume")
    assert scene is not None
    text = compose_scene_prompt(scene, "", {"name": "林晓"}, "bigtech", reveal_contact=False)
    assert "林晓" in text
    assert "大厂极简" in text
    assert "打码" in text
    revealed = compose_scene_prompt(scene, "请做简历", {}, None, reveal_contact=True)
    assert "打码" not in revealed
    long_text = compose_scene_prompt(scene, "经历" * 3000, {"name": "林晓"}, "bigtech", reveal_contact=False)
    assert "林晓" in long_text
    assert "打码" in long_text
    assert len(long_text) <= 4000


def test_format_image_block_lists_urls() -> None:
    block = format_image_block([("头像", "https://example.com/assets/1", "a.png")])
    assert "https://example.com/assets/1" in block
    assert "<img>" in block


def test_inject_share_meta_writes_og_and_print_script() -> None:
    html = "<!doctype html><html><head><title>生成页面</title></head><body><p>hi</p></body></html>"
    rendered = inject_share_meta(
        html,
        title="林晓的简历",
        description="网页简历",
        image_url="https://example.com/u/p-1/og.png",
        page_url="https://example.com/u/p-1",
        print_after_load=True,
    )
    assert 'property="og:title"' in rendered
    assert "林晓的简历" in rendered
    assert "og.png" in rendered
    assert "window.print" in rendered


def test_preview_bot_skips_wechat_readers() -> None:
    from app.services.analytics.tracking import is_preview_bot

    assert is_preview_bot("Mozilla/5.0 mpcrawler")
    assert not is_preview_bot("Mozilla/5.0 MicroMessenger/8.0")


def test_slug_rules() -> None:
    assert normalize_slug("Lin-Xiao") == "lin-xiao"
    assert normalize_slug("中文") is None
    assert normalize_slug("ab") is None
    slug = allocate_slug(None)
    assert slug.startswith("p-")
    assert normalize_slug(slug) == slug
