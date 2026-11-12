# document_parser：上传资料解析与信息提取

把用户上传的文档变成写码模型用得上的资料：创建请求里流式轻预检，后台受限子进程解析一次全批共用，每轮由写码同款模型按需求提取。

## 流程

```text
POST /api/generations
  precheck.precheck_uploads      基于 UploadFile 临时文件流同步轻预检（size 先拦、DOCX iterparse），坏文件 4xx
  pipeline.stage_documents       put_stream 直接把临时文件流写进私有键 batch-uploads/{staging}/{n}{ext}
后台每个模型的 run_task
  pipeline.wait_for_batch_parse  批次共享解析（跨 worker 原子抢占 + 续期，parse_attempts 防毒文件最多 2 次）
    storage.download_file        原件按块下载到临时目录，不在 worker 父进程内存里驻留
    child.run_parse_subprocess   拉起受限子进程（nice 10 + RLIMIT_AS 1.5GB，超时/取消立即 SIGKILL + wait4）
      parse.parse_document       按扩展名分发到各格式解析器，结果 pickle 写回临时文件
    _store_outputs               配图写 page_assets(doc_embedded)，快照写 batch-private/{batch}/snapshots/，传完即清字节
  context.collect_lineage        沿会话线（view_batches）收集资料，按文件哈希去重，总长 ≤ 12 万字
  extractor_agent.extract_for_round  同模型、关闭思考、纯文本提取本轮相关内容
  attachments.build_document_context / load_attachments  决定列哪些配图地址、附哪些图片
```

## 模块

| 文件 | 作用 |
| --- | --- |
| `limits.py` | 全部上限。前端 `page.tsx` 的同名常量要同步改 |
| `precheck.py` | `UploadFile.size` 前置拦截、文件头、能否打开、加密、页数、zip 解压规模、DOCX `iterparse` 流式段落计数、图片像素 |
| `child.py` | 解析子进程入口与父进程调度器：并发信号量、`nice(10)`、`RLIMIT_AS`、`pidfd` + `os.wait4` 采集 `ru_maxrss`、超时与取消强杀 |
| `pdf_parser.py` | 单次打开 pypdfium2 + pdfplumber（逐页 `flush_cache`）分栏还原阅读顺序、表格、标题层级，去页眉页脚，取配图与整页快照 |
| `pptx_parser.py` | 展开组合形状，按版面位置排序；表格、图表数据、备注、母版主题色、配图 |
| `docx_parser.py` | 段落和表格保持原顺序；浮动图、文本框；收满 20 张有效配图立即停止取图；合并单元格表格输出 HTML |
| `excel_parser.py` | python-calamine `iter_rows` 按行流式读取；≤ 60 行整表，大表用紧凑数组流式算统计画像加首尾样本 |
| `html_parser.py` | HTML 转 Markdown（保留标题、列表、表格）；txt / md 只解码 |
| `images.py` | 图片识别、单次重编码归一化（转网页格式、过滤小图、去重）、转 data URL |
| `pipeline.py` | 流式暂存、跨 worker 批次解析抢占与续期、`parse_attempts` 毒文件熔断、退出交接 `shutdown_parse_jobs`、只退一次免费次数 |
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
- **解析的取消与退出交接**：等待者被停掉时，先查同批还有没有排队或在跑的任务（排除刚被停的），有就让解析跑完；整批取消或进程排空超时退出（`shutdown_parse_jobs`）时立即取消解析任务并 `SIGKILL` 子进程。正常退出交接会退回本次 `parse_attempts` 计数；连续两次崩溃失联（`parse_attempts >= 2`）直接标失败并退款，不再无限接手。
- **提取结果为空的判断**：正文为空且没有 `IMAGE_REFS` 才算「与资料无关」；只点名了图片也要把图交给写码模型。
- **重复图片**：同一批多份文档出现同一张图只存一份，各文档都用同一个 `img:` 锚点。
- **纯视觉资料直通**：资料除锚点外不到 100 字、又有快照或核心大图时，在它上传的那一轮跳过提取，直接把图交给写码模型。
- **失败语义**：解析和提取失败抛 `DocumentStageError` 的子类，任务 `error_message` 带 `document_stage: ` 前缀；
  整批每一路都以这个前缀失败时退回一次匿名免费次数。
- **许可证**：只用宽松许可的库（pdfplumber MIT、pypdfium2 BSD/Apache、python-pptx / python-docx / python-calamine MIT）。
  不要引入 PyMuPDF / pymupdf4llm，它们是 AGPL。

## 加一种格式

1. `limits.py` 的扩展名集合、`precheck.py` 的校验、`parse.py` 的分发各加一处（解析器同时支持 `bytes` 与 `Path` 输入）。
2. 解析器返回 `ParsedDocument`：正文里放占位，配图放 `images`，需要整页给模型看的放 `snapshots`。
3. 在 `tests/doc_fixtures.py` 里加生成器，在 `tests/test_document_parsers.py` 里加用例，并用 `script/parse_stress.py` 验证峰值内存。
4. 前端 `page.tsx` 的 `DOCUMENT_FILE_EXTENSIONS` 同步加上。
