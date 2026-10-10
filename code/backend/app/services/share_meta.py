"""给生成页补上微信分享用的 Open Graph 信息、打印脚本和「制作同款」浮标。"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

# 浮标挂在 Shadow DOM 里，生成页自己的 CSS 碰不到它，它也不会改动页面样式。
_BADGE_SCRIPT = """
(function () {
  var cfg = __CFG__;
  try { if (window.self !== window.top) return; } catch (e) { return; }
  if (document.getElementById('sp-made-with')) return;
  var host = document.createElement('div');
  host.id = 'sp-made-with';
  host.style.cssText = 'all:initial;position:fixed;z-index:2147483646;right:0;bottom:0;';
  var root = host.attachShadow ? host.attachShadow({ mode: 'closed' }) : host;
  var mobile = window.matchMedia && window.matchMedia('(max-width: 720px)').matches;
  var showPoster = Boolean(cfg.poster) && mobile;
  root.innerHTML = [
    '<style>',
    ':host{all:initial}',
    '.bar{position:fixed;right:max(14px,env(safe-area-inset-right));bottom:max(14px,env(safe-area-inset-bottom));display:flex;gap:6px;align-items:center;font:500 12.5px/1 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;}',
    '.pill{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 13px;border-radius:999px;border:1px solid rgba(255,255,255,.55);background:rgba(17,24,39,.72);color:#fff;text-decoration:none;cursor:pointer;-webkit-backdrop-filter:blur(12px) saturate(140%);backdrop-filter:blur(12px) saturate(140%);box-shadow:0 6px 20px -8px rgba(15,23,42,.45);letter-spacing:.01em;transition:transform .18s ease,background .18s ease;}',
    '.pill:hover{transform:translateY(-1px);background:rgba(17,24,39,.86)}',
    '.pill.light{background:rgba(255,255,255,.86);color:#111827;border-color:rgba(15,23,42,.08)}',
    '.star{color:#a5b4fc;font-size:13px}',
    '.mask{position:fixed;inset:0;display:none;align-items:center;justify-content:center;padding:24px;background:rgba(15,23,42,.62);-webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px)}',
    '.mask.open{display:flex}',
    '.sheet{width:min(360px,100%);max-height:100%;overflow:auto;padding:14px;border-radius:20px;background:#fff;text-align:center;font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#111827}',
    '.sheet img{display:block;width:100%;border-radius:12px;background:#f3f4f6;-webkit-touch-callout:default}',
    '.tip{margin:12px 0 4px;font-weight:600}',
    '.sub{margin:0 0 12px;color:#6b7280;font-size:12.5px}',
    '.close{height:36px;width:100%;border:0;border-radius:10px;background:#f3f4f6;color:#111827;font:inherit;cursor:pointer}',
    '@media print{.bar,.mask{display:none!important}}',
    '</style>',
    '<div class="bar">',
    showPoster ? '<button class="pill light" type="button" data-act="poster">发朋友圈</button>' : '',
    '<a class="pill" href="' + cfg.remix + '" target="_blank" rel="noopener"><span class="star">✦</span>用星页做同款</a>',
    '</div>',
    showPoster ? '<div class="mask" data-act="close"><div class="sheet" role="dialog" aria-label="朋友圈海报"><img alt="页面海报" src="' + cfg.poster + '"><p class="tip">长按图片保存，再发到朋友圈</p><p class="sub">海报里的二维码会打开这个页面</p><button class="close" type="button" data-act="close">关闭</button></div></div>' : ''
  ].join('');
  root.addEventListener('click', function (event) {
    var target = event.target;
    var act = target && target.getAttribute && target.getAttribute('data-act');
    var mask = root.querySelector('.mask');
    if (act === 'poster' && mask) { mask.classList.add('open'); }
    if (act === 'close' && mask && (target === mask || target.classList.contains('close'))) { mask.classList.remove('open'); }
  });
  (document.body || document.documentElement).appendChild(host);
})();
"""


def inject_share_meta(
    html: str,
    *,
    title: str,
    description: str,
    image_url: str | None = None,
    page_url: str | None = None,
    print_after_load: bool = False,
    remix_url: str | None = None,
    poster_url: str | None = None,
) -> str:
    """在已有 HTML 的 head 里写入 og 标签。print_after_load 为真时，加载后调起打印。

    remix_url 有值时在页面右下角挂「用星页做同款」浮标；poster_url 有值时手机上多一个「发朋友圈」。
    """
    soup = BeautifulSoup(html, "html.parser")
    head = soup.head
    if head is None:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)

    _upsert_meta(soup, head, "og:title", title)
    _upsert_meta(soup, head, "og:description", description)
    _upsert_meta(soup, head, "og:type", "website")
    if page_url:
        _upsert_meta(soup, head, "og:url", page_url)
    if image_url:
        _upsert_meta(soup, head, "og:image", image_url)

    description_tag = soup.find("meta", attrs={"name": "description"})
    if description_tag is None:
        description_tag = soup.new_tag("meta")
        description_tag["name"] = "description"
        head.append(description_tag)
    description_tag["content"] = description

    if soup.title is None or not soup.title.string:
        title_tag = soup.title or soup.new_tag("title")
        title_tag.string = title
        if soup.title is None:
            head.append(title_tag)
    elif title and soup.title.string.strip() in {"", "生成页面"}:
        soup.title.string = title

    body = soup.body
    if print_after_load:
        if body is not None:
            script = soup.new_tag("script")
            script.string = "window.addEventListener('load',function(){setTimeout(function(){window.print();},400);});"
            body.append(script)
    elif remix_url and body is not None:
        script = soup.new_tag("script")
        script.string = build_badge_script(remix_url=remix_url, poster_url=poster_url)
        body.append(script)

    return str(soup)


def build_badge_script(*, remix_url: str, poster_url: str | None) -> str:
    config = json.dumps({"remix": remix_url, "poster": poster_url or ""}, ensure_ascii=False)
    # 防止配置里的字符串提前闭合 <script>。
    config = config.replace("</", "<\\/")
    return _BADGE_SCRIPT.replace("__CFG__", config)


def _upsert_meta(soup: BeautifulSoup, head, prop: str, content: str) -> None:
    tag = soup.find("meta", attrs={"property": prop})
    if tag is None:
        tag = soup.new_tag("meta")
        tag["property"] = prop
        head.append(tag)
    tag["content"] = content
