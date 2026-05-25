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

如果迁移时报 `permission denied for schema public`，说明当前 RDS 应用账号只有连接权限、没有建表权限。请先用高权限账号参考 `script/prepare_rds_database.sql` 创建业务库并授权，再重新执行迁移。

服务器上已通过 `star-page-backend.service` 常驻运行：

```bash
systemctl restart star-page-backend.service
journalctl -u star-page-backend.service -f
```

## 关键接口

- `POST /api/generations`：创建页面生成任务。
- `GET /api/generations/{task_id}/events`：通过 SSE 推送思考过程和完成状态。
- `GET /api/pages/{page_id}`：获取页面元数据。
- `GET /p/{page_id}`：页面访问网关，从私有 OSS 读取 HTML 并返回。

`POST /api/generations` 同时兼容 JSON 和 `multipart/form-data`。上传文件时表单字段为：

- `prompt`：用户页面需求。
- `files`：当前只允许 1 个文件，最大 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`txt`、`md`、`html`。后端会抽取为 Markdown/文本并合并到 LLM 上下文。

如果抽取文本超过 5000 字符，后端会先调用 LLM 将资料压缩为面向页面生成的任务简报，再把压缩后的资料放入最终生成 prompt。

Nginx 入口需要同步放开上传体积限制，当前示例配置为 `client_max_body_size 60m`，用于覆盖 50MB 单文件上传和 multipart 开销。

为方便后续排查，`generation_tasks` 会记录：

- 用户原始 prompt。
- 上传文件名列表。
- 文件抽取文本。
- 触发压缩时使用的压缩 prompt 说明。
- 最终输入页面生成模型的 prompt。
- 生成 HTML 上传后的 OSS 调试定位信息。

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
