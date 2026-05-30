# 上传资料 PDF 支持与后端镜像发布记录

## 背景

本次工作围绕上传资料能力做增量优化：

- 上传文件从单文件放宽到最多 3 个文件。
- 上传类型新增 PDF，但仅承诺可复制文本型 PDF 的文本抽取，不接入 OCR。
- 保持当前“点击生成时再上传”的交互与后端单入口架构，不新增独立上传 API。
- 将新增 PDF 解析依赖打入后端 Docker 镜像并推送到阿里云 ACR。

## 产品与架构取舍

上传时机继续沿用“用户点击生成时再上传”。原因是当前后端没有独立上传 API，原始文件也不持久化；`POST /api/generations` 同时接收 prompt 和文件，并在创建任务前完成抽取与必要压缩。若改成“选择文件后立即上传”，需要新增临时文件存储、`file_id` 引用、过期清理、替换/取消/重试状态，范围明显更大。

PDF 支持按最小可用路径接入：复用 MarkItDown 的转换能力，新增 `markitdown[pdf]` extra。扫描件图片 PDF、加密 PDF 或无法抽取文本的 PDF 仍可能失败，错误文案会提示用户转换为可复制文本 PDF 或其他文档格式。

## 代码变更

- 前端 `code/frontend/app/page.tsx`
  - `MAX_FILE_COUNT` 从 1 调整为 3。
  - `<input type="file">` 增加 `multiple`。
  - 支持 `.pdf`，并增加单次总大小 50MB 的前端校验。
  - 文件展示 key 增加序号，避免同名同大小文件产生 React key 冲突。

- 后端 `code/backend/app/services/document_extractor.py`
  - `MAX_FILE_COUNT` 从 1 调整为 3。
  - `SUPPORTED_EXTENSIONS` 增加 `.pdf`。
  - 将 `_convert_office_to_markdown` 改为更通用的 `_convert_file_to_markdown`。
  - PDF 解析失败时补充扫描版/加密 PDF 的提示。

- 后端依赖 `code/backend/requirements.txt`
  - 从 `markitdown[docx,pptx,xlsx,xls]` 调整为 `markitdown[docx,pptx,xlsx,xls,pdf]`。
  - 实际新增 PDF 相关依赖包括 `pdfminer-six`、`pdfplumber`、`pypdfium2`。

## 部署与问题处理

前端 CSS 未加载的问题来自 Next standalone 运行方式：执行 `npm run build` 会重新生成 `.next/standalone`，可能清掉 standalone 目录中的 `.next/static`。当前生产前端仍由 `star-page-frontend.service` 运行，需要重启服务，让 `ExecStartPre` 重新复制 `.next/static` 和 `public`。

验证结果：

```text
/_next/static/chunks/0ez.1sxunq6r..css -> 200 text/css; charset=UTF-8 28123 bytes
```

PDF 初次上传时报“不支持 pdf”，原因是后端 uvicorn 旧进程尚未重启，仍加载旧白名单。处理方式：

1. 在后端 `.venv` 中安装更新后的 `requirements.txt`。
2. 重启 `star-page-backend.service`。
3. 用不支持后缀请求确认错误文案已经包含 `pdf`。
4. 运行后端上传校验，确认 3 个文件通过、4 个文件被拒绝。

## 后端镜像发布

为了让容器部署也包含 PDF 依赖，更新了 `code/backend/Dockerfile`：

- 增加 `PYTHON_IMAGE` 构建参数。
- 增加 `PIP_INDEX_URL` 与 `PIP_TRUSTED_HOST` 构建参数。
- 默认使用阿里云 PyPI 镜像源，避免容器内访问默认 PyPI 过慢。
- 新增 `code/backend/.dockerignore`，排除 `.venv`、缓存和本地环境文件。

构建过程中曾遇到默认 PyPI 下载速度很慢。第一次只在 `FROM` 前声明 `ARG PIP_INDEX_URL`，但该值没有进入构建阶段，`pip install` 仍走默认源。修复方式是在 `FROM` 后重新声明同名 `ARG`，再写入 `ENV`。

已推送到 ACR：

```text
crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page:backend-7d58e50-pdf
crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page:backend-latest
```

镜像 digest：

```text
sha256:b5c29c8c476e84962db0ff717a8ae0a642496c19419b4deb123e915c8ebd4970
```

镜像内验证：

```text
backend image pdf dependencies ok
```

## tag 约定

后端镜像保留两个 tag：

- `backend-<git 短 sha>` 或带说明的版本 tag：用于可追溯、回滚和问题排查。
- `backend-latest`：用于日常部署最新版本。

本次使用 `backend-7d58e50-pdf` 是因为改动尚未提交，短 SHA 仍是旧提交；追加 `-pdf` 能明确表达该镜像包含 PDF 支持。后续提交后应优先使用新的真实提交 SHA。

## 验证记录

- `npm run lint`：通过，仅有既有 `<img>` 性能警告。
- `npm run build`：通过。
- `python3 -m compileall app`：通过。
- 后端 `.venv` 上传校验：通过。
- 后端 Docker 镜像 PDF 依赖导入：通过。
- ACR push：`backend-7d58e50-pdf` 与 `backend-latest` 均成功。

