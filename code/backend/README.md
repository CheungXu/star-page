# FastAPI 后端

后端负责页面生成主流程：默认用户、数据库、LLM 流式调用、OSS 存取、HTML 清洗、SSE 事件、上传文件内容抽取和 `/p/{page_id}` 页面访问网关。

## 本地运行

```bash
cd code/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m app.db.migrate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

真实配置从环境变量读取，也会在本地开发时尝试读取仓库 `config/*.env`。密钥文件不要提交。

## Docker 镜像

后端镜像推送到阿里云 ACR 应用仓库：

- 镜像仓库：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page`
- tag 约定：`backend-<git 短 sha>` 或带变更说明的版本 tag，另用 `backend-latest` 指向最新后端镜像。

```bash
cd code/backend
ACR=crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page
SHA=$(git rev-parse --short HEAD)
docker build -t "$ACR:backend-$SHA" -t "$ACR:backend-latest" .
docker push "$ACR:backend-$SHA"
docker push "$ACR:backend-latest"
```

`Dockerfile` 默认使用阿里云 PyPI 镜像源安装依赖，避免构建时访问默认 PyPI 过慢；如需切换源，可通过 `--build-arg PIP_INDEX_URL=... --build-arg PIP_TRUSTED_HOST=...` 覆盖。

如果迁移时报 `permission denied for schema public`，说明当前 RDS 应用账号只有连接权限、没有建表权限。请先用高权限账号参考 `script/prepare_rds_database.sql` 创建业务库并授权，再重新执行迁移。

服务器上已通过 `star-page-backend.service` 常驻运行：

```bash
systemctl restart star-page-backend.service
journalctl -u star-page-backend.service -f
```

## 关键接口

- `POST /api/generations`：创建页面生成任务。
- `GET /api/generations/{task_id}/events`：通过 SSE 推送思考过程和完成状态。
- `GET /api/pages`：查询当前默认测试用户可访问的页面历史列表，供左侧历史创建跨设备使用；后续接入真实登录后替换当前用户来源。
- `GET /api/pages/{page_id}`：获取页面元数据。
- `GET /p/{page_id}`：页面访问网关，从私有 OSS 读取 HTML 并返回。

`POST /api/generations` 同时兼容 JSON 和 `multipart/form-data`。上传文件时表单字段为：

- `prompt`：用户页面需求。
- `files`：当前最多允许 3 个文件，单文件和单次总大小均不超过 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`pdf`、`txt`、`md`、`html`。后端会抽取为 Markdown/文本并合并到 LLM 上下文；PDF 仅保证可复制文本内容的抽取，扫描版图片 PDF 或加密 PDF 可能解析失败。

如果抽取文本超过 5000 字符，后端会先调用 LLM 将资料压缩为面向页面生成的任务简报，再把压缩后的资料放入最终生成 prompt。

Nginx 入口需要同步放开上传体积限制，当前示例配置为 `client_max_body_size 60m`，用于覆盖 50MB 单次上传和 multipart 开销。

为方便后续排查，`generation_tasks` 会记录：

- 用户原始 prompt。
- 上传文件名列表。
- 文件抽取文本。
- 触发压缩时使用的压缩 prompt 说明。
- 最终输入页面生成模型的 prompt。
- 模型原始输出文本，便于排查 HTML 提取或清洗导致的内容丢失。
- 生成 HTML 上传后的 OSS 调试定位信息。

LLM 调用默认带重试机制，配置项为 `LLM_RETRY_ATTEMPTS`、`LLM_RETRY_INITIAL_DELAY_MS`、`LLM_RETRY_MAX_DELAY_MS`。底层客户端会重试可恢复的网络、超时、限流和 5xx 错误；资料压缩会对“调用成功但正文为空”的情况重试；页面生成会在正式 HTML 输出开始前失败或空输出时重试。

## 生成过程事件

`GET /api/generations/{task_id}/events` 使用 SSE 推送以下事件：

- `status`：普通状态文案。
- `reasoning_delta`：模型流式返回的 `reasoning_content`。
- `answer_started`：模型进入正式 HTML 输出阶段，前端开始展示“页面创建中”。
- `progress`：创建节点状态，目前包含 `upload_file`、`parse_file`、`compress_document`、`model_thinking`、`model_output`、`deploy`。
- `completed`：页面已上传 OSS、数据库已更新，返回可访问链接。
- `failed`：生成失败。

`model_thinking` 节点承载模型 reasoning 内容，前端默认展开且支持收起；`model_output` 节点会优先使用模型返回的真实 `completion_tokens`，生成中没有真实 usage 时，前端先展示估算 token 数。

## 当前默认配置

- 默认测试用户：`default_test`。
- 页面默认权限：`public`，但 OSS Bucket 仍保持私有，访问统一走 `/p/{page_id}` 网关。
- 业务数据库：`stars_page`。
- 当前 Qwen 配置：`LLM_PROVIDER=qwen`、`LLM_PROTOCOL=openai`、`LLM_MODEL=qwen3.7-max`。
