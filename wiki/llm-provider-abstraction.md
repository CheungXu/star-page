# LLM Provider 抽象原则

## 核心思路

大模型厂商很多，但文本生成 API 的主流请求格式可以先按协议族归类，而不是按厂商在业务层逐一分支。

早期可优先支持：

- OpenAI Chat Completions 兼容格式。
- Anthropic Messages 兼容格式。

业务层只依赖统一接口，例如 `streamText()` / `completeText()`，不直接依赖具体厂商 SDK、URL 或响应结构。

## 配置维度

新增模型供应商时，优先通过配置表达差异：

- `provider`：供应商标识，例如 `openai`、`qwen`、`deepseek`、`anthropic`。
- `protocol`：协议族，例如 `openai` 或 `anthropic`。
- `baseUrl`：API 基础地址。
- `model`：模型名称。
- `apiKey`：运行时注入的密钥。
- `extraBody`：同一协议族下的厂商扩展字段，例如 Qwen 的 `enable_thinking`。

只有当供应商协议明显不兼容现有协议族时，才新增适配器。

## Qwen / 百炼接入

阿里云百炼 Qwen 可按 OpenAI-compatible 协议接入：

- `provider=qwen`
- `protocol=openai`
- `baseUrl=https://dashscope.aliyuncs.com/compatible-mode/v1`
- `apiKey` 使用 DashScope / 百炼 API Key

Qwen 的深度思考能力不应写死在业务层，可通过配置把 `enable_thinking=true` 合并到 OpenAI-compatible 请求体中。

流式响应中的 `reasoning_content` 应在适配层转换为统一的思考过程增量，普通 `content` 转换为回复文本增量。

## 业务边界

业务代码应传入统一消息结构：

- `system`：系统指令。
- `user`：用户输入。
- `assistant`：历史助手回复。

适配器负责转换成目标协议格式。例如 Anthropic 协议需要把 `system` 拆到顶层字段，OpenAI 协议则直接放在 `messages` 中。

## 重试与空输出

LLM 适配层应提供统一重试能力，而不是让每个业务调用点各自实现：

- 可重试错误包括网络失败、超时、远端协议中断、JSON 解析失败、`429`、`408` 和常见 `5xx`。
- 重试次数和退避时间应通过配置控制，例如 `LLM_RETRY_ATTEMPTS`、`LLM_RETRY_INITIAL_DELAY_MS`、`LLM_RETRY_MAX_DELAY_MS`。
- 非流式任务可以封装为 `completeText(requireContent=True)`；如果模型只返回 reasoning、没有正式 content，应视为可重试空输出。
- 流式任务要区分“开始输出前失败”和“开始输出后失败”。开始输出前可以整体重试；开始输出后不应自动重试拼接，避免重复内容或半截 HTML。

## 密钥处理

- API Key 不写入仓库。
- 示例文件只保留变量名。
- 构建镜像时不注入生产密钥。
- 生产密钥通过服务器 `.env`、部署平台 Secret 或云厂商密钥服务在运行时注入。

## 多模型目录与参数三层覆盖

当需要"同一产品里并行/可切换多个模型"时，不要把每个模型写死在业务层或塞进一堆扁平 env，而是用"可提交的模型目录 + 仅密钥的 env + 参数三层覆盖"：

- 模型目录（非密钥，可提交可 review）：一个 JSON 文件（如 `config/llm.models.json`），描述 `defaults`（全局兜底参数）、`default_models`（默认勾选，可多项以展示多模型并行）、`models[]`（每模型 `key/label/provider/protocol/base_url/model/api_key_env/params/extra_body`）。前端首访无 `localStorage` 时勾选全部 `is_default` 模型。
- 密钥与基建参数（敏感/运维，gitignored）：只放各模型 API Key（变量名由目录里的 `api_key_env` 指定，如 `QWEN_API_KEY`/`ARK_API_KEY`）与 `LLM_TIMEOUT_MS`/`LLM_RETRY_*`。
- 密钥从 `api_key_env` 解析，缺失的模型自动标记为不可用（前端多选里不出现，不崩）；可设 `api_key_fallback_env` 兼容旧单模型变量（如 qwen 回退 `LLM_API_KEY`）。

参数三层覆盖（就近优先）：`有效参数 = {...defaults, ...model.params}`，再把 `extra_body` 合并进请求体最后一层（最高优先）。

- `params`：标准生成参数（temperature/top_p/max_tokens/penalties/stop），映射到 OpenAI 标准 body 字段，有全局默认、可被每模型覆盖。
- `extra_body`：厂商专有透传（qwen `enable_thinking`、doubao `reasoning_effort`，以及未来新字段），无 schema、不改代码即可新增。
- `params` 值为 `null` = 显式不发该字段；适用于"系统固定并忽略传入"的参数（如 doubao 固定 temperature/top_p）或会拒绝未知参数的模型。

业务层只按 `model_key` 取目录配置构建客户端，N 个模型并行就是 N 个独立客户端调用，互不影响。

### 火山方舟 doubao 接入

doubao（火山方舟 ARK）同样按 OpenAI-compatible 协议接入：

- `base_url=https://ark.cn-beijing.volces.com/api/v3`、`api_key_env=ARK_API_KEY`。
- Pro：`model=doubao-seed-2-0-pro-260215`（目录 key `doubao`）。
- Code：`model=doubao-seed-2-0-code-preview-260215`（目录 key `doubao-code`）。**不能**写 `doubao-seed-2-0-code`，方舟会返回 `InvalidEndpointOrModel.NotFound`。
- 思考程度用 `reasoning_effort`（minimal/low/medium/high）经 `extra_body` 传；流式响应里 `reasoning_content` 仍按统一思考增量解析，`content` 按正文增量解析，无需改适配层。

### 模型 ID 与「最新版」命名（2026-06 实测）

百炼 / 方舟的 `model` 字段需与控制台**精确一致**，不能凭产品名猜测缩写：

| 目录 key | 产品名 | 可用 model ID | 备注 |
| --- | --- | --- | --- |
| `qwen` | 通义千问 3.7 Max | `qwen3.7-max` | 可直接用，无需日期后缀 |
| `qwen-plus` | 通义千问 3.7 Plus | `qwen3.7-plus` | 可直接用，无需日期后缀 |
| `doubao` | 豆包 Seed 2.0 Pro | `doubao-seed-2-0-pro-260215` | 需带发布日期后缀 |
| `doubao-code` | 豆包 Seed 2.0 Code | `doubao-seed-2-0-code-preview-260215` | 需 `preview` + 日期后缀；`doubao-seed-2-0-code` 不可用 |

Qwen 侧 Max / Plus 已支持「裸名」最新版；Doubao 侧 Pro / Code 目前仍要带 `-260215`（及 Code 的 `preview` 前缀），后续若方舟开放无后缀别名，只需改 `config/llm.models.json` 的 `model` 字段。

### 模型列表与官方定价 API（2026-06 实测）

| 厂商 | 模型列表 | 价格 | 鉴权 |
| --- | --- | --- | --- |
| 百炼 Qwen | `GET /api/v1/models?page_no=&page_size=` | 同接口 `prices` 字段 | Bearer API Key |
| 百炼 Qwen | `GET /compatible-mode/v1/models` | 无，仅 ID | Bearer API Key |
| 火山方舟 | `GET /api/v3/models` | 无 | Bearer ARK API Key |

百炼可用 `https://dashscope.aliyuncs.com/api/v1/models` 拉取模型目录与实时单价；方舟推理 API **不返回价格**，需维护 `pricing` 配置或对照[官方定价页](https://www.volcengine.com/docs/82379/1544106)。

### Token 用量与费用自算

OpenAI-compatible 流式响应在 `stream_options.include_usage=true` 时，末 chunk 的 `usage` 含：

- `prompt_tokens` / `completion_tokens` / `total_tokens`
- `prompt_tokens_details.cached_tokens`（缓存命中输入）
- `completion_tokens_details.reasoning_tokens`（思考 token，已计入 `completion_tokens`）

**接口不返回 `cost` 字段**，需在业务层按目录 `pricing.tiers` 自算：

```text
输入费用 = (prompt_tokens - cached_tokens) × 输入单价/1e6 + cached_tokens × 缓存单价/1e6
输出费用 = completion_tokens × 输出单价/1e6
```

分档规则：`tiers[]` 按 `max_input_tokens` 升序，用 **prompt_tokens** 选档（Doubao Seed 2.0 Pro/Code 三档：≤32K / 32K–128K / 128K–256K；Qwen Plus 两档：≤256K / 256K–1M）。

实现落点：

- 定价配置：`config/llm.models.json` 每模型 `pricing.tiers[]`（`label` / `max_input_tokens` / `input_per_million` / `output_per_million` / 可选 `cache_input_per_million`）。
- 计算：`code/backend/app/services/llm/cost.py` 的 `estimate_llm_cost(model_key, usage)`。
- 持久化：`page_versions` 的 token 与 `*_cost_cny` 字段（迁移 `008_llm_usage_cost.sql`）。
- 展示：SSE `progress`（model_output 完成）与 `completed` 携带 `usage` + `cost`；前端进度区下方摘要卡片。
- 恢复：会话详情接口也应从当前 `page_versions` 返回 `usage` + `cost`，否则刷新页面或从历史进入时，前端只能恢复页面链接，无法恢复 token 与费用摘要。

官方调价时只改 `llm.models.json` 的 `pricing`，无需改代码。

## 演进建议

MVP 先实现非流式文本生成。后续按真实需求逐步扩展：

- 更完整的流式输出协议支持。
- 图片和多模态输入（doubao 支持 `image_url` 内容块，后续做多模态时可复用其 OpenAI 兼容格式）。
- 工具调用。
- provider 级超时、重试、限流和熔断。
- 延迟监控；成本已支持单次请求 token + 自算费用展示，后续可接账单对账与聚合统计。
