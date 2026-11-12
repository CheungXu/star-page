"""PPTX：展开组合形状，按版面位置还原阅读顺序，取表格、图表数据、备注、配图和主题色。"""

from __future__ import annotations

import io
import re
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from app.services.document_parser.images import normalize_image
from app.services.document_parser.limits import DOC_MAX_IMAGES, PPTX_MAX_SLIDES
from app.services.document_parser.text_utils import markdown_table, normalize_text
from app.services.document_parser.types import IMAGE_PLACEHOLDER, ParsedDocument, ParsedImage

LOW_TEXT_SLIDE_CHARS = 40
CORE_IMAGE_RATIO = 0.25
# 同一「行」的纵向容差：幻灯片高度的 5%。
ROW_BUCKET_RATIO = 0.05
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def parse_pptx(filename: str, source: bytes | Path, content_sha256: str) -> ParsedDocument:
    presentation = Presentation(str(source) if isinstance(source, Path) else io.BytesIO(source))
    slide_width = int(presentation.slide_width or 12192000)
    slide_height = int(presentation.slide_height or 6858000)
    slide_area = max(slide_width * slide_height, 1)

    images: list[ParsedImage] = []
    seen: set[str] = set()
    sections: list[str] = []
    theme = _theme_colors(presentation)
    if theme:
        sections.append("主题色：" + "，".join(f"{name} {value}" for name, value in theme))

    for number, slide in enumerate(list(presentation.slides)[:PPTX_MAX_SLIDES], start=1):
        title_shape = slide.shapes.title
        title = (title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else "")
        shapes = _flatten(slide.shapes, slide_height)
        text_parts: list[str] = []
        pictures: list[tuple[object, float]] = []
        for shape in shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                table = markdown_table([[cell.text for cell in row.cells] for row in shape.table.rows])
                if table:
                    text_parts.append(table)
            elif getattr(shape, "has_chart", False) and shape.has_chart:
                chart = _chart_text(shape.chart)
                if chart:
                    text_parts.append(chart)
            elif _is_picture(shape):
                area = max(0, int(shape.width or 0)) * max(0, int(shape.height or 0))
                pictures.append((shape, area / slide_area))
            elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                block = _text_frame(shape.text_frame)
                if block:
                    text_parts.append(block)

        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""

        char_count = len(re.sub(r"\s+", "", title + "".join(text_parts)))
        low_text = char_count < LOW_TEXT_SLIDE_CHARS
        label = f"{filename} 第 {number} 页" + (f"「{title[:30]}」" if title else "")
        for shape, ratio in pictures:
            if len(images) >= DOC_MAX_IMAGES:
                break
            try:
                blob = shape.image.blob
            except Exception:  # noqa: BLE001 - 链接外部图片、EMF 等取不到
                continue
            normalized = normalize_image(blob)
            if normalized is None or normalized.sha256 in seen:
                continue
            seen.add(normalized.sha256)
            images.append(
                ParsedImage(
                    data=normalized.data,
                    content_type=normalized.content_type,
                    extension=normalized.extension,
                    width=normalized.width,
                    height=normalized.height,
                    sha256=normalized.sha256,
                    label=label,
                    area_ratio=min(ratio, 1.0),
                    core=low_text and ratio >= CORE_IMAGE_RATIO,
                )
            )
            text_parts.append(IMAGE_PLACEHOLDER.format(index=len(images) - 1))

        heading = f"## 第 {number} 页" + (f"：{title}" if title else "")
        body = "\n\n".join(part for part in text_parts if part)
        section = heading + (f"\n\n{body}" if body else "")
        if notes:
            section += f"\n\n演讲者备注：{notes}"
        sections.append(section)

    return ParsedDocument(
        filename=filename,
        extension="pptx",
        content_sha256=content_sha256,
        text=normalize_text("\n\n".join(sections)),
        images=images,
    )


def _flatten(shapes, slide_height: int) -> list:
    """组合形状按自身位置参与排序，组内的形状紧跟其后、按组内位置排序。"""
    bucket = max(int(slide_height * ROW_BUCKET_RATIO), 1)

    def key(shape) -> tuple[int, int]:
        return (int(shape.top or 0) // bucket, int(shape.left or 0))

    ordered: list = []
    for shape in sorted(shapes, key=key):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            ordered.extend(_flatten(shape.shapes, slide_height))
        else:
            ordered.append(shape)
    return ordered


def _is_picture(shape) -> bool:
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        return True
    # 图片占位符、带图片填充的占位符也有 image 属性。
    return shape.shape_type == MSO_SHAPE_TYPE.PLACEHOLDER and hasattr(shape, "image")


def _text_frame(frame) -> str:
    lines = []
    for paragraph in frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs).strip() or paragraph.text.strip()
        if not text:
            continue
        level = int(paragraph.level or 0)
        lines.append(("  " * level + "- " + text) if level > 0 or len(frame.paragraphs) > 1 else text)
    return "\n".join(lines)


def _chart_text(chart) -> str:
    try:
        plot = chart.plots[0]
    except (IndexError, AttributeError):
        return ""
    title = ""
    if chart.has_title and chart.chart_title.has_text_frame:
        title = chart.chart_title.text_frame.text.strip()
    categories = [str(item) for item in plot.categories]
    rows: list[list[object]] = [["系列", *categories]]
    for series in plot.series:
        rows.append([series.name, *list(series.values)])
    table = markdown_table(rows)
    return f"图表{('：' + title) if title else ''}\n{table}" if table else ""


def _theme_colors(presentation) -> list[tuple[str, str]]:
    """读母版主题里的配色（accent1-6、深浅底色），给网页配色参考。"""
    try:
        master = presentation.slide_masters[0]
        theme_part = master.part.part_related_by(RT.THEME)
        root = etree.fromstring(theme_part.blob)
    except Exception:  # noqa: BLE001 - 没有主题或结构不标准
        return []
    scheme = root.find(f".//{{{_A_NS}}}clrScheme")
    if scheme is None:
        return []
    colors = []
    for name in ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6"):
        node = scheme.find(f"{{{_A_NS}}}{name}")
        if node is None:
            continue
        srgb = node.find(f"{{{_A_NS}}}srgbClr")
        sys_color = node.find(f"{{{_A_NS}}}sysClr")
        value = srgb.get("val") if srgb is not None else (sys_color.get("lastClr") if sys_color is not None else None)
        if value:
            colors.append((name, f"#{value.upper()}"))
    return colors
