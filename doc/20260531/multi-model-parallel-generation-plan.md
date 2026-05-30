# 多模型并行生成实施计划（Scheme A：会话=生成树，Node=Page，合并另起新会话）

## 背景

当前产品是"自然语言生成 HTML 页面"，一次创建只调用单个模型、左侧展示一路生成过程、右侧单个 iframe 预览。

为了让用户在创建时能"货比三家"，需要支持：

- 新对话时可勾选多个模型（先支持 2 个，框架可扩展到更多）。
- 请求同时发给多个模型并行生成。
- 预览区同时展示多个模型结果，便于对比。
- 每个结果都有独立可分享链接。
- 兼顾未来：多轮续写、从任一结果分支继续、以及（预留）多结果合并。

## 关键设计决策

1. 生成结构升级为三层："会话(conversation)=一棵生成树" → "批次(batch)=一轮生成" → "节点(node)=某个模型的一个结果"。
2. 节点直接映射为现有 `Page`：每个模型结果是一个独立 `Page`，复用现有 `/p/{page_id}` 访问网关，天然拥有独立可分享链接。
3. 会话内"续写"永远单父，保证会话内是一棵严格的树，便于索引与导航。
4. 合并（多个节点融合）不在原会话内形成 DAG，而是"另起一个新会话作为根"，用根批次记录被合并的来源节点，保持每个会话始终是干净的树。本次仅预留 schema，不做端到端。
5. 多模型走"模型目录 + 多选"，默认只勾选 `qwen`，`doubao` 已注册可选；新增模型只改配置不改业务代码。

## 本次落地范围

- 端到端：新对话多选模型 → N 路并行生成 → 预览区并排对比 → 从任一结果节点"继续生成"（单父多轮）。
- 仅预留 schema：合并（多节点 → 新会话根）。
- 内部测试阶段，可丢弃老数据，无需兼容历史行。

## 数据模型（Scheme A）

```text
conversations 会话(树)
  └─ generation_batches 批次(一轮, 记录 base_page_id / kind / selected_models / 共享 prompt 与文件)
       └─ pages 节点(每模型一个, 独立可分享 /p/{page_id})
            ├─ generation_tasks 该节点的模型 run(model_key, 状态, token, 产物)
            │    └─ generation_events 该 run 的事件流(SSE 回放)
            └─ page_versions 产物版本(HTML 存 OSS)
```

- 新增 `conversations`：会话/生成树容器（owner、title、origin、root_batch_id、时间）。
- 新增 `generation_batches`：一轮生成，持有该轮共享数据（prompt、文件、抽取文本、压缩简报、选中模型）、`base_page_id`（续写基点，根轮为空）、`kind`（create/continue/merge_seed）、`source_page_ids`（合并预留）、轮状态。
- `pages` 升级为"节点"：增加 `conversation_id`、`batch_id`、`parent_page_id`（会话内单父）、`model_key/model_provider/model_name`；仍保留 `current_version_id`，`/p/{page_id}` 不变。
- `generation_tasks` 升级为"每节点的模型 run"：增加 `batch_id`、`model_key/model_provider/model_name`，共享 prompt/文件从批次冗余拷贝。
- `page_versions`：增加 `batch_id` 便于分组；产物的 provider/model/token 现在天然按模型记录。
- `generation_events` 不变，每个 run（task）独立事件流，支持断线重放。

迁移：新增 `code/backend/migrations/004_multi_model_tree.sql`，同步 ORM 实体。

## 模型目录与参数分层（方案A）

配置分三层落点，做到"加模型 / 调参数互不污染、密钥不进 Git"：

- 模型目录（非密钥、可提交可 review）：新增 `config/llm.models.json`，含 `defaults`（全局兜底参数）、`default_models`（默认勾选）、`models[]`（每模型 `key/label/protocol/base_url/model/api_key_env/params/extra_body`）。
- 密钥与基建参数（敏感/运维、gitignored）：沿用 `config/llm.env`，只放各模型 API Key（如 `QWEN_API_KEY`/`ARK_API_KEY`）与 `LLM_TIMEOUT_MS`/`LLM_RETRY_*` 等。
- `config/llm.env.example` 只保留变量名。

参数三层覆盖（就近优先）：`有效参数 = {...defaults, ...model.params}`，再把 `extra_body`（厂商专有，如 qwen 的 `enable_thinking`、doubao 的 `reasoning_effort`）合并进请求体最后一层。`params` 值为 `null` 表示显式不发该字段（doubao 系统固定 temperature/top_p 并忽略传入）。

可用性：密钥从 `api_key_env` 解析，缺失的模型自动不可选（未配 `ARK_API_KEY` 时 doubao 不出现在多选里，不影响 qwen 运行）。

## 后端服务与接口

- 生成服务：拆分为"建会话/批次/每模型节点"与"按 model_key 跑单节点 run"，新增按兄弟 run 重算批次状态；断线重放沿用现有逻辑。
- 接口：
  - `POST /api/generations` 新增 `models`（键列表）与续写参数（`conversation_id`/`base_page_id`），返回 `conversation_id/batch_id/runs[]`。
  - `GET /api/generations/{task_id}/events` 不变，前端按 run 开 N 路。
  - `GET /api/models` 返回模型目录（供前端动态渲染多选）。
  - `GET /api/conversations`、`GET /api/conversations/{id}` 支撑历史列表（按会话一条）与会话树恢复。
  - `/api/pages/{page_id}`、`/p/{page_id}` 保持不变。

## 前端

- 状态从单模型升级为"会话 + 多个 run"，每个 run 一份进度/思考/预览；按 run 开 N 路 SSE。
- 新对话输入卡内增加模型多选（从 `GET /api/models` 渲染，默认勾默认键）。
- 左侧：共享需求 + 多模型进度列表，点选某模型展开其思考与创建节点。
- 右侧：N 个"浏览器视窗"单元并排对比（复用固定 1200px 视口缩放），响应式网格 + 单元聚焦放大，每单元独立打开/复制链接。
- 多轮续写：结果单元"以此结果继续"，新批次结果接续展示。
- 历史与会话恢复改为基于会话接口。

## 风险与注意点

- N 路 SSE 受 HTTP/1.1 单源约 6 连接限制：默认 2 个无碍；模型变多后再升级"批次多路复用 SSE"或轮询。
- 每路 run 用独立 DB session；批次状态重算幂等，轻微竞态可接受。
- 安全（CSP/HTML 清洗）、固定视口预览方案沿用现有实现。

## 沉淀计划

- 实现后更新：`wiki/llm-provider-abstraction.md`（模型目录与参数分层）、新增"生成树/分支会话"与"多模型预览对比" wiki、`code/README.md`、`config/README.md`。
