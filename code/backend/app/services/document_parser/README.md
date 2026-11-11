# document_parser：上传资料解析与信息提取

把用户上传的文档变成写码模型用得上的资料：创建请求里预检，后台解析一次全批共用，每轮由写码同款模型按需求提取。

## 流程

```text
POST /api/generations
  precheck.precheck_uploads      同步预检，坏文件 4xx；图片入 page_assets，文档原件暂存
  pipeline.stage_documents       原件写私有键 batch-uploads/{staging}/{n}{ext}
后台每个模型的 run_task
  pipeline.wait_for_batch_parse  批次共享的解析任务（第一个模型拉起，其余等同一个结果）
    parse.parse_document         按扩展名分发到各格式解析器（线程里跑，单文件 60 秒超时）
    _store_outputs               配图写 page_assets(doc_embedded)，快照写 batch-private/{batch}/snapshots/
  context.collect_lineage        沿会话线（view_batches）收集资料，按文件哈希去重，总长 ≤ 12 万字
  extractor_agent.extract_for_round  同模型、关闭思考、纯文本提取本轮相关内容
  attachments.build_document_context / load_attachments  决定列哪些配图地址、附哪些图片
```

## 模块

| 文件 | 作用 |
| --- | --- |
| `limits.py` | 全部上限。前端 `page.tsx` 的同名常量要同步改 |
| `precheck.py` | 文件头、能否打开、加密、页数、zip 解压规模、图片像素 |
| `pdf_parser.py` | pdfplumber 按坐标分栏还原阅读顺序、表格、标题层级，去页眉页脚；pypdfium2 取配图、渲染整页快照 |
| `pptx_parser.py` | 展开组合形状，按版面位置排序；表格、图表数据、备注、母版主题色、配图 |
| `docx_parser.py` | 段落和表格保持原顺序；浮动图、文本框；有合并单元格的表格输出 HTML |
| `excel_parser.py` | python-calamine 读 xlsx / xls；≤ 60 行整表，更大的表给代码算的统计画像加首尾样本 |
| `html_parser.py` | HTML 转 Markdown（保留标题、列表、表格）；txt / md 只解码 |
| `images.py` | 图片识别、归一化（转网页格式、过滤小图、去重）、转 data URL |
| `pipeline.py` | 暂存、批次共享解析、入库、失败清理、只退一次免费次数 |
| `context.py` | 跨轮资料收集与去重、纯视觉资料判断 |
| `extractor_agent.py` | 提取 prompt 与输出协议（`<EMPTY/>`、`IMAGE_REFS`）、分段并发、总超时 |
| `attachments.py` | 写码阶段的图片双通道 |

## 约定

- **锚点**：解析器先在正文里留占位 `⟦IMG:n⟧` / `⟦SNAP:n⟧`，入库后换成
  `[配图 img:{asset_id}｜位置｜宽×高]`、`[核心内容大图 img:…]`、`[整页快照 snap:{id}｜文件 第 N 页｜原因]`。
  提取模型看不到图，靠锚点判断要不要用图，用 `IMAGE_REFS: id1, id2` 告诉写码阶段。
- **快照不公开**：少字大图页、扫描页渲染的整页图只存 `generation_batches.visual_snapshots` 记录的私有键，不进 `page_assets`，
  `/assets` 读不到，也从不写进页面可用的地址清单。扫描底图（占页面七成以上）不作为配图取出。
- **核心内容大图**：少字页上占版面四分之一以上的图，`source_label` 末尾带「｜核心内容大图」。
- **有效字数**：`text_utils.effective_char_count` 会去掉锚点、页码标记、文件头这类结构行，用来判断「纯视觉资料」。
- **解析的取消**：等待者被停掉时，先查同批还有没有排队或在跑的任务（排除刚被停的），有就让解析跑完；
  解析任务登记到解析结束才注销，后到的模型复用同一个任务。
- **提取结果为空的判断**：正文为空且没有 `IMAGE_REFS` 才算「与资料无关」；只点名了图片也要把图交给写码模型。
- **重复图片**：同一批多份文档出现同一张图只存一份，各文档都用同一个 `img:` 锚点。
- **纯视觉资料直通**：资料除锚点外不到 100 字、又有快照或核心大图时，在它上传的那一轮跳过提取，直接把图交给写码模型。
- **失败语义**：解析和提取失败抛 `DocumentStageError` 的子类，任务 `error_message` 带 `document_stage: ` 前缀；
  整批每一路都以这个前缀失败时退回一次匿名免费次数。
- **许可证**：只用宽松许可的库（pdfplumber MIT、pypdfium2 BSD/Apache、python-pptx / python-docx / python-calamine MIT）。
  不要引入 PyMuPDF / pymupdf4llm，它们是 AGPL。

## 加一种格式

1. `limits.py` 的扩展名集合、`precheck.py` 的校验、`parse.py` 的分发各加一处。
2. 解析器返回 `ParsedDocument`：正文里放占位，配图放 `images`，需要整页给模型看的放 `snapshots`。
3. 在 `tests/doc_fixtures.py` 里加生成器，在 `tests/test_document_parsers.py` 里加用例。
4. 前端 `page.tsx` 的 `DOCUMENT_FILE_EXTENSIONS` 同步加上。
