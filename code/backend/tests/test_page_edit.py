"""局部编辑模块（page_edit）纯逻辑单测。

    /root/star-page/code/backend/.venv/bin/pytest code/backend/tests/test_page_edit.py
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.page_edit import (  # noqa: E402
    AppliedBlockDetail,
    EditBlock,
    ModelOutputKind,
    apply_edit_blocks,
    apply_page_edits,
    classify_model_output,
    normalize_newlines,
    parse_edit_blocks,
)

BASE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>年度产品报告</title>
  <style>
    .hero { color: #111827; background: #ffffff; }
    .card { padding: 16px; }
  </style>
</head>
<body>
  <header class="hero">
    <h1>2025 年度产品报告</h1>
    <p class="subtitle">稳健增长与技术突破</p>
  </header>
  <main>
    <section class="card" id="summary">
      <h2>核心指标</h2>
      <p>营收同比增长 32%</p>
    </section>
  </main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 1. 模型输出形态判别（classify_model_output）
# ---------------------------------------------------------------------------


def test_classify_bare_and_fenced_edit_blocks():
    bare = (
        "<<<<<<< SEARCH\n"
        "  <title>年度产品报告</title>\n"
        "=======\n"
        "  <title>2026 年度产品报告</title>\n"
        ">>>>>>> REPLACE\n"
    )
    assert classify_model_output(bare) == ModelOutputKind.EDIT_BLOCKS

    fenced_multi = (
        "下面按要求做两处局部调整：\n\n"
        "```diff\n"
        "<<<<<<< SEARCH\n"
        "<h1>2025 年度产品报告</h1>\n"
        "=======\n"
        "<h1>2026 年度产品报告</h1>\n"
        ">>>>>>> REPLACE\n"
        "```\n\n"
        "同时调整副标题：\n"
        "```html\n"
        "<<<<<<< SEARCH\n"
        "<p class=\"subtitle\">稳健增长与技术突破</p>\n"
        "=======\n"
        "<p class=\"subtitle\">全面迈向智能化</p>\n"
        ">>>>>>> REPLACE\n"
        "```\n"
    )
    assert classify_model_output(fenced_multi) == ModelOutputKind.EDIT_BLOCKS


def test_classify_edit_block_touching_doctype_and_html_tag_is_still_edit_blocks():
    """编辑块内容本身修改了 <!doctype html> / <html> 时，仍必须判为编辑块而非整页 HTML。"""
    head_edit = (
        "```html\n"
        "<<<<<<< SEARCH\n"
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "=======\n"
        "<!doctype html>\n"
        '<html lang="en">\n'
        ">>>>>>> REPLACE\n"
        "```"
    )
    assert classify_model_output(head_edit) == ModelOutputKind.EDIT_BLOCKS


def test_classify_full_html_variants_and_benign_symbols():
    """不含编辑块标记的完整 HTML（含 CSS 分割注释、JS >>> 位移、对称装饰符等）应判为 FULL_HTML。"""
    assert classify_model_output(BASE_HTML) == ModelOutputKind.FULL_HTML

    fenced_html = f"由于改动较大，直接输出完整页面：\n```html\n{BASE_HTML}\n```"
    assert classify_model_output(fenced_html) == ModelOutputKind.FULL_HTML

    no_doctype_html = "<html><head></head><body><h1>Hello</h1></body></html>"
    assert classify_model_output(no_doctype_html) == ModelOutputKind.FULL_HTML

    html_with_tricky_content = """<!DOCTYPE html>
<html>
<head>
<style>
/* ======= Navigation ======= */
</style>
</head>
<body>
  <pre>
=======
  </pre>
  <div><<<<<<< SYSTEM ONLINE >>>>>>></div>
  <script>
    const mask = (255 >>> 2) << 1;
  </script>
</body>
</html>"""
    assert classify_model_output(html_with_tricky_content) == ModelOutputKind.FULL_HTML


@pytest.mark.parametrize(
    "malformed_text",
    [
        "",
        "   \n\t  ",
        "好的，我已经理解您的修改要求。",
        "<div>没有 html 根标签也没有编辑标记的半截片段</div>",
        # 有 SEARCH 但缺 ======= 与 >>>>>>> REPLACE（即使内容含 <html> 也绝不能当完整 HTML 发布）
        "<<<<<<< SEARCH\n<!doctype html>\n<html><body>old</body></html>",
        # 有 SEARCH 与 ======= 但缺 >>>>>>> REPLACE
        "<<<<<<< SEARCH\n<h1>old</h1>\n=======\n<h1>new</h1>",
        # 有 SEARCH 与 >>>>>>> REPLACE 但缺中间 =======
        "<<<<<<< SEARCH\n<h1>old</h1>\n>>>>>>> REPLACE",
        # 块外孤立的 >>>>>>> REPLACE
        "<h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> REPLACE",
        # 第一个代码块内未写 >>>>>>> REPLACE 就关闭了代码围栏，防止吞入后续代码块
        "```html\n<<<<<<< SEARCH\n<h1>old</h1>\n=======\n<h1>new</h1>\n```\n说明文字\n```html\n<p>x</p>\n>>>>>>> REPLACE\n```",
        # 第一个块未闭合紧接着开启第二个块
        "<<<<<<< SEARCH\n<h1>a</h1>\n=======\n<h1>b</h1>\n<<<<<<< SEARCH\n<p>c</p>\n=======\n<p>d</p>\n>>>>>>> REPLACE",
        # 同一编辑块内出现两次 =======
        "<<<<<<< SEARCH\n<h1>a</h1>\n=======\n<h1>b</h1>\n=======\n<h1>c</h1>\n>>>>>>> REPLACE",
        # 编辑块外存在悬空的 =======
        "<<<<<<< SEARCH\n<h1>a</h1>\n=======\n<h1>b</h1>\n>>>>>>> REPLACE\n\n<p>c</p>\n=======\n<p>d</p>",
        # 标记与代码粘在同一行
        "<<<<<<< SEARCH <h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> REPLACE",
        "<<<<<<< SEARCH\n<h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> REPLACE```",
        # 尖括号数量错误、缺空格、小写标记或 Git 冲突标记误用
        "<<<<<< SEARCH\n<h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> REPLACE",
        "<<<<<<<SEARCH\n<h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> REPLACE",
        "<<<<<<< search\n<h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> replace",
        "<<<<<<< HEAD\n<h1>old</h1>\n=======\n<h1>new</h1>\n>>>>>>> REPLACE",
        "<<<<<<<\n<h1>old</h1>",
        ">>>>>>>",
    ],
)
def test_classify_and_parse_reject_malformed_outputs(malformed_text: str):
    assert classify_model_output(malformed_text) == ModelOutputKind.MALFORMED
    parsed = parse_edit_blocks(malformed_text)
    assert not parsed.ok
    result = apply_page_edits(BASE_HTML, malformed_text)
    assert not result.ok
    assert result.html is None


# ---------------------------------------------------------------------------
# 2. 单块/多块精确替换、顺序依赖、代码块包裹
# ---------------------------------------------------------------------------


def test_single_block_exact_replace():
    raw_edit = (
        "<<<<<<< SEARCH\n"
        "    <h1>2025 年度产品报告</h1>\n"
        "=======\n"
        "    <h1>2026 年度产品报告</h1>\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(BASE_HTML, raw_edit)
    assert result.ok
    assert result.html is not None
    assert result.blocks_total == 1
    assert result.applied_blocks == (AppliedBlockDetail(index=1, strategy="exact"),)
    assert result.failed_block_index is None
    assert result.error_code is None
    assert "<h1>2026 年度产品报告</h1>" in result.html
    assert "<h1>2025 年度产品报告</h1>" not in result.html


def test_multiple_blocks_with_prose_and_sequential_dependency():
    """多块包裹在不同围栏中并穿插说明文字；且第 2 块可以基于第 1 块演进后的 HTML 继续修改。"""
    raw_edit = """1. 先修改样式与主标题区域：
```html
<<<<<<< SEARCH
    .hero { color: #111827; background: #ffffff; }
=======
    .hero { color: #0f172a; background: #f8fafc; }
>>>>>>> REPLACE

<<<<<<< SEARCH
    <h1>2025 年度产品报告</h1>
    <p class="subtitle">稳健增长与技术突破</p>
=======
    <h1>2026 年度战略报告</h1>
    <p class="subtitle">AI 原生产品矩阵</p>
>>>>>>> REPLACE
```

2. 在刚更新的新副标题后补充徽章：
~~~diff
<<<<<<< SEARCH
    <p class="subtitle">AI 原生产品矩阵</p>
=======
    <p class="subtitle">AI 原生产品矩阵</p>
    <span class="badge">NEW</span>
>>>>>>> REPLACE
~~~
"""
    result = apply_page_edits(BASE_HTML, raw_edit)
    assert result.ok
    assert result.html is not None
    assert result.blocks_total == 3
    assert [item.strategy for item in result.applied_blocks] == ["exact", "exact", "exact"]
    assert "background: #f8fafc;" in result.html
    assert "<h1>2026 年度战略报告</h1>" in result.html
    assert '<span class="badge">NEW</span>' in result.html


def test_bare_edit_block_containing_markdown_backticks_inside_script():
    """裸输出编辑块时，即便被编辑的 <script> 内容中含 ``` 字符串也不应被误判截断。"""
    html_with_backticks = (
        "<!doctype html>\n"
        "<html>\n"
        "<body>\n"
        "<script>\n"
        "const sample = `\n"
        "```js\n"
        'console.log("old");\n'
        "```\n"
        "`;\n"
        "</script>\n"
        "</body>\n"
        "</html>"
    )
    raw_edit = (
        "<<<<<<< SEARCH\n"
        "```js\n"
        'console.log("old");\n'
        "```\n"
        "=======\n"
        "```js\n"
        'console.log("new");\n'
        "```\n"
        ">>>>>>> REPLACE"
    )
    assert classify_model_output(raw_edit) == ModelOutputKind.EDIT_BLOCKS
    result = apply_page_edits(html_with_backticks, raw_edit)
    assert result.ok
    assert result.html is not None
    assert 'console.log("new");' in result.html


# ---------------------------------------------------------------------------
# 3. CRLF 换行兼容与归一化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_sep", ["\n", "\r\n"])
@pytest.mark.parametrize("edit_sep", ["\n", "\r\n"])
def test_crlf_and_lf_interoperability(base_sep: str, edit_sep: str):
    base_lines = [
        "<!doctype html>",
        "<html>",
        "<body>",
        "  <div class='box'>",
        "    <p>第一行</p>",
        "    <p>第二行</p>",
        "  </div>",
        "</body>",
        "</html>",
    ]
    edit_lines = [
        "```html",
        "<<<<<<< SEARCH",
        "    <p>第一行</p>",
        "    <p>第二行</p>",
        "=======",
        "    <p>已更新第一行</p>",
        "    <p>已更新第二行</p>",
        ">>>>>>> REPLACE",
        "```",
    ]
    base_text = base_sep.join(base_lines)
    edit_text = edit_sep.join(edit_lines)

    assert classify_model_output(edit_text) == ModelOutputKind.EDIT_BLOCKS
    result = apply_page_edits(base_text, edit_text)
    assert result.ok
    assert result.html is not None
    assert "\r" not in result.html
    assert "<p>已更新第一行</p>\n    <p>已更新第二行</p>" in result.html


def test_normalize_newlines_handles_lone_cr():
    assert normalize_newlines("a\r\nb\rc\n") == "a\nb\nc\n"


# ---------------------------------------------------------------------------
# 4. 行首尾空白（缩进）容错匹配与删除块
# ---------------------------------------------------------------------------


def test_line_stripped_fallback_when_indentation_differs():
    """精确匹配因缩进/行尾空格不命中时，按每行 strip() 唯一命中并替换对应原始行区间。"""
    raw_edit = (
        "<<<<<<< SEARCH\n"
        "\t\t<section class=\"card\" id=\"summary\">   \n"
        "        <h2>核心指标</h2>\n"
        "        <p>营收同比增长 32%</p>\n"
        "        </section>\n"
        "=======\n"
        "    <section class=\"card\" id=\"summary\">\n"
        "      <h2>核心经营指标</h2>\n"
        "      <p>营收同比增长 48%</p>\n"
        "    </section>\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(BASE_HTML, raw_edit)
    assert result.ok
    assert result.html is not None
    assert result.applied_blocks == (AppliedBlockDetail(index=1, strategy="line_stripped"),)
    assert "<h2>核心经营指标</h2>" in result.html
    assert "<p>营收同比增长 48%</p>" in result.html
    assert "<p>营收同比增长 32%</p>" not in result.html


def test_empty_replace_deletes_matched_lines():
    """REPLACE 为空时允许删除目标片段（精确匹配与行首尾空白容错匹配均支持）。"""
    raw_edit = (
        "<<<<<<< SEARCH\n"
        '        <p class="subtitle">稳健增长与技术突破</p>\n'
        "=======\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(BASE_HTML, raw_edit)
    assert result.ok
    assert result.html is not None
    assert result.applied_blocks == (AppliedBlockDetail(index=1, strategy="line_stripped"),)
    assert "稳健增长与技术突破" not in result.html
    assert "<h1>2025 年度产品报告</h1>" in result.html


# ---------------------------------------------------------------------------
# 5. 空 SEARCH、重复歧义拒绝、原子回滚与结果文档校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "empty_search_edit",
    [
        "<<<<<<< SEARCH\n=======\n<div>无锚点插入</div>\n>>>>>>> REPLACE",
        "<<<<<<< SEARCH\n   \n\t  \n=======\n<div>空白锚点插入</div>\n>>>>>>> REPLACE",
    ],
)
def test_empty_search_rejected(empty_search_edit: str):
    # 语法结构上是闭合编辑块，分类为 EDIT_BLOCKS，在应用阶段因空 SEARCH 被拒绝
    assert classify_model_output(empty_search_edit) == ModelOutputKind.EDIT_BLOCKS
    result = apply_page_edits(BASE_HTML, empty_search_edit)
    assert not result.ok
    assert result.html is None
    assert result.failed_block_index == 1
    assert result.error_code == "empty_search"
    assert "第 1 个编辑块" in (result.error_message or "")


def test_ambiguous_exact_match_rejected_immediately():
    html = """<!doctype html>
<html>
<body>
  <div class="item"><span>相同文案</span></div>
  <div class="item"><span>相同文案</span></div>
</body>
</html>"""
    raw_edit = (
        "<<<<<<< SEARCH\n"
        "<span>相同文案</span>\n"
        "=======\n"
        "<span>新文案</span>\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(html, raw_edit)
    assert not result.ok
    assert result.html is None
    assert result.failed_block_index == 1
    assert result.error_code == "ambiguous_exact_match"
    assert "2 处" in (result.error_message or "")


def test_ambiguous_overlapping_exact_match_rejected():
    html = "<!doctype html><html><body>aaaa</body></html>"
    raw_edit = "<<<<<<< SEARCH\naa\n=======\nbb\n>>>>>>> REPLACE"
    result = apply_page_edits(html, raw_edit)
    assert not result.ok
    assert result.html is None
    assert result.failed_block_index == 1
    assert result.error_code == "ambiguous_exact_match"


def test_ambiguous_line_stripped_match_rejected():
    """精确匹配 0 处（缩进均不同），但按行 strip 后命中 2 处时，同样必须判歧义拒绝。"""
    html = """<!doctype html>
<html>
<body>
  <section class="block">
    <p class="metric">100%</p>
  </section>
  <section class="block">
      <p class="metric">100%</p>
  </section>
</body>
</html>"""
    raw_edit = (
        "<<<<<<< SEARCH\n"
        '<section class="block">\n'
        '        <p class="metric">100%</p>\n'
        "</section>\n"
        "=======\n"
        '<section class="block">\n'
        '  <p class="metric">99.9%</p>\n'
        "</section>\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(html, raw_edit)
    assert not result.ok
    assert result.html is None
    assert result.failed_block_index == 1
    assert result.error_code == "ambiguous_stripped_match"
    assert "2 处" in (result.error_message or "")


def test_atomic_rollback_when_later_block_fails():
    """第 1 块成功、第 2 块找不到原文时，整次应用必须原子作废，不返回半改 HTML。"""
    raw_edit = (
        "<<<<<<< SEARCH\n"
        "    <h1>2025 年度产品报告</h1>\n"
        "=======\n"
        "    <h1>已被第一块改掉的标题</h1>\n"
        ">>>>>>> REPLACE\n\n"
        "<<<<<<< SEARCH\n"
        "    <p>这段原文在页面里根本不存在</p>\n"
        "=======\n"
        "    <p>新内容</p>\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(BASE_HTML, raw_edit)
    assert not result.ok
    assert result.html is None
    assert result.blocks_total == 2
    assert result.applied_blocks == ()
    assert result.failed_block_index == 2
    assert result.error_code == "search_not_found"
    assert "第 2 个编辑块" in (result.error_message or "")


def test_result_validation_rejects_output_without_html_tag():
    """若编辑块把整个文档替换成了不含 <html 的片段或空内容，应用校验应拒绝。"""
    minimal = "<!doctype html>\n<html><body><p>hi</p></body></html>"
    raw_edit = (
        "<<<<<<< SEARCH\n"
        "<!doctype html>\n"
        "<html><body><p>hi</p></body></html>\n"
        "=======\n"
        "<div>没有 html 根元素了</div>\n"
        ">>>>>>> REPLACE"
    )
    result = apply_page_edits(minimal, raw_edit)
    assert not result.ok
    assert result.html is None
    assert result.error_code == "invalid_result_html"


def test_apply_edit_blocks_rejects_empty_sequence():
    result = apply_edit_blocks(BASE_HTML, [])
    assert not result.ok
    assert result.html is None
    assert result.error_code == "no_edit_blocks"


def test_direct_edit_blocks_api_preserves_custom_or_sequential_index():
    blocks = [
        EditBlock(
            index=1,
            search="    <h1>2025 年度产品报告</h1>",
            replace="    <h1>2026 年度产品报告</h1>",
        )
    ]
    result = apply_edit_blocks(BASE_HTML, blocks)
    assert result.ok
    assert result.html is not None
    assert "<h1>2026 年度产品报告</h1>" in result.html
