#!/usr/bin/env python3
"""文档解析隔离与内存峰值压测脚本。

现场生成接近各项上限的四份压力样本：
  1. 30p.pdf        — 30 页双栏图文 + 矢量框 PDF（含少字大图快照页）
  2. 1000img.docx   — 含 1,000 张不同图片 + 1,900 段落的 DOCX
  3. 50k_100c.xlsx  — 50,000 行 × 100 列的共享字符串大型表格
  4. 40slides.pptx  — 40 页带标题、要点与高分辩率配图的 PPTX

分别在独立进程中测量三组核心指标：
  1) web 进程预检（Starlette SpooledTemporaryFile 流式预检）：峰值 RSS 增量与事件循环最大延迟 loop_lag_max_ms
  2) worker 父进程解析期间：父进程自身 RSS 增量与事件循环最大延迟 loop_lag_max_ms（目标 < 100ms）
  3) 解析子进程峰值 RSS（os.wait4 ru_maxrss）：单份 < 1.5GB，双 worker 同时解析峰值合计 < 3GB

用法：
  code/backend/.venv/bin/python script/parse_stress.py
  code/backend/.venv/bin/python script/parse_stress.py --json
"""

from __future__ import annotations

import argparse
import io
import json
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "code" / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

from doc_fixtures import build_pdf  # noqa: E402


def _make_jpeg(width: int, height: int, seed: int, quality: int = 85) -> bytes:
    raw = bytearray(width * height * 3)
    step_seed = (seed * 2654435761) & 0xFFFFFFFF
    for idx in range(0, len(raw), 3):
        val = (idx + step_seed) * 2246822519 & 0xFF
        raw[idx] = val
        raw[idx + 1] = (val * 3 + seed) & 0xFF
        raw[idx + 2] = (val * 7 + seed) & 0xFF
    img = Image.frombytes("RGB", (width, height), bytes(raw))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _unique_jpeg_variant(base_jpeg: bytes, index: int) -> bytes:
    payload = f"sp-stress-img-{index:05d}".encode("ascii")
    segment = b"\xff\xfe" + struct.pack(">H", len(payload) + 2) + payload
    return base_jpeg[:2] + segment + base_jpeg[2:]


def build_30p_pdf() -> bytes:
    pages = []
    for p in range(30):
        img = _make_jpeg(1200, 900, p + 1, quality=88)
        if p < 3:
            texts = [(50, 800, 14, f"Visual Overview {p + 1}")]
        else:
            texts = [(60, 800, 20, f"Section {p + 1} Report")]
            for r in range(35):
                texts.append((50, 760 - r * 14, 10, f"Left col p{p} row{r} metric value {p * 100 + r}"))
                texts.append((320, 760 - r * 14, 10, f"Right col p{p} row{r} detail note {p * 100 + r}"))
        rects = [(50, 120, 240, 80), (50, 160, 240, 40), (170, 120, 120, 80)]
        pages.append({"texts": texts, "image": (50, 220, 480, 360, img), "rects": rects})
    return build_pdf(pages)


def build_1000img_docx() -> bytes:
    base_jpeg = _make_jpeg(300, 220, 77, quality=72)
    buf = io.BytesIO()
    rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    ]
    body: list[str] = []
    long_run = "".join(
        f'<w:r><w:rPr><w:b/><w:sz w:val="22"/></w:rPr><w:t>数据字段说明与经营指标解读第{k}节</w:t></w:r>'
        for k in range(35)
    )
    for i in range(1000):
        rid = f"rImg{i}"
        rels.append(
            f'<Relationship Id="{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/img{i}.jpg"/>'
        )
        body.append(
            f"<w:p>{long_run}"
            f'<w:r><w:drawing><wp:inline><wp:extent cx="2400000" cy="1800000"/>'
            f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:blipFill><a:blip r:embed="{rid}"/></pic:blipFill></pic:pic>'
            f"</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )
    for _ in range(900):
        body.append(f"<w:p>{long_run}</w:p>")
    rels.append("</Relationships>")
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        "<w:body>" + "".join(body) + "</w:body></w:document>"
    )
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="jpg" ContentType="image/jpeg"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", "".join(rels))
        for i in range(1000):
            zf.writestr(f"word/media/img{i}.jpg", _unique_jpeg_variant(base_jpeg, i))
    return buf.getvalue()


def build_50k_100c_xlsx() -> bytes:
    buf = io.BytesIO()
    sst_items = ["华东", "华北", "华南", "西南", "西北", "东北"] + [f"Col_{c}" for c in range(100)]
    sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(sst_items)}" uniqueCount="{len(sst_items)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in sst_items)
        + "</sst>"
    )
    header_cells = "".join(f'<c t="s"><v>{6 + c}</v></c>' for c in range(100))
    rows = [f'<row r="1">{header_cells}</row>']
    tail_cells = "".join(
        f'<c t="s"><v>{c % 6}</v></c>' if c % 5 == 0 else f"<c><v>{c * 1.25}</v></c>" for c in range(2, 100)
    )
    for r in range(2, 50002):
        c0 = f'<c t="s"><v>{r % 6}</v></c>'
        c1 = f"<c><v>{r * 1.5}</v></c>"
        rows.append(f'<row r="{r}">{c0}{c1}{tail_cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(rows)
        + "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Data50k" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
            'Target="sharedStrings.xml"/></Relationships>',
        )
        zf.writestr("xl/sharedStrings.xml", sst_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


def build_40slides_pptx() -> bytes:
    prs = Presentation()
    layout = prs.slide_layouts[5]
    for s in range(40):
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = f"Slide {s + 1}: Quarterly Deep Dive"
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(4.5), Inches(3))
        tb.text_frame.text = "\n".join(f"Bullet point {i} on slide {s + 1} with detailed commentary" for i in range(10))
        img = _make_jpeg(1000, 750, 200 + s, quality=85)
        slide.shapes.add_picture(io.BytesIO(img), Inches(5.2), Inches(1.5), Inches(4.2), Inches(3.2))
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


_PROBE_SNIPPET = r"""
import asyncio, json, shutil, sys, tempfile, time
from pathlib import Path
from starlette.datastructures import UploadFile

mode, name, ext, src_path = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])

def status_mb(field: str) -> float:
    with open("/proc/self/status") as handle:
        for line in handle:
            if line.startswith(field + ":"):
                return round(int(line.split()[1]) / 1024, 1)
    return 0.0

async def main() -> None:
    from app.services.document_parser.precheck import precheck_uploads
    from app.services.document_parser.child import parse_subprocess_stats, run_parse_subprocess

    lags = []
    stop = False
    loop = asyncio.get_running_loop()

    async def ticker() -> None:
        while not stop:
            started = loop.time()
            await asyncio.sleep(0.01)
            lags.append(max(0.0, (loop.time() - started - 0.01) * 1000))

    if mode == "web_precheck":
        spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        with src_path.open("rb") as src:
            shutil.copyfileobj(src, spool)
        spool.seek(0)
        upload = UploadFile(file=spool, size=src_path.stat().st_size, filename=name)
        base_rss, base_hwm = status_mb("VmRSS"), status_mb("VmHWM")
        tick = asyncio.create_task(ticker())
        await asyncio.sleep(0.03)
        lags.clear()
        t0 = time.perf_counter()
        docs, _imgs = await asyncio.to_thread(precheck_uploads, [upload])
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        await asyncio.sleep(0.02)
        stop = True
        await tick
        await upload.close()
        peak_hwm, end_rss = status_mb("VmHWM"), status_mb("VmRSS")
        print(json.dumps({
            "sample": name,
            "mode": mode,
            "elapsed_ms": elapsed_ms,
            "base_rss_mb": base_rss,
            "end_rss_mb": end_rss,
            "peak_hwm_mb": peak_hwm,
            "delta_hwm_mb": round(peak_hwm - base_hwm, 1),
            "loop_lag_max_ms": round(max(lags) if lags else 0.0, 1),
            "staged_in_ram": docs[0].data is not None if docs else False,
        }))
    elif mode == "worker_parse":
        base_rss, base_hwm = status_mb("VmRSS"), status_mb("VmHWM")
        tick = asyncio.create_task(ticker())
        await asyncio.sleep(0.03)
        lags.clear()
        t0 = time.perf_counter()
        doc = await run_parse_subprocess(filename=name, extension=ext, input_path=src_path, timeout_seconds=60)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        # 模拟上传完立即清空字节
        for img in doc.images:
            img.data = b""
        for snap in doc.snapshots:
            snap.data = b""
        await asyncio.sleep(0.02)
        stop = True
        await tick
        peak_hwm, end_rss = status_mb("VmHWM"), status_mb("VmRSS")
        child_stats = parse_subprocess_stats()
        print(json.dumps({
            "sample": name,
            "mode": mode,
            "elapsed_ms": elapsed_ms,
            "parent_base_rss_mb": base_rss,
            "parent_end_rss_mb": end_rss,
            "parent_peak_hwm_mb": peak_hwm,
            "parent_delta_hwm_mb": round(peak_hwm - base_hwm, 1),
            "parent_loop_lag_max_ms": round(max(lags) if lags else 0.0, 1),
            "child_peak_rss_mb": child_stats["parse_last_rss_mb"],
            "images": len(doc.images),
            "snapshots": len(doc.snapshots),
            "chars": len(doc.text),
        }))

asyncio.run(main())
"""


def _run_probe(mode: str, name: str, ext: str, path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SNIPPET, mode, name, ext, str(path)],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> None:
    parser = argparse.ArgumentParser(description="运行文档解析内存峰值与事件循环延迟压测")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON 结果")
    args = parser.parse_args()

    builders = [
        ("30p.pdf", ".pdf", build_30p_pdf),
        ("1000img.docx", ".docx", build_1000img_docx),
        ("50k_100c.xlsx", ".xlsx", build_50k_100c_xlsx),
        ("40slides.pptx", ".pptx", build_40slides_pptx),
    ]

    with tempfile.TemporaryDirectory(prefix="sp-stress-") as tmp:
        tmp_dir = Path(tmp)
        sample_paths: list[tuple[str, str, Path, float]] = []
        for name, ext, builder in builders:
            data = builder()
            sample_path = tmp_dir / name
            sample_path.write_bytes(data)
            sample_paths.append((name, ext, sample_path, round(len(data) / 1024 / 1024, 2)))

        web_rows = []
        worker_rows = []
        for name, ext, sample_path, size_mb in sample_paths:
            w = _run_probe("web_precheck", name, ext, sample_path)
            w["size_mb"] = size_mb
            web_rows.append(w)

            p = _run_probe("worker_parse", name, ext, sample_path)
            p["size_mb"] = size_mb
            worker_rows.append(p)

        # 双 worker 同时解析最重样本（50k_100c.xlsx + 30p.pdf），测量并发子进程峰值合计
        xlsx_path = next(p for n, _, p, _ in sample_paths if n == "50k_100c.xlsx")
        pdf_path = next(p for n, _, p, _ in sample_paths if n == "30p.pdf")
        p1 = subprocess.Popen(
            [sys.executable, "-c", _PROBE_SNIPPET, "worker_parse", "50k_100c.xlsx", ".xlsx", str(xlsx_path)],
            cwd=str(BACKEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        p2 = subprocess.Popen(
            [sys.executable, "-c", _PROBE_SNIPPET, "worker_parse", "30p.pdf", ".pdf", str(pdf_path)],
            cwd=str(BACKEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        out1, err1 = p1.communicate()
        out2, err2 = p2.communicate()
        if p1.returncode != 0 or p2.returncode != 0:
            raise RuntimeError(f"并发解析失败: {err1}\n{err2}")
        c1 = json.loads(out1.strip().splitlines()[-1])
        c2 = json.loads(out2.strip().splitlines()[-1])
        concurrent_summary = {
            "worker_1_child_rss_mb": c1["child_peak_rss_mb"],
            "worker_2_child_rss_mb": c2["child_peak_rss_mb"],
            "combined_child_peak_rss_mb": round(c1["child_peak_rss_mb"] + c2["child_peak_rss_mb"], 1),
            "worker_1_loop_lag_max_ms": c1["parent_loop_lag_max_ms"],
            "worker_2_loop_lag_max_ms": c2["parent_loop_lag_max_ms"],
        }

    report = {"web_precheck": web_rows, "worker_parse": worker_rows, "concurrent_two_workers": concurrent_summary}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("### 1. Web 进程流式预检（SpooledTemporaryFile）")
    print("| 样本 | 文件大小(MB) | 耗时(ms) | 峰值增量(MB) | 结束RSS(MB) | 最大loop_lag(ms) | 原件驻留内存 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | :---: |")
    for row in web_rows:
        print(
            f"| {row['sample']} | {row['size_mb']} | {row['elapsed_ms']} | {row['delta_hwm_mb']} "
            f"| {row['end_rss_mb']} | {row['loop_lag_max_ms']} | {row['staged_in_ram']} |"
        )

    print("\n### 2. Worker 父进程与解析子进程")
    print("| 样本 | 耗时(ms) | 父进程峰值增量(MB) | 父进程结束RSS(MB) | 父进程最大loop_lag(ms) | 子进程峰值RSS(MB) |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for row in worker_rows:
        print(
            f"| {row['sample']} | {row['elapsed_ms']} | {row['parent_delta_hwm_mb']} "
            f"| {row['parent_end_rss_mb']} | {row['parent_loop_lag_max_ms']} | {row['child_peak_rss_mb']} |"
        )

    print("\n### 3. 双 Worker 并发解析（50k_100c.xlsx + 30p.pdf）")
    print(json.dumps(concurrent_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
