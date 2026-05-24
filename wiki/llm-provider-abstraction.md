# LLM Provider 抽象原则

## 核心思路

大模型厂商很多，但文本生成 API 的主流请求格式可以先按协议族归类，而不是按厂商在业务层逐一分支。

早期可优先支持：

- OpenAI Chat Completions 兼容格式。
- Anthropic Messages 兼容格式。

业务层只依赖统一接口，例如 `generateText()`，不直接依赖具体厂商 SDK、URL 或响应结构。

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

## 密钥处理

- API Key 不写入仓库。
- 示例文件只保留变量名。
- 构建镜像时不注入生产密钥。
- 生产密钥通过服务器 `.env`、部署平台 Secret 或云厂商密钥服务在运行时注入。

## 演进建议

MVP 先实现非流式文本生成。后续按真实需求逐步扩展：

- 更完整的流式输出协议支持。
- 图片和多模态输入。
- 工具调用。
- provider 级超时、重试、限流和熔断。
- 成本、token 用量和延迟监控。
