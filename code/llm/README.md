# LLM 请求适配层

## 目标

业务代码只依赖统一的 `LlmClient` 接口，不直接绑定某一家模型厂商。

当前先支持两类主流请求协议：

- `openai`：OpenAI Chat Completions 兼容格式。
- `anthropic`：Anthropic Messages 兼容格式。

后续接入其他厂商时，优先判断其 API 兼容哪一种协议，再新增配置；只有协议差异明显时才新增 provider 实现。

## 使用示例

```ts
import { createLlmClientFromEnv } from "./llm";

const llm = createLlmClientFromEnv();

const result = await llm.generateText({
  messages: [
    { role: "system", content: "你是一个 HTML 页面生成助手。" },
    { role: "user", content: "生成一个产品介绍页。" },
  ],
  temperature: 0.7,
  maxTokens: 4096,
});

console.log(result.text);
```

## 流式输出示例

Qwen 等 OpenAI-compatible 后端可能在流式响应中返回 `reasoning_content`。统一接口会将其转换为 `reasoning_delta`，普通回复内容转换为 `text_delta`。

```ts
import { createLlmClientFromEnv } from "./llm";

const llm = createLlmClientFromEnv();
let isAnswering = false;

console.log("\n====================思考过程====================");

for await (const chunk of llm.streamText({
  messages: [{ role: "user", content: "你是谁" }],
  maxTokens: 4096,
})) {
  if (chunk.type === "reasoning_delta" && chunk.reasoningText) {
    process.stdout.write(chunk.reasoningText);
  }

  if (chunk.type === "text_delta" && chunk.text) {
    if (!isAnswering) {
      console.log("\n====================完整回复====================");
      isAnswering = true;
    }

    process.stdout.write(chunk.text);
  }
}
```

## 环境变量

```text
LLM_PROVIDER=openai
LLM_PROTOCOL=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini
LLM_API_KEY=
LLM_TIMEOUT_MS=60000
LLM_ANTHROPIC_VERSION=2023-06-01
LLM_ENABLE_THINKING=
LLM_OPENAI_EXTRA_BODY_JSON=
```

字段说明：

- `LLM_PROVIDER`：业务侧识别用的供应商名称，例如 `openai`、`deepseek`、`qwen`、`anthropic`。
- `LLM_PROTOCOL`：请求协议，目前只支持 `openai` 或 `anthropic`。
- `LLM_BASE_URL`：模型 API 基础地址。OpenAI 协议会请求 `/chat/completions`，Anthropic 协议会请求 `/v1/messages`。
- `LLM_MODEL`：模型名称。
- `LLM_API_KEY`：真实密钥，只能放在真实环境变量文件或部署平台密钥中。
- `LLM_TIMEOUT_MS`：请求超时时间。
- `LLM_ANTHROPIC_VERSION`：Anthropic 协议版本，仅 Anthropic 协议需要。
- `LLM_ENABLE_THINKING`：Qwen 等模型的思考模式开关，会作为 `enable_thinking` 合并到 OpenAI-compatible 请求体。
- `LLM_OPENAI_EXTRA_BODY_JSON`：给 OpenAI-compatible 后端预留的额外请求体，必须是 JSON 对象。

## Qwen 百炼配置示例

真实配置建议放在 `config/llm.env`：

```text
LLM_PROVIDER=qwen
LLM_PROTOCOL=openai
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.7-max
LLM_API_KEY=
LLM_ENABLE_THINKING=true
LLM_TIMEOUT_MS=60000
```

如果模型名称在控制台里不同，以阿里云百炼控制台实际名称为准。DashScope / 百炼 API Key 写入真实 `config/llm.env` 的 `LLM_API_KEY`，不要写入仓库。

## 当前限制

- OpenAI-compatible 协议已支持流式输出和 `reasoning_content`。
- Anthropic-compatible 协议暂未实现流式输出。
- 暂不支持图片、文件、工具调用。
- 暂不做 provider 级重试；后续可在任务队列或调用层统一重试。
- 统一消息结构目前只支持纯文本内容，适合第一版 HTML 生成任务。
