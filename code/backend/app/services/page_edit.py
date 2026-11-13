"""局部编辑（SEARCH/REPLACE 编辑块）解析、分类与原子应用模块。

用于在续写修改与渲染修复轮次中，区分模型是输出了局部编辑块、完整 HTML 还是畸形残缺内容，
并将合法的编辑块按顺序、唯一且原子地应用到基准 HTML 文档上。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import re
from typing import Literal

__all__ = [
    "SEARCH_MARKER",
    "DIVIDER_MARKER",
    "REPLACE_MARKER",
    "ModelOutputKind",
    "MatchStrategy",
    "EditBlock",
    "EditParseResult",
    "AppliedBlockDetail",
    "PageEditResult",
    "normalize_newlines",
    "classify_model_output",
    "parse_edit_blocks",
    "apply_edit_blocks",
    "apply_page_edits",
]

SEARCH_MARKER = "<<<<<<< SEARCH"
DIVIDER_MARKER = "======="
REPLACE_MARKER = ">>>>>>> REPLACE"

_SEARCH_START_RE = re.compile(r"^[ \t]*<<<<<<<[ \t]+SEARCH[ \t]*$")
_DIVIDER_RE = re.compile(r"^[ \t]*=======[ \t]*$")
_REPLACE_END_RE = re.compile(r"^[ \t]*>>>>>>>[ \t]+REPLACE[ \t]*$")

# 非标准形态的可疑编辑块标记：用于拦截粘连同行、大小写手滑、尖括号数量偏差或半截冲突标记。
_INLINE_MARKER_HINT_RE = re.compile(
    r"<{4,}[ \t]*SEARCH\b|>{4,}[ \t]*REPLACE\b",
    re.IGNORECASE,
)
# 独占一行的半截/Git 冲突头尾（如 "<<<<<<<"、"<<<<<<< HEAD"、">>>>>>>"），
# 但排除 "<<<<<<< WELCOME >>>>>>>" 这类同行左右对称的普通装饰文案。
_BARE_OPEN_CONFLICT_RE = re.compile(r"^[ \t]*<{5,}(?:[ \t]+(?!.*>{5,})[A-Za-z0-9_-]+)?[ \t]*$")
_BARE_CLOSE_CONFLICT_RE = re.compile(r"^[ \t]*>{5,}(?:[ \t]+[A-Za-z0-9_-]+)?[ \t]*$")

_BACKTICK_FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,})([^`]*)$")
_TILDE_FENCE_OPEN_RE = re.compile(r"^[ \t]*(~{3,})(.*)$")

MatchStrategy = Literal["exact", "line_stripped"]


class ModelOutputKind(str, Enum):
    """模型输出内容的顶层形态分类。"""

    EDIT_BLOCKS = "edit_blocks"
    FULL_HTML = "full_html"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class EditBlock:
    """单个 SEARCH/REPLACE 编辑块（序号从 1 开始）。"""

    index: int
    search: str
    replace: str


@dataclass(frozen=True)
class EditParseResult:
    """编辑块解析结果。"""

    ok: bool
    blocks: tuple[EditBlock, ...] = ()
    failed_block_index: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class AppliedBlockDetail:
    """单个成功应用的编辑块命中详情。"""

    index: int
    strategy: MatchStrategy


@dataclass(frozen=True)
class PageEditResult:
    """将编辑块应用到基准 HTML 后的原子结果。"""

    ok: bool
    html: str | None = None
    blocks_total: int = 0
    applied_blocks: tuple[AppliedBlockDetail, ...] = ()
    failed_block_index: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class _ScanOutcome:
    has_edit_intent: bool
    parse_result: EditParseResult


class _ScanState(Enum):
    OUTSIDE = "outside"
    IN_SEARCH = "in_search"
    IN_REPLACE = "in_replace"


def normalize_newlines(text: str) -> str:
    """统一将 CRLF (\\r\\n) 与孤立 CR (\\r) 归一化为 LF (\\n)。"""
    if "\r" not in text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def classify_model_output(raw_text: str) -> ModelOutputKind:
    """判断模型输出属于合法编辑块、完整 HTML 还是格式异常。

    规则：
    1. 只要输出包含编辑块标记特征：全部配对合法且至少一块 -> EDIT_BLOCKS；存在未闭合/畸形标记 -> MALFORMED。
       即使编辑块内部修改了 <!doctype html> 或 <html> 标签，也按 EDIT_BLOCKS 处理。
    2. 不含任何编辑块标记时：包含 <!doctype 或 <html（忽略大小写） -> FULL_HTML；否则 -> MALFORMED。
    """
    if not raw_text or not raw_text.strip():
        return ModelOutputKind.MALFORMED

    normalized = normalize_newlines(raw_text)
    outcome = _scan_edit_blocks(normalized)
    if outcome.has_edit_intent:
        return ModelOutputKind.EDIT_BLOCKS if outcome.parse_result.ok else ModelOutputKind.MALFORMED

    if _looks_like_full_html(normalized):
        return ModelOutputKind.FULL_HTML

    return ModelOutputKind.MALFORMED


def parse_edit_blocks(raw_text: str) -> EditParseResult:
    """解析模型输出中的全部 SEARCH/REPLACE 编辑块。

    支持裸输出、单个或多个 Markdown 代码围栏（```html / ```diff / ``` / ~~~）包裹以及块间说明文字。
    任何未闭合、嵌套、缺失分隔符或畸形的标记均判为解析失败。
    """
    if not raw_text or not raw_text.strip():
        return EditParseResult(
            ok=False,
            error_code="empty_output",
            error_message="模型输出为空，未找到编辑块",
        )

    normalized = normalize_newlines(raw_text)
    return _scan_edit_blocks(normalized).parse_result


def apply_edit_blocks(base_html: str, blocks: Sequence[EditBlock]) -> PageEditResult:
    """按顺序将已解析的编辑块原子应用到基准 HTML 上。

    任一编辑块为空 SEARCH、命中 0 处或命中多处（歧义），整次编辑立即作废（html=None）。
    全部应用成功后校验结果仍为包含 `<html` 的非空文档。
    """
    blocks_tuple = tuple(blocks)
    total = len(blocks_tuple)
    if total == 0:
        return PageEditResult(
            ok=False,
            blocks_total=0,
            error_code="no_edit_blocks",
            error_message="没有任何可应用的编辑块",
        )

    working_html = normalize_newlines(base_html)
    applied: list[AppliedBlockDetail] = []

    for offset, block in enumerate(blocks_tuple, start=1):
        block_index = block.index if block.index > 0 else offset
        search_text = normalize_newlines(block.search)
        replace_text = normalize_newlines(block.replace)

        if not search_text or not search_text.strip():
            return PageEditResult(
                ok=False,
                blocks_total=total,
                failed_block_index=block_index,
                error_code="empty_search",
                error_message=f"第 {block_index} 个编辑块的 SEARCH 内容为空，局部编辑不允许无锚点插入",
            )

        exact_positions = _find_exact_matches(working_html, search_text)
        exact_count = len(exact_positions)
        if exact_count > 1:
            return PageEditResult(
                ok=False,
                blocks_total=total,
                failed_block_index=block_index,
                error_code="ambiguous_exact_match",
                error_message=f"第 {block_index} 个编辑块在页面中精确命中 {exact_count} 处，存在歧义",
            )

        if exact_count == 1:
            pos = exact_positions[0]
            working_html = working_html[:pos] + replace_text + working_html[pos + len(search_text) :]
            applied.append(AppliedBlockDetail(index=block_index, strategy="exact"))
            continue

        stripped_updated, stripped_count = _apply_line_stripped_match(
            working_html,
            search_text,
            replace_text,
        )
        if stripped_count > 1:
            return PageEditResult(
                ok=False,
                blocks_total=total,
                failed_block_index=block_index,
                error_code="ambiguous_stripped_match",
                error_message=(
                    f"第 {block_index} 个编辑块按行忽略首尾空白后命中 {stripped_count} 处，存在歧义"
                ),
            )

        if stripped_count == 0 or stripped_updated is None:
            return PageEditResult(
                ok=False,
                blocks_total=total,
                failed_block_index=block_index,
                error_code="search_not_found",
                error_message=f"第 {block_index} 个编辑块在页面中未找到匹配的原文片段",
            )

        working_html = stripped_updated
        applied.append(AppliedBlockDetail(index=block_index, strategy="line_stripped"))

    if not working_html.strip() or "<html" not in working_html.lower():
        return PageEditResult(
            ok=False,
            blocks_total=total,
            error_code="invalid_result_html",
            error_message="应用编辑块后的结果不是包含 <html 的有效非空 HTML 文档",
        )

    return PageEditResult(
        ok=True,
        html=working_html,
        blocks_total=total,
        applied_blocks=tuple(applied),
    )


def apply_page_edits(base_html: str, raw_text: str) -> PageEditResult:
    """解析模型原始编辑输出并原子应用到基准 HTML 上。"""
    parsed = parse_edit_blocks(raw_text)
    if not parsed.ok:
        return PageEditResult(
            ok=False,
            blocks_total=len(parsed.blocks),
            failed_block_index=parsed.failed_block_index,
            error_code=parsed.error_code,
            error_message=parsed.error_message,
        )
    return apply_edit_blocks(base_html, parsed.blocks)


def _scan_edit_blocks(normalized_text: str) -> _ScanOutcome:
    lines = normalized_text.split("\n")
    state = _ScanState.OUTSIDE
    has_edit_intent = False

    blocks: list[EditBlock] = []
    current_block_index = 0
    search_lines: list[str] = []
    replace_lines: list[str] = []

    active_fence: tuple[str, int] | None = None
    block_fence: tuple[str, int] | None = None
    stray_divider_lines: list[int] = []

    for line_no, line in enumerate(lines, start=1):
        is_start = _SEARCH_START_RE.match(line) is not None
        is_divider = _DIVIDER_RE.match(line) is not None
        is_end = _REPLACE_END_RE.match(line) is not None

        if not (is_start or is_divider or is_end) and _is_malformed_marker_line(line):
            return _ScanOutcome(
                has_edit_intent=True,
                parse_result=EditParseResult(
                    ok=False,
                    blocks=tuple(blocks),
                    failed_block_index=current_block_index if state != _ScanState.OUTSIDE else None,
                    error_code="malformed_markers",
                    error_message=f"第 {line_no} 行包含畸形或未独立成行的编辑块标记",
                ),
            )

        if state == _ScanState.OUTSIDE:
            if is_start:
                has_edit_intent = True
                current_block_index = len(blocks) + 1
                search_lines = []
                replace_lines = []
                block_fence = active_fence
                state = _ScanState.IN_SEARCH
                continue

            if is_end:
                return _ScanOutcome(
                    has_edit_intent=True,
                    parse_result=EditParseResult(
                        ok=False,
                        blocks=tuple(blocks),
                        error_code="malformed_markers",
                        error_message=f"第 {line_no} 行出现孤立的 '{REPLACE_MARKER}'，缺少起始标记",
                    ),
                )

            if is_divider:
                stray_divider_lines.append(line_no)
                continue

            if active_fence is None:
                opened = _match_opening_fence(line)
                if opened is not None:
                    active_fence = opened
            elif _is_closing_fence(line, active_fence[0], active_fence[1]):
                active_fence = None
            continue

        if state == _ScanState.IN_SEARCH:
            if is_divider:
                state = _ScanState.IN_REPLACE
                continue

            if is_start:
                return _ScanOutcome(
                    has_edit_intent=True,
                    parse_result=EditParseResult(
                        ok=False,
                        blocks=tuple(blocks),
                        failed_block_index=current_block_index,
                        error_code="malformed_markers",
                        error_message=(
                            f"第 {current_block_index} 个编辑块尚未闭合，第 {line_no} 行又出现了 '{SEARCH_MARKER}'"
                        ),
                    ),
                )

            if is_end:
                return _ScanOutcome(
                    has_edit_intent=True,
                    parse_result=EditParseResult(
                        ok=False,
                        blocks=tuple(blocks),
                        failed_block_index=current_block_index,
                        error_code="malformed_markers",
                        error_message=(
                            f"第 {current_block_index} 个编辑块缺少 '{DIVIDER_MARKER}' 分隔标记"
                        ),
                    ),
                )

            if block_fence is not None and _is_closing_fence(line, block_fence[0], block_fence[1]):
                return _ScanOutcome(
                    has_edit_intent=True,
                    parse_result=EditParseResult(
                        ok=False,
                        blocks=tuple(blocks),
                        failed_block_index=current_block_index,
                        error_code="malformed_markers",
                        error_message=(
                            f"第 {current_block_index} 个编辑块在代码块结束前未闭合（缺少 '{DIVIDER_MARKER}' 与 '{REPLACE_MARKER}'）"
                        ),
                    ),
                )

            search_lines.append(line)
            continue

        # state == _ScanState.IN_REPLACE
        if is_end:
            blocks.append(
                EditBlock(
                    index=current_block_index,
                    search="\n".join(search_lines),
                    replace="\n".join(replace_lines),
                )
            )
            search_lines = []
            replace_lines = []
            block_fence = None
            state = _ScanState.OUTSIDE
            continue

        if is_start:
            return _ScanOutcome(
                has_edit_intent=True,
                parse_result=EditParseResult(
                    ok=False,
                    blocks=tuple(blocks),
                    failed_block_index=current_block_index,
                    error_code="malformed_markers",
                    error_message=(
                        f"第 {current_block_index} 个编辑块缺少 '{REPLACE_MARKER}' 结束标记，"
                        f"第 {line_no} 行已开始下一个编辑块"
                    ),
                ),
            )

        if is_divider:
            return _ScanOutcome(
                has_edit_intent=True,
                parse_result=EditParseResult(
                    ok=False,
                    blocks=tuple(blocks),
                    failed_block_index=current_block_index,
                    error_code="malformed_markers",
                    error_message=(
                        f"第 {current_block_index} 个编辑块内重复出现了 '{DIVIDER_MARKER}' 分隔标记"
                    ),
                ),
            )

        if block_fence is not None and _is_closing_fence(line, block_fence[0], block_fence[1]):
            return _ScanOutcome(
                has_edit_intent=True,
                parse_result=EditParseResult(
                    ok=False,
                    blocks=tuple(blocks),
                    failed_block_index=current_block_index,
                    error_code="malformed_markers",
                    error_message=(
                        f"第 {current_block_index} 个编辑块在代码块结束前缺少 '{REPLACE_MARKER}' 结束标记"
                    ),
                ),
            )

        replace_lines.append(line)

    if state == _ScanState.IN_SEARCH:
        return _ScanOutcome(
            has_edit_intent=True,
            parse_result=EditParseResult(
                ok=False,
                blocks=tuple(blocks),
                failed_block_index=current_block_index,
                error_code="malformed_markers",
                error_message=(
                    f"第 {current_block_index} 个编辑块未闭合，缺少 '{DIVIDER_MARKER}' 与 '{REPLACE_MARKER}'"
                ),
            ),
        )

    if state == _ScanState.IN_REPLACE:
        return _ScanOutcome(
            has_edit_intent=True,
            parse_result=EditParseResult(
                ok=False,
                blocks=tuple(blocks),
                failed_block_index=current_block_index,
                error_code="malformed_markers",
                error_message=f"第 {current_block_index} 个编辑块未闭合，缺少 '{REPLACE_MARKER}'",
            ),
        )

    if has_edit_intent and stray_divider_lines:
        first_line = stray_divider_lines[0]
        return _ScanOutcome(
            has_edit_intent=True,
            parse_result=EditParseResult(
                ok=False,
                blocks=tuple(blocks),
                error_code="malformed_markers",
                error_message=f"第 {first_line} 行存在未配对的 '{DIVIDER_MARKER}' 分隔标记",
            ),
        )

    if not blocks:
        return _ScanOutcome(
            has_edit_intent=False,
            parse_result=EditParseResult(
                ok=False,
                error_code="no_edit_blocks",
                error_message="未解析到任何 SEARCH/REPLACE 编辑块",
            ),
        )

    return _ScanOutcome(
        has_edit_intent=True,
        parse_result=EditParseResult(
            ok=True,
            blocks=tuple(blocks),
        ),
    )


def _is_malformed_marker_line(line: str) -> bool:
    if _INLINE_MARKER_HINT_RE.search(line):
        return True
    if _BARE_OPEN_CONFLICT_RE.match(line):
        return True
    if _BARE_CLOSE_CONFLICT_RE.match(line):
        return True
    return False


def _match_opening_fence(line: str) -> tuple[str, int] | None:
    backtick_match = _BACKTICK_FENCE_OPEN_RE.match(line)
    if backtick_match is not None:
        return ("`", len(backtick_match.group(1)))
    tilde_match = _TILDE_FENCE_OPEN_RE.match(line)
    if tilde_match is not None:
        return ("~", len(tilde_match.group(1)))
    return None


def _is_closing_fence(line: str, fence_char: str, min_len: int) -> bool:
    stripped = line.strip()
    return len(stripped) >= min_len and all(ch == fence_char for ch in stripped)


def _looks_like_full_html(normalized_text: str) -> bool:
    lowered = normalized_text.lower()
    return "<!doctype" in lowered or "<html" in lowered


def _find_exact_matches(haystack: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _apply_line_stripped_match(
    current_html: str,
    search: str,
    replace: str,
) -> tuple[str | None, int]:
    doc_lines = current_html.split("\n")
    search_lines = search.split("\n")
    window_len = len(search_lines)
    if window_len == 0 or window_len > len(doc_lines):
        return None, 0

    doc_stripped = [line.strip() for line in doc_lines]
    search_stripped = [line.strip() for line in search_lines]

    matches = [
        start
        for start in range(len(doc_lines) - window_len + 1)
        if doc_stripped[start : start + window_len] == search_stripped
    ]
    if len(matches) != 1:
        return None, len(matches)

    match_start = matches[0]
    replacement_lines = [] if replace == "" else replace.split("\n")
    updated_lines = doc_lines[:match_start] + replacement_lines + doc_lines[match_start + window_len :]
    return "\n".join(updated_lines), 1
