# LLM 多模型扩展与用量费用展示 — 实施记录

日期：2026-06-07

## 背景

1. 补充接入 Qwen 3.7 Plus、Doubao Seed 2.0 Code。
2. 验证各模型「最新版」model ID 是否可直接调用。
3. 每次生成完成后展示精确输入/输出 token 与花费。

## 结论摘要

### 模型 ID（实测可通）

| 目录 key | model ID | 备注 |
| --- | --- | --- |
| qwen | qwen3.7-max | 裸名可用 |
| qwen-plus | qwen3.7-plus | 裸名可用 |
| doubao | doubao-seed-2-0-pro-260215 | 需日期后缀 |
| doubao-code | doubao-seed-2-0-code-preview-260215 | 不能写 doubao-seed-2-0-code |

### 定价数据来源

- **百炼**：`GET /api/v1/models` 可拉模型+价格；推理响应无 cost 字段。
- **方舟**：`GET /api/v3/models` 仅有模型元数据；价格需对照官方文档，在 `llm.models.json` 维护 `pricing`。

### 费用计算

接口返回 `usage`，业务层按 `pricing.tiers` 分档自算；结果经 SSE 推送并写入 `page_versions`。

## 实施内容

- `config/llm.models.json`：新增 qwen-plus、doubao-code 及四模型 pricing。
- `app/services/llm/cost.py`：费用估算与 SSE 序列化。
- 迁移 `008_llm_usage_cost.sql`：page_versions 增加 token 明细与费用字段。
- 前端：生成完成后展示用量与费用摘要卡片；刷新或从历史进入时，通过会话详情接口恢复 token 与费用展示。

## 问题修复：刷新后 token/费用展示丢失

现象：生成完成时，进度节点能看到「输入/输出 tokens」和费用摘要；刷新页面或从历史重新进入后，相关展示消失。

根因：

- 实时生成路径依赖 SSE `progress` / `completed` 事件中的 `usage` 与 `cost`，前端据此写入 `RunState.usageSummary`。
- 刷新/历史恢复路径依赖 `/api/conversations/{conversation_id}` 会话详情接口重建 `RunState`。
- 数据库 `page_versions` 已保存 token 与费用字段，但会话详情接口没有返回这些字段，前端恢复时无法重建 `usageSummary`。

修复：

- `ConversationNode` 增加 `usage`、`cost` 可选字段。
- 会话详情接口按页面 `current_version_id` 查询 `PageVersion`，把已落库的 token/费用挂到对应节点。
- 前端 `buildSessionFromDetail` 恢复时重建 `usageSummary`，并把 `model_output` 进度节点的输出 token 标记为实际值。

## 部署

- 执行 `python -m app.db.migrate`（含 008）。
- 前端需 `npm run build` 后 `systemctl restart star-page-backend star-page-frontend`。

## 后续

- 百炼 pricing API 定时同步脚本（可选）。
- 账单对账与按用户/会话聚合统计。
