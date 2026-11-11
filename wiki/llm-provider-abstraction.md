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
- Pro：`model=doubao-seed-2-1-pro-260915`（目录 key `doubao`）。需带发布日期后缀；`260628` 仍可用，当前用较新的 `260915`。
- Turbo：`model=doubao-seed-2-1-turbo-260628`（目录 key `doubao-code`）。2026-10 没有 Seed 2.1 Code，这个槽改挂 Turbo。
- 思考程度用 `reasoning_effort`（minimal/low/medium/high）经 `extra_body` 传；流式响应里 `reasoning_content` 仍按统一思考增量解析，`content` 按正文增量解析，无需改适配层。

### 模型 ID 与「最新版」命名（2026-10 实测）

百炼 / 方舟的 `model` 字段需与控制台**精确一致**，不能凭产品名猜测缩写：

| 目录 key | 产品名 | 可用 model ID | 备注 |
| --- | --- | --- | --- |
| `qwen` | 通义千问 3.8 Max | `qwen3.8-max` | 裸名即最新版；快照如 `qwen3.8-max-0902` |
| `qwen-plus` | 通义千问 3.8 Flash | `qwen3.8-flash` | 目录 key 仍为 `qwen-plus`；没有 3.8 Plus |
| `doubao` | 豆包 Seed 2.1 Pro | `doubao-seed-2-1-pro-260915` | 需日期后缀 |
| `doubao-code` | 豆包 Seed 2.1 Turbo | `doubao-seed-2-1-turbo-260628` | 目录 key 仍为 `doubao-code` |

Qwen Max 用裸名最新版；Doubao 仍要带日期后缀。方舟开放无后缀别名后，只改 `config/llm.models.json` 的 `model` 字段。

### DeepSeek / GLM / Kimi（百炼主路，2026-10 更新）

DeepSeek、GLM 旗舰、Kimi 的生产主路走阿里云百炼（与 Qwen 共用 `QWEN_API_KEY`）。2026-11 的 GLM-5.3 Flash 是例外：百炼原厂直供未开通，改走方舟。完整定价与灾备见 `doc/20260614/domestic-llm-pricing-and-integration.md`，当次换代见 `doc/20261110/llm-model-catalog-update.md`。

| 目录 key | model ID | extra_body 要点 |
| --- | --- | --- |
| `deepseek-v4.1-flash` | `deepseek-v4.1-flash-1017` | `enable_thinking` + `reasoning_effort: high`。2026-11 起用 1017 快照，目录 key 仍是 `deepseek-v4.1-flash` |
| `deepseek-v4-pro` | `deepseek-v4-pro` | 同上；百炼裸名与 `deepseek-v4-pro-0813` 是两个在售 ID，价格不同，槽位继续用裸名 |
| `glm-5.3` | `glm-5.3` | `enable_thinking: true` |
| `glm-5.3-flash` | `glm-5-3-flash-260828` | 方舟。`reasoning_effort: high`。百炼 `ZHIPU/GLM-5.3-Flash` 产品未开通，不要写进目录 |
| `kimi-k3.1-pro` | `kimi-k3.1-pro` | `enable_thinking: true`，且 `temperature` 必须省略。2026-11 从 `kimi-k3` 换代，旧 key 只留在计费倍率里 |

百炼控制台需分别开通各模型服务。流式 `reasoning_content` 仍由现有 OpenAI 适配层解析，无需改代码。

**灾备**（百炼故障时只改目录三项）：DeepSeek → `https://api.deepseek.com`；GLM → `https://open.bigmodel.cn/api/paas/v4`；Kimi → `https://api.moonshot.cn/v1`；MiniMax → `https://api.minimaxi.com/v1`。灾备 Key 变量名见 `config/llm.env.example`。

**注意**：Coding Plan / Kimi Code / MiniMax Token Plan 的 endpoint 与 Key **不能**用于星页服务端生成。

### 百炼托管第三方模型的 model ID 坑（2026-06 实测）

同一账号在百炼上，**带厂商前缀的 ID 与裸名 ID 可能对应不同产品开通状态**：

| 模型 | ❌ 易错 ID | ✅ 推荐 ID |
| --- | --- | --- |
| Kimi | `kimi/kimi-k3.1-pro`（厂名前缀，和裸名可能开通状态不同） | `kimi-k3.1-pro` |
| GLM | `ZHIPU/GLM-5.3-Flash`（产品未开通）、`glm-5.3-flash`（Access denied） | 旗舰 `glm-5.3`；Flash 走方舟 `glm-5-3-flash-260828` |
| DeepSeek | 把 `deepseek-v4.1-flash` 当成 1017 | Flash 用 `deepseek-v4.1-flash-1017`；Pro 槽位仍是 `deepseek-v4-pro` |
| MiniMax | `MiniMax-M3`（404） | `MiniMax/MiniMax-M3`（需先控制台开通产品） |

接入后务必用 `script/probe-llm-models.py` 或最小 chat 请求验证，勿凭文档前缀猜测。

### 模型换代检查清单（2026-10）

换一代模型时，先对清单再改生产目录。价格和开通状态以当时接口为准，不要沿用上一季文档里的 ID。

1. **列目录再探活。** 百炼 `GET /api/v1/models` 的成功包在 `output.models`，不在 OpenAI 风格的 `data`；单价在每条的 `prices[].prices[]`。方舟 `GET /api/v3/models` 只有 ID 和状态（如 `Retiring` / `Shutdown`），**不返回价格**，单价要另查官方价目。列表里的 `inference_provider` 能区分平台托管（`aliyun-bailian`）和原厂直供（如 `zhipu-ai`）。
2. **未开通有两层，不要和 404 混。** `does not exist` 才是 ID 写错或已下架。`The product is not activated` 是商品未开通；`Access denied` 是另一类拒绝。业务空间授权 `POST /api/v1/models/permissions` 返回 success，只表示空间权限写上了，**不等于商品已开通**，必须再探一次最小对话。未开通的 ID 不要写进生产。原厂直供开不通、方舟又有同款且探活能吐出正文时，可以改走方舟。
3. **思考模型的空正文。** 探活若把 `max_tokens` 压到十几，模型可能把额度用在思考上，HTTP 200 但正文为空。加大额度再确认能吐出正文，才算探活通过。
4. **key 与展示名分开。** 版本写在目录 key 里（如 `glm-5.2`）就随版本改 key，并在计费倍率里暂时保留旧 key，避免更新瞬间未结算的调用找不到倍率；浏览器里勾过旧 key 的需要重选。槽位角色不变（更便宜的那一档、第二个豆包）就保留 key，只改 `label` 和 `model`，已勾选的用户会自动跟上。
5. **高峰 / 低峰。** 厂商给两套单价、计费引擎又没有时段时，按高峰写入 `pricing`，避免高峰少计成本。档位 `label` 写明「低峰减半」，方便以后对账。
6. **裸名与日期快照。** `qwen3.8-max` 这类裸名是滚动最新版，带日期的是快照，不要把快照当成更新。豆包必须带日期后缀，同名系列取较新日期。百炼上若裸名和日期快照**同时在售且单价不同**，它们是两个商品，不要把快照当成裸名已经滚过去的版本，也不要反过来把裸名当成那个快照。2026-11 的例子：`deepseek-v4-pro` 是 12 / 24 / 1，`deepseek-v4-pro-0813` 高峰是 9 / 27 / 0.9。脚本若按字典序猜「更新 ID」，会把 omni、带厂名前缀的 ID误报成更新，以探活和产品档位为准。
7. **生效范围。** 目录在后端启动时加载，改完重启后端即可。展示名来自模型列表接口，前端不用重新构建。现网以公网 `/api/models` 和本机 `127.0.0.1:8000/api/models` 一致为准。key 没变的档，已勾选用户会自动跟上；版本写进 key 的档，旧勾选不会迁移。
8. **思考/推理模型的参数禁忌（如 Kimi K3 不接受 temperature）。**
   部分开启深度思考的新一代模型（如百炼托管的 `kimi-k3.1-pro`，报错文案仍写 `kimi-k3`），供应商上游严格禁止传通用参数 `temperature`（传入默认的 0.7 即报 `400 InvalidParameter: Parameter 'temperature'=0.7 is not supported`）。必须在模型目录中配置 `"params": { "temperature": null }` 显式由适配层丢弃该参数，不能直接依赖默认模板参数。

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

分档规则：`tiers[]` 按 `max_input_tokens` 升序，用 **prompt_tokens** 选档。Seed 2.1 Pro / Turbo 按公开价单档计费。

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
- 图片和多模态输入：已按 OpenAI `image_url` 内容块接入（`LlmMessage.images`，data URL）。模型目录用 `supports_vision` 标明能否读图，不能读的在适配层丢图；带图请求在输出前返回 400 时去图重试一次。能力必须实测，见 `uploaded-document-ingestion-for-generation.md` 第 5 节。
- 工具调用。
- provider 级超时、重试、限流和熔断。
- 延迟监控；成本已支持单次请求 token + 自算费用展示，后续可接账单对账与聚合统计。
