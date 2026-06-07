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
- 前端：生成完成后展示用量与费用摘要卡片。

## 部署

- 执行 `python -m app.db.migrate`（含 008）。
- 前端需 `npm run build` 后 `systemctl restart star-page-backend star-page-frontend`。

## 后续

- 历史会话 reload 时从 API 回显用量（当前仅 SSE 实时展示）。
- 百炼 pricing API 定时同步脚本（可选）。
- 账单对账与按用户/会话聚合统计。
