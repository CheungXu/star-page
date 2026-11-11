"""测试用的文档生成器：现场造 PDF / PPTX / DOCX / XLSX，不依赖仓库里的样例文件。"""

from __future__ import annotations

import io
import zipfile

from PIL import Image


def jpeg_bytes(width: int = 400, height: int = 300, color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), color)
    # 加点内容，避免不同尺寸的纯色图被当成同一张。
    for x in range(0, width, 7):
        image.putpixel((x, x * height // max(width, 1) % height), (255, 255, 255))
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def png_bytes(width: int = 300, height: int = 200, color: tuple[int, int, int] = (200, 60, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def build_pdf(pages: list[dict], *, encrypt: bool = False) -> bytes:
    """pages: [{"texts": [(x, y, size, text)], "image": (x, y, w, h, jpeg_bytes) | None}]，单位 pt，A4。

    文字用 Helvetica，只支持 ASCII。坐标原点在左下角。
    """
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    kids_index = add(b"")  # Pages 对象最后回填
    for page in pages:
        resources = f"/Font << /F1 {font_id} 0 R >>"
        stream = []
        for x, y, size, text in page.get("texts", []):
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.append(f"BT /F1 {size} Tf {x} {y} Td ({safe}) Tj ET")
        image = page.get("image")
        if image is not None:
            x, y, w, h, data = image
            with Image.open(io.BytesIO(data)) as pil:
                px_w, px_h = pil.size
            image_id = add(
                f"<< /Type /XObject /Subtype /Image /Width {px_w} /Height {px_h} /ColorSpace /DeviceRGB "
                f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"
            )
            resources += f" /XObject << /Im1 {image_id} 0 R >>"
            stream.append(f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q")
        for rect in page.get("rects", []):
            stream.append(f"{rect[0]} {rect[1]} {rect[2]} {rect[3]} re S")
        content = "\n".join(stream).encode()
        content_id = add(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {kids_index} 0 R /MediaBox [0 0 595 842] "
                f"/Resources << {resources} >> /Contents {content_id} 0 R >>".encode()
            )
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[kids_index - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    catalog_id = add(f"<< /Type /Catalog /Pages {kids_index} 0 R >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R"
    if encrypt:
        # 只用于预检：声明了 Encrypt 字典但不可解，pdfium 会要求密码。
        trailer += " /Encrypt << /Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -4 >>"
    out.write(f"{trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def zip_bomb_docx(entry_size: int) -> bytes:
    """声明的解压大小很大的 docx（内容是重复字节，压缩后很小）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr("word/media/huge.bin", b"\x00" * entry_size)
    return buffer.getvalue()
