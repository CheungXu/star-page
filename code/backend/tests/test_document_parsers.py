"""分格式解析与预检：阅读顺序、表格、配图、少字大图快照，以及各项安全上限。"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_fixtures import build_pdf, jpeg_bytes, png_bytes, zip_bomb_docx  # noqa: E402

from app.services.document_parser import limits  # noqa: E402
from app.services.document_parser.parse import parse_document  # noqa: E402
from app.services.document_parser.precheck import precheck_uploads  # noqa: E402
from app.services.document_parser.text_utils import effective_char_count  # noqa: E402


# ---------- PDF ----------


def _two_column_pdf() -> bytes:
    texts = [(60, 800, 22, "Annual Report Title")]
    for index in range(20):
        texts.append((50, 750 - index * 14, 10, f"Left column line {index} alpha"))
        texts.append((320, 750 - index * 14, 10, f"Right column line {index} gamma"))
    texts.append((280, 20, 9, "Page 1"))
    return build_pdf([{"texts": texts}])


def test_pdf_two_columns_read_left_then_right_and_drop_page_number() -> None:
    document = parse_document("r.pdf", ".pdf", _two_column_pdf())
    text = document.text
    assert text.startswith("# Annual Report Title")
    assert text.index("Left column line 19") < text.index("Right column line 0")
    assert "Page 1" not in text


def _row_words(rows: int, spanning: int) -> list[list[dict]]:
    """中文排版：一整行常常只是一个「词」。"""
    from app.services.document_parser.pdf_parser import _group_lines

    def word(x0, x1, top):
        return {"x0": x0, "x1": x1, "top": top, "bottom": top + 10, "size": 10, "text": "字"}

    words = [word(150, 450, 40 + 22 * index) for index in range(spanning)]
    for row in range(rows):
        words += [word(50, 285, 120 + row * 20), word(310, 545, 120 + row * 20)]
    return _group_lines(words)


def test_column_gap_tolerates_spanning_titles_and_short_cjk_pages() -> None:
    from app.services.document_parser.pdf_parser import _column_gap

    assert _column_gap(_row_words(25, 2), 595) is not None
    assert _column_gap(_row_words(25, 3), 595) is not None
    assert _column_gap(_row_words(12, 1), 595) is not None


def test_column_gap_not_found_on_single_column_page() -> None:
    from app.services.document_parser.pdf_parser import _column_gap, _group_lines

    lines = _group_lines([{"x0": 50, "x1": 545, "top": 40 + 20 * row, "bottom": 50 + 20 * row, "size": 10, "text": "字"} for row in range(30)])
    assert _column_gap(lines, 595) is None


def test_pdf_scanned_pages_become_private_snapshots_not_public_images() -> None:
    scan = jpeg_bytes(600, 850, (90, 90, 90))
    document = parse_document("scan.pdf", ".pdf", build_pdf([{"image": (0, 0, 595, 842, scan)}] * 2))
    assert [snap.page_no for snap in document.snapshots] == [1, 2]
    assert all(max(snap.width, snap.height) <= limits.SNAPSHOT_MAX_EDGE for snap in document.snapshots)
    # 整页扫描底图交给快照，不作为配图公开。
    assert document.images == []
    assert effective_char_count(document.text) == 0


def test_pdf_low_text_page_with_big_image_gets_snapshot_and_core_image() -> None:
    pages = [
        {"texts": [(60, 800, 22, "Title"), (50, 760, 10, "Body text " * 20)], "image": (60, 300, 200, 120, jpeg_bytes(400, 240))},
        {"texts": [(50, 780, 10, "Architecture overview")], "image": (50, 200, 500, 500, jpeg_bytes(500, 500, (10, 200, 10)))},
    ]
    document = parse_document("deck.pdf", ".pdf", build_pdf(pages))
    assert [snap.page_no for snap in document.snapshots] == [2]
    assert len(document.images) == 2
    assert document.images[0].core is False
    assert document.images[1].core is True
    assert "第 2 页" in document.images[1].label


def test_pdf_images_capped_per_document() -> None:
    pages = [
        {"texts": [(50, 780, 10, "Text " * 30)], "image": (50, 100, 300, 300, jpeg_bytes(300 + index, 300, (index * 9 % 255, 80, 120)))}
        for index in range(limits.DOC_MAX_IMAGES + 3)
    ]
    data = build_pdf(pages)
    # 页数上限比图片上限高，这里绕过预检直接测解析器。
    document = parse_document("many.pdf", ".pdf", data)
    assert len(document.images) == limits.DOC_MAX_IMAGES


# ---------- PPTX ----------


def _deck() -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    presentation = Presentation()
    layout = presentation.slide_layouts[5]
    slide = presentation.slides.add_slide(layout)
    slide.shapes.title.text = "产品架构"
    group = slide.shapes.add_group_shape()
    right = group.shapes.add_textbox(Inches(5), Inches(2), Inches(3), Inches(1))
    right.text_frame.text = "右侧卡片：高可用"
    left = slide.shapes.add_textbox(Inches(0.5), Inches(2), Inches(3), Inches(1))
    left.text_frame.text = "左侧卡片：低延迟"
    chart_data = CategoryChartData()
    chart_data.categories = ["Q1", "Q2"]
    chart_data.add_series("收入", (10, 20))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(4), Inches(4), Inches(2), chart_data)
    slide.notes_slide.notes_text_frame.text = "这里讲架构"
    poster = presentation.slides.add_slide(layout)
    poster.shapes.title.text = "海报"
    poster.shapes.add_picture(io.BytesIO(jpeg_bytes(800, 600)), Inches(0), Inches(1.5), Inches(10), Inches(6))
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def test_pptx_reading_order_group_chart_notes_and_core_image() -> None:
    document = parse_document("deck.pptx", ".pptx", _deck())
    text = document.text
    assert "## 第 1 页：产品架构" in text
    # 组合形状里的文字没丢，且按版面从左到右。
    assert text.index("左侧卡片") < text.index("右侧卡片")
    assert "| 收入 | 10 | 20 |" in text
    assert "演讲者备注：这里讲架构" in text
    assert "主题色：" in text
    assert len(document.images) == 1 and document.images[0].core is True


# ---------- DOCX ----------


def _docx_with_merged_table_and_image() -> bytes:
    import docx
    from docx.shared import Inches

    document = docx.Document()
    document.add_heading("项目总结", 1)
    document.add_paragraph("正文第一段。")
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "合并表头"
    table.cell(0, 2).text = "C"
    table.cell(1, 0).merge(table.cell(2, 0))
    table.cell(1, 0).text = "纵向"
    table.cell(1, 1).text = "b1"
    table.cell(2, 1).text = "b2"
    document.add_paragraph("列表项", style="List Bullet")
    document.add_picture(io.BytesIO(png_bytes(500, 400)), width=Inches(5))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_heading_merged_table_list_and_image_in_order() -> None:
    document = parse_document("a.docx", ".docx", _docx_with_merged_table_and_image())
    text = document.text
    assert text.startswith("## 项目总结")
    assert '<td colspan="2">合并表头</td>' in text
    assert '<td rowspan="2">纵向</td>' in text
    assert "- 列表项" in text
    assert text.index("合并表头") < text.index("- 列表项") < text.index("⟦IMG:0⟧")
    assert document.images[0].label == "a.docx「项目总结」附近"


# ---------- Excel ----------


def _workbook(rows: int) -> bytes:
    import datetime

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "明细"
    sheet.append(["地区", "金额", "日期"])
    for index in range(rows):
        sheet.append([["华东", "华北", "华南"][index % 3], index * 1.5, datetime.date(2026, 1, 1) + datetime.timedelta(days=index)])
    merged = workbook.create_sheet("合并表头")
    merged.append(["地区", "2024", None])
    merged.merge_cells("B1:C1")
    merged.append(["", "Q1", "Q2"])
    merged.append(["华东", 1, 2])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_excel_small_table_is_kept_whole() -> None:
    text = parse_document("x.xlsx", ".xlsx", _workbook(10)).text
    assert "| 华东 | 0 | 2026-01-01 |" in text
    assert "统计" not in text.split("## 工作表：合并表头")[0]


def test_excel_large_table_gets_exact_profile_and_samples() -> None:
    text = parse_document("x.xlsx", ".xlsx", _workbook(100)).text
    assert "共 100 行数据" in text
    assert "合计 7,425" in text
    assert "最常见：华东（34）" in text
    assert "前 15 行" in text and "末 5 行" in text


def test_excel_merged_header_is_expanded() -> None:
    text = parse_document("x.xlsx", ".xlsx", _workbook(1)).text.split("## 工作表：合并表头")[1]
    assert "| 地区 | 2024/Q1 | 2024/Q2 |" in text


# ---------- HTML ----------


def test_html_keeps_structure_and_drops_scripts() -> None:
    html = "<html><head><title>介绍</title><script>alert(1)</script></head><body><h2>功能</h2><ul><li>快</li><li>稳</li></ul><table><tr><th>A</th></tr><tr><td>1</td></tr></table></body></html>"
    text = parse_document("p.html", ".html", html.encode()).text
    assert text.startswith("# 介绍")
    assert "## 功能" in text and "- 快\n- 稳" in text and "| A |" in text
    assert "alert" not in text


def test_rotated_photo_reports_display_size() -> None:
    from PIL import Image

    from app.services.document_parser.images import image_size, normalize_image

    image = Image.new("RGB", (1600, 900), (10, 100, 200))
    exif = Image.Exif()
    exif[0x0112] = 6  # 存成横向像素，显示时转 90°，实际是竖图
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    data = buffer.getvalue()
    assert image_size(data) == (900, 1600)
    normalized = normalize_image(data)
    assert (normalized.width, normalized.height) == (900, 1600)


# ---------- 预检 ----------


def _expect(status: int, files: list[tuple[str, bytes]]) -> str:
    with pytest.raises(HTTPException) as caught:
        precheck_uploads(files)
    assert caught.value.status_code == status
    return str(caught.value.detail)


def test_precheck_splits_documents_and_images() -> None:
    documents, images = precheck_uploads([("a.pdf", _two_column_pdf()), ("logo.png", png_bytes())])
    assert [item.filename for item in documents] == ["a.pdf"]
    assert [(item.filename, item.width, item.height) for item in images] == [("logo.png", 300, 200)]


def test_precheck_rejects_too_many_files_and_unknown_types() -> None:
    _expect(413, [(f"{index}.txt", b"hi") for index in range(limits.MAX_FILE_COUNT + 1)])
    assert "格式暂不支持" in _expect(400, [("a.exe", b"MZ")])


def test_precheck_rejects_pdf_over_page_limit() -> None:
    pages = [{"texts": [(50, 780, 10, f"page {index}")]} for index in range(limits.PDF_MAX_PAGES + 1)]
    assert f"最多支持 {limits.PDF_MAX_PAGES} 页" in _expect(422, [("long.pdf", build_pdf(pages))])


def test_precheck_rejects_encrypted_pdf_and_fake_extension() -> None:
    assert "加密" in _expect(422, [("locked.pdf", build_pdf([{"texts": [(50, 780, 10, "x")]}], encrypt=True))])
    assert "不是有效的 PDF" in _expect(400, [("fake.pdf", b"hello world")])
    assert "不是有效的" in _expect(400, [("fake.png", b"GIF8")])


def test_precheck_rejects_corrupt_spreadsheets() -> None:
    assert "无法打开" in _expect(422, [("bad.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)])
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "not xml")
    assert "无法打开" in _expect(422, [("bad.xlsx", buffer.getvalue())])
    documents, _images = precheck_uploads([("ok.xlsx", _workbook(3))])
    assert documents[0].filename == "ok.xlsx"


def test_precheck_rejects_zip_bomb_and_big_image() -> None:
    assert "解压后内容过大" in _expect(413, [("bomb.docx", zip_bomb_docx(limits.ZIP_MAX_UNCOMPRESSED_BYTES + 1))])
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (limits.MAX_UPLOAD_IMAGE_BYTES + 1)
    assert "超过 10MB" in _expect(413, [("big.png", big)])


def test_precheck_rejects_pptx_over_slide_limit() -> None:
    from pptx import Presentation

    presentation = Presentation()
    for _ in range(limits.PPTX_MAX_SLIDES + 1):
        presentation.slides.add_slide(presentation.slide_layouts[6])
    buffer = io.BytesIO()
    presentation.save(buffer)
    assert f"最多支持 {limits.PPTX_MAX_SLIDES} 页" in _expect(422, [("long.pptx", buffer.getvalue())])


def test_precheck_rejects_docx_with_too_many_blocks() -> None:
    import docx

    document = docx.Document()
    for index in range(limits.DOCX_MAX_BLOCKS + 1):
        document.add_paragraph(str(index))
    buffer = io.BytesIO()
    document.save(buffer)
    assert "段落和表格过多" in _expect(422, [("long.docx", buffer.getvalue())])
