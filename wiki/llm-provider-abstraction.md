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

- 模型目录（非密钥，可提交可 review）：一个 JSON 文件（如 `config/llm.models.json`），描述 `defaults`（全局兜底参数）、`default_models`（默认勾选）、`models[]`（每模型 `key/label/provider/protocol/base_url/model/api_key_env/params/extra_body`）。
- 密钥与基建参数（敏感/运维，gitignored）：只放各模型 API Key（变量名由目录里的 `api_key_env` 指定，如 `QWEN_API_KEY`/`ARK_API_KEY`）与 `LLM_TIMEOUT_MS`/`LLM_RETRY_*`。
- 密钥从 `api_key_env` 解析，缺失的模型自动标记为不可用（前端多选里不出现，不崩）；可设 `api_key_fallback_env` 兼容旧单模型变量（如 qwen 回退 `LLM_API_KEY`）。

参数三层覆盖（就近优先）：`有效参数 = {...defaults, ...model.params}`，再把 `extra_body` 合并进请求体最后一层（最高优先）。

- `params`：标准生成参数（temperature/top_p/max_tokens/penalties/stop），映射到 OpenAI 标准 body 字段，有全局默认、可被每模型覆盖。
- `extra_body`：厂商专有透传（qwen `enable_thinking`、doubao `reasoning_effort`，以及未来新字段），无 schema、不改代码即可新增。
- `params` 值为 `null` = 显式不发该字段；适用于"系统固定并忽略传入"的参数（如 doubao 固定 temperature/top_p）或会拒绝未知参数的模型。

业务层只按 `model_key` 取目录配置构建客户端，N 个模型并行就是 N 个独立客户端调用，互不影响。

### 火山方舟 doubao 接入

doubao（火山方舟 ARK）同样按 OpenAI-compatible 协议接入：

- `base_url=https://ark.cn-beijing.volces.com/api/v3`、`model=doubao-seed-2-0-pro-260215`、`api_key_env=ARK_API_KEY`。
- 思考程度用 `reasoning_effort`（minimal/low/medium/high）经 `extra_body` 传；流式响应里 `reasoning_content` 仍按统一思考增量解析，`content` 按正文增量解析，无需改适配层。

## 演进建议

MVP 先实现非流式文本生成。后续按真实需求逐步扩展：

- 更完整的流式输出协议支持。
- 图片和多模态输入（doubao 支持 `image_url` 内容块，后续做多模态时可复用其 OpenAI 兼容格式）。
- 工具调用。
- provider 级超时、重试、限流和熔断。
- 成本、token 用量和延迟监控。
