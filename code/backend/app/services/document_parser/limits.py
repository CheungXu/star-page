"""上传与解析的硬上限。前端 page.tsx 里的同名常量要保持一致。"""

from __future__ import annotations

MAX_FILE_COUNT = 3
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_IMAGE_BYTES = 10 * 1024 * 1024

# Office 文件是 zip：解压后总大小和条目数都要有上限，防 zip 炸弹。
ZIP_MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
ZIP_MAX_ENTRIES = 5000
# 约 6000×6000，超过就当作像素炸弹拒绝。
MAX_IMAGE_PIXELS = 36_000_000

PDF_MAX_PAGES = 30
PPTX_MAX_SLIDES = 40
DOCX_MAX_BLOCKS = 2000
XLSX_MAX_SHEETS = 10
XLSX_MAX_ROWS = 50_000
XLSX_MAX_COLS = 100
# 表格行数不超过它就整表给出，超过就给统计画像加样本行。
XLSX_FULL_TABLE_ROWS = 60

DOC_MAX_IMAGES = 20
BATCH_MAX_SNAPSHOTS = 12
MIN_IMAGE_SIDE = 80
SNAPSHOT_MAX_EDGE = 1600
STORED_IMAGE_MAX_EDGE = 2400
PARSE_TIMEOUT_SECONDS = 60

DOCUMENT_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xls", ".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS
SUPPORTED_FILE_TYPES_LABEL = "docx、pptx、xlsx、xls、pdf、txt、md、html 或 png、jpg、webp、gif"
