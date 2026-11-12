"""给生成页补上微信分享用的 Open Graph 信息、打印脚本和「制作同款」浮标。

默认不解析整页：只在 <head> 里删掉同名标签、在 </head> 前插入新标签、在最后一个 </body> 前插入脚本。
50KB 的页面约 0.1 毫秒，可以直接在事件循环里做。找不到 </head> 的不规整页面才退回 BeautifulSoup（约 30 毫秒）。
"""

from __future__ import annotations

import html as html_lib
import json
import re

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
    '.report{display:inline-flex;align-items:center;height:24px;padding:0 8px;border-radius:999px;background:rgba(255,255,255,.7);color:#64748b;font-size:11px;text-decoration:none;border:1px solid rgba(15,23,42,.08)}',
    '.report:hover{color:#b42318}',
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
    cfg.report ? '<a class="report" href="' + cfg.report + '" target="_blank" rel="noopener" title="举报违规内容">举报</a>' : '',
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

_PRINT_SCRIPT = "window.addEventListener('load',function(){setTimeout(function(){window.print();},400);});"
_PLACEHOLDER_TITLES = {"", "生成页面"}

# <head> 里要跳过的原文块：注释、脚本、样式、标题里的文字都可能出现 "</head>" 或 "<meta" 字样。
_HEAD_TOKEN = re.compile(r"<!--|<(script|style|title|textarea|noscript)\b|</head\s*>", re.I)
_CLOSE_TAG = {name: re.compile(rf"</{name}\s*>", re.I) for name in ("script", "style", "title", "textarea", "noscript")}
# 属性值里可以有 ">"，按引号配对整段跳过。
_META_TAG = re.compile(r"<meta\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.I)
_ATTR = re.compile(r"""([^\s=/>"']+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""")


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
    report_url: str | None = None,
    force_title: bool = False,
) -> str:
    """在已有 HTML 的 head 里写入 og 标签。print_after_load 为真时，加载后调起打印。

    remix_url 有值时在页面右下角挂「用星页做同款」浮标；poster_url 有值时手机上多一个「发朋友圈」；
    report_url 有值时浮标旁多一个「举报」。force_title 为真时（用户自定义了分享标题）连页面 <title> 一起换掉，
    微信等只读 <title> 的地方显示的也是用户写的标题。
    """
    options = dict(
        title=title,
        description=description,
        image_url=image_url,
        page_url=page_url,
        print_after_load=print_after_load,
        remix_url=remix_url,
        poster_url=poster_url,
        report_url=report_url,
        force_title=force_title,
    )
    fast = inject_share_meta_fast(html, **options)
    return fast if fast is not None else inject_share_meta_with_soup(html, **options)


def inject_share_meta_fast(
    html: str,
    *,
    title: str,
    description: str,
    image_url: str | None = None,
    page_url: str | None = None,
    print_after_load: bool = False,
    remix_url: str | None = None,
    poster_url: str | None = None,
    report_url: str | None = None,
    force_title: bool = False,
) -> str | None:
    """不解析整页的版本，结果与 inject_share_meta 相同。找不到完整的 <head> 时返回 None，调用方改用 BeautifulSoup。"""
    scan = _scan_head(html)
    if scan is None:
        return None
    head_end, skipped, title_span = scan

    metas: list[tuple[str, str, str]] = [
        ("property", "og:title", title),
        ("property", "og:description", description),
        ("property", "og:type", "website"),
    ]
    if page_url:
        metas.append(("property", "og:url", page_url))
    if image_url:
        metas.append(("property", "og:image", image_url))
    metas.append(("name", "description", description))
    replaced = {key for _attr, key, _value in metas}

    edits: list[tuple[int, int, str]] = []
    for match in _META_TAG.finditer(html, 0, head_end):
        if _inside(match.start(), skipped):
            continue
        attrs = _attrs(match.group(0))
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        if key in replaced:
            edits.append((match.start(), match.end(), ""))

    block = "".join(f'<meta {attr}="{key}" content="{_attr_escape(value)}">' for attr, key, value in metas)
    if title_span is None:
        block += f"<title>{html_lib.escape(title, quote=False)}</title>"
    else:
        inner_start, inner_end = title_span
        current = html_lib.unescape(html[inner_start:inner_end]).strip()
        if not current or (title and (force_title or current in _PLACEHOLDER_TITLES)):
            edits.append((inner_start, inner_end, html_lib.escape(title, quote=False)))
    edits.append((head_end, head_end, block))

    script: str | None = None
    if print_after_load:
        script = _PRINT_SCRIPT
    elif remix_url:
        script = build_badge_script(remix_url=remix_url, poster_url=poster_url, report_url=report_url)
    if script is not None:
        lowered = html.lower()
        at = lowered.rfind("</body")
        if at < head_end:
            at = lowered.rfind("</html")
        if at < head_end:
            at = len(html)
        edits.append((at, at, f"<script>{script}</script>"))

    edits.sort(key=lambda item: (item[0], item[1]))
    parts: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        parts.append(html[cursor:start])
        parts.append(replacement)
        cursor = end
    parts.append(html[cursor:])
    return "".join(parts)


def extract_title(html: str) -> str:
    """页面 <title> 的文字（去掉首尾空白），没有就返回空串。只看页面头部。"""
    scan = _scan_head(html)
    if scan is None:
        soup = BeautifulSoup(html, "html.parser")
        return soup.title.get_text(" ", strip=True) if soup.title else ""
    title_span = scan[2]
    if title_span is None:
        return ""
    return " ".join(html_lib.unescape(html[title_span[0] : title_span[1]]).split())


def _scan_head(html: str) -> tuple[int, list[tuple[int, int]], tuple[int, int] | None] | None:
    """返回 (</head> 的位置, 要跳过的原文块, 第一个 <title> 的文字范围)。没有 </head> 或块没闭合时返回 None。"""
    skipped: list[tuple[int, int]] = []
    title_span: tuple[int, int] | None = None
    pos = 0
    while True:
        match = _HEAD_TOKEN.search(html, pos)
        if match is None:
            return None
        token = match.group(0)
        if token.startswith("</"):
            return match.start(), skipped, title_span
        if token == "<!--":
            close = html.find("-->", match.end())
            if close == -1:
                return None
            skipped.append((match.start(), close + 3))
            pos = close + 3
            continue
        name = match.group(1).lower()
        open_end = html.find(">", match.end())
        close_match = _CLOSE_TAG[name].search(html, open_end + 1) if open_end != -1 else None
        if close_match is None:
            return None
        skipped.append((match.start(), close_match.end()))
        if name == "title" and title_span is None:
            title_span = (open_end + 1, close_match.start())
        pos = close_match.end()


def _inside(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _attrs(tag: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _ATTR.finditer(tag, 5):
        value = next((group for group in match.groups()[1:] if group is not None), "")
        found.setdefault(match.group(1).lower(), html_lib.unescape(value))
    return found


def _attr_escape(value: str) -> str:
    return html_lib.escape(value or "", quote=True)


def inject_share_meta_with_soup(
    html: str,
    *,
    title: str,
    description: str,
    image_url: str | None = None,
    page_url: str | None = None,
    print_after_load: bool = False,
    remix_url: str | None = None,
    poster_url: str | None = None,
    report_url: str | None = None,
    force_title: bool = False,
) -> str:
    """整页解析的版本：页面结构不规整、快速插入做不了时用。"""
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
    elif title and (force_title or soup.title.string.strip() in {"", "生成页面"}):
        soup.title.string = title

    body = soup.body
    if print_after_load:
        if body is not None:
            script = soup.new_tag("script")
            script.string = "window.addEventListener('load',function(){setTimeout(function(){window.print();},400);});"
            body.append(script)
    elif remix_url and body is not None:
        script = soup.new_tag("script")
        script.string = build_badge_script(remix_url=remix_url, poster_url=poster_url, report_url=report_url)
        body.append(script)

    return str(soup)


def build_badge_script(*, remix_url: str, poster_url: str | None, report_url: str | None = None) -> str:
    config = json.dumps({"remix": remix_url, "poster": poster_url or "", "report": report_url or ""}, ensure_ascii=False)
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
