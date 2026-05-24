# FastAPI 后端

后端负责页面生成主流程：默认用户、数据库、LLM 流式调用、OSS 存取、HTML 清洗、SSE 事件和 `/p/{page_id}` 页面访问网关。

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

## 关键接口

- `POST /api/generations`：创建页面生成任务。
- `GET /api/generations/{task_id}/events`：通过 SSE 推送思考过程和完成状态。
- `GET /api/pages/{page_id}`：获取页面元数据。
- `GET /p/{page_id}`：页面访问网关，从私有 OSS 读取 HTML 并返回。

## 生成过程事件

`GET /api/generations/{task_id}/events` 使用 SSE 推送以下事件：

- `status`：普通状态文案。
- `reasoning_delta`：模型流式返回的 `reasoning_content`。
- `answer_started`：模型进入正式 HTML 输出阶段，前端开始展示“页面创建中”。
- `progress`：创建节点状态，目前包含 `model_output`、`upload`、`database`。
- `completed`：页面已上传 OSS、数据库已更新，返回可访问链接。
- `failed`：生成失败。

`model_output` 节点会优先使用模型返回的真实 `completion_tokens`；生成中没有真实 usage 时，前端先展示估算 token 数。

## 当前默认配置

- 默认测试用户：`default_test`。
- 页面默认权限：`public`，但 OSS Bucket 仍保持私有，访问统一走 `/p/{page_id}` 网关。
- 业务数据库：`stars_page`。
- 当前 Qwen 配置：`LLM_PROVIDER=qwen`、`LLM_PROTOCOL=openai`、`LLM_MODEL=qwen3.7-max`。
