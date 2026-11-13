"""生成页发布前无头浏览器渲染检查的结果类型与转换工具。

提供三套下游消费视图：
1. `to_dict()` / `from_dict()`：存入 `page_versions.render_check` JSONB 列，完整保留状态、耗时、内存与结构化问题。
2. `to_render_issue()` / `render_issue_from_stored()`：面向 `completed` 事件与会话详情接口下发给前端的精简提示。
3. `format_for_repair_prompt()` / `format_issues_for_model()`：面向模型自动修复轮的结构化诊断提示词。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

RenderCheckStatus = Literal["passed", "issues_found", "skipped"]
RenderIssueKind = Literal["js_error", "blank_page", "hang"]

MAIN_DOCUMENT_URL = "http://starpage.invalid/"
_MAIN_URL_LINE_RE = re.compile(r"http://starpage\.invalid/(?:[^\s:)]*)?:(\d+)(?::(\d+))?")

_KIND_LABELS: dict[str, str] = {
    "js_error": "脚本运行出错",
    "blank_page": "首屏疑似白屏",
    "hang": "脚本长时间无响应",
}


def _truncate_single_line(text: str, max_chars: int = 140) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _truncate_block(text: str, max_chars: int = 1000) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


@dataclass(frozen=True)
class RenderIssue:
    """单条渲染检查问题。"""

    kind: RenderIssueKind
    summary: str
    detail: str = ""
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "summary": self.summary,
            "detail": self.detail,
        }
        if self.line is not None:
            payload["line"] = int(self.line)
        if self.column is not None:
            payload["column"] = int(self.column)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RenderIssue:
        raw_kind = str(data.get("kind") or "js_error")
        kind: RenderIssueKind = (
            raw_kind if raw_kind in {"js_error", "blank_page", "hang"} else "js_error"  # type: ignore[assignment]
        )
        raw_line = data.get("line")
        raw_col = data.get("column")
        return cls(
            kind=kind,
            summary=str(data.get("summary") or _KIND_LABELS.get(kind, "页面显示异常")),
            detail=str(data.get("detail") or ""),
            line=int(raw_line) if isinstance(raw_line, int) or (isinstance(raw_line, str) and raw_line.isdigit()) else None,
            column=int(raw_col) if isinstance(raw_col, int) or (isinstance(raw_col, str) and raw_col.isdigit()) else None,
        )


def build_js_error_issue(name: str, message: str, stack: str = "") -> RenderIssue:
    """将浏览器捕获的未处理异常解析为带行号与可读摘要的 RenderIssue。"""
    clean_name = (name or "").strip()
    first_line = (message or "").strip().splitlines()[0].strip() if (message or "").strip() else ""

    if first_line:
        if clean_name and clean_name != "Error" and not first_line.lower().startswith(clean_name.lower()):
            headline = f"{clean_name}: {first_line}"
        else:
            headline = first_line
    elif clean_name:
        headline = clean_name
    else:
        headline = "未捕获的 JavaScript 异常"

    line: int | None = None
    column: int | None = None
    for candidate in (stack, message):
        if not candidate:
            continue
        match = _MAIN_URL_URL_SEARCH(candidate)
        if match is not None:
            line, column = match
            break

    short_headline = _truncate_single_line(headline, 120)
    if line is not None:
        summary = f"脚本运行出错（第 {line} 行）：{short_headline}"
    else:
        summary = f"脚本运行出错：{short_headline}"

    raw_detail = stack.strip() if stack and stack.strip() else headline
    if headline and headline not in raw_detail:
        raw_detail = f"{headline}\n{raw_detail}"
    detail = _truncate_block(raw_detail, 1000)

    return RenderIssue(
        kind="js_error",
        summary=summary,
        detail=detail,
        line=line,
        column=column,
    )


def _MAIN_URL_URL_SEARCH(text: str) -> tuple[int, int | None] | None:
    match = _MAIN_URL_LINE_RE.search(text)
    if match is None:
        return None
    line = int(match.group(1))
    col = int(match.group(2)) if match.group(2) is not None else None
    return line, col


def build_blank_page_issue(detail: str = "") -> RenderIssue:
    """构造白屏问题记录。"""
    return RenderIssue(
        kind="blank_page",
        summary="页面首屏未检测到可见内容（疑似白屏）",
        detail=_truncate_block(detail or "首屏视口内既无可见文字，也无非零尺寸的可见图形或媒体元素", 600),
    )


def build_hang_issue(timeout_seconds: float, detail: str = "") -> RenderIssue:
    """构造脚本卡死/加载无响应问题记录。"""
    seconds_label = f"{timeout_seconds:g}"
    fallback_detail = f"页面在 {seconds_label} 秒内未完成加载或主线程持续阻塞无响应"
    return RenderIssue(
        kind="hang",
        summary="页面脚本长时间无响应",
        detail=_truncate_block(detail or fallback_detail, 600),
    )


@dataclass
class RenderCheckResult:
    """单次页面无头渲染检查结果。"""

    status: RenderCheckStatus
    issues: list[RenderIssue] = field(default_factory=list)
    skip_reason: str | None = None
    duration_ms: float = 0.0
    rss_mb: float = 0.0
    repaired: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def has_issues(self) -> bool:
        return self.status == "issues_found" and bool(self.issues)

    def summary_text(self) -> str:
        """面向前端展示的顶层简明摘要；无问题时返回空字符串。"""
        if not self.has_issues:
            return ""
        seen_labels: list[str] = []
        for issue in self.issues:
            label = _KIND_LABELS.get(issue.kind, "页面显示异常")
            if label not in seen_labels:
                seen_labels.append(label)
        joined = "、".join(seen_labels)
        return f"页面可能有显示问题（{joined}）"

    def to_dict(
        self,
        *,
        repaired: bool | None = None,
        mode: str | None = None,
        expose_issue: bool | None = None,
    ) -> dict[str, Any]:
        """导出为存入 `page_versions.render_check` JSONB 列的字典。"""
        payload: dict[str, Any] = {
            "status": self.status,
            "issues": [issue.to_dict() for issue in self.issues],
            "repaired": self.repaired if repaired is None else bool(repaired),
            "duration_ms": round(float(self.duration_ms), 1),
            "rss_mb": round(float(self.rss_mb), 1),
        }
        if self.skip_reason:
            payload["skip_reason"] = self.skip_reason
        if mode is not None:
            payload["mode"] = mode
        if expose_issue is not None:
            payload["expose_issue"] = bool(expose_issue)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RenderCheckResult:
        """从数据库 `page_versions.render_check` JSONB 字典还原。"""
        raw_status = str(data.get("status") or "skipped")
        status: RenderCheckStatus = (
            raw_status if raw_status in {"passed", "issues_found", "skipped"} else "skipped"  # type: ignore[assignment]
        )
        raw_issues = data.get("issues") or []
        issues = [RenderIssue.from_dict(item) for item in raw_issues if isinstance(item, Mapping)]
        skip_reason = data.get("skip_reason")
        return cls(
            status=status,
            issues=issues,
            skip_reason=str(skip_reason) if skip_reason else None,
            duration_ms=float(data.get("duration_ms") or 0.0),
            rss_mb=float(data.get("rss_mb") or 0.0),
            repaired=bool(data.get("repaired", False)),
        )

    def to_render_issue(self) -> dict[str, Any] | None:
        """转换为下发前端的 `render_issue` 结构（无问题或跳过时返回 None）。"""
        if not self.has_issues:
            return None
        items: list[str] = []
        for issue in self.issues:
            text = _truncate_single_line(issue.summary, 160)
            if text and text not in items:
                items.append(text)
        return {
            "summary": self.summary_text(),
            "issues": items,
        }

    def format_issues_for_model(self) -> str:
        """将检测到的问题格式化为面向大模型的诊断条目列表。"""
        if not self.has_issues:
            return ""
        blocks: list[str] = []
        for index, issue in enumerate(self.issues, start=1):
            loc = ""
            if issue.line is not None:
                loc = f"（位置：HTML 第 {issue.line} 行" + (
                    f" 第 {issue.column} 列）" if issue.column is not None else "）"
                )
            lines = [f"{index}. [{issue.kind}] {issue.summary}{loc}"]
            if issue.detail and issue.detail != issue.summary:
                indented = "\n".join(f"   {line}" for line in issue.detail.splitlines())
                lines.append(f"   详情：\n{indented}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def format_for_repair_prompt(self, *, prefer_edit_blocks: bool = True) -> str:
        """构造发给模型进行一次自动修复的完整用户提示词。"""
        if not self.has_issues:
            return ""

        kinds = {issue.kind for issue in self.issues}
        hints: list[str] = []
        if "js_error" in kinds:
            hints.append(
                "- 修复脚本报错：检查变量与函数声明顺序、DOM 节点判空、语法拼写，以及沙箱限制"
                "（线上启用严格 CSP sandbox：禁止读写 localStorage/sessionStorage/cookie，"
                "禁止 fetch/XHR 等任何网络请求，仅允许可信白名单 CDN 脚本）。"
            )
        if "blank_page" in kinds:
            hints.append(
                "- 修复首屏白屏：确保页面在 1200×800 视口加载后无需滚动即可直接看到主体内容；"
                "不要把主体内容保持在 opacity: 0、visibility: hidden、display: none 或零高度折叠容器中；"
                "若使用了入场动画或滚动显现，请保证首屏立即显现或在脚本未执行时依然可见。"
            )
        if "hang" in kinds:
            hints.append(
                "- 修复脚本无响应：排查并移除 while/for 死循环、未更新终止条件的循环、无限递归或同步重计算阻塞。"
            )

        output_rule = (
            "若只需局部修改即可修复，请优先输出 `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` 编辑块精确替换问题代码；"
            "若涉及较多结构调整，也可直接输出修改后的完整 HTML 文档。不要输出解释文字或 Markdown 代码块。"
            if prefer_edit_blocks
            else "请在保持原有内容、结构与视觉设计意图的基础上修复上述问题，直接输出修改后的完整 HTML 文档，不要输出解释或 Markdown 代码块。"
        )

        sections = [
            "上一版生成的 HTML 页面在无头浏览器（视口 1200×800，启用与线上一致的 CSP 沙箱）渲染检查时发现以下问题：",
            self.format_issues_for_model(),
            "请针对以上问题进行修复：\n" + "\n".join(hints),
            output_rule,
        ]
        return "\n\n".join(section for section in sections if section)

    # 兼容简短调用名
    to_repair_prompt = format_for_repair_prompt


def render_issue_from_stored(stored: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """从数据库存储的 `page_versions.render_check` JSONB 直接提取前端 `render_issue`。

    若记录来自 `observe` 观察模式（`mode == "observe"` 或 `expose_issue is False`），
    仅用于后台质量分析，不向前端下发显示问题标签。
    """
    if not stored or not isinstance(stored, Mapping):
        return None
    if stored.get("expose_issue") is False:
        return None
    if str(stored.get("mode") or "").strip().lower() == "observe":
        return None
    return RenderCheckResult.from_dict(stored).to_render_issue()
