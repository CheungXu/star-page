import { postJson } from "../http";
import type {
  LlmClient,
  LlmGenerateTextRequest,
  LlmGenerateTextResponse,
  LlmGenerateTextStreamChunk,
  LlmMessage,
  LlmProviderConfig,
} from "../types";
import { LlmProviderError } from "../types";

interface AnthropicMessagesResponse {
  model?: string;
  content?: Array<{
    type?: string;
    text?: string;
  }>;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
  };
}

export class AnthropicCompatibleClient implements LlmClient {
  private readonly config: LlmProviderConfig;

  constructor(config: LlmProviderConfig) {
    this.config = config;
  }

  async generateText(request: LlmGenerateTextRequest): Promise<LlmGenerateTextResponse> {
    const { system, messages } = toAnthropicMessages(request.messages);

    const response = await postJson<AnthropicMessagesResponse>({
      url: `${normalizeBaseUrl(this.config.baseUrl ?? "https://api.anthropic.com")}/v1/messages`,
      provider: this.config.provider,
      timeoutMs: this.config.timeoutMs ?? 60_000,
      headers: {
        "x-api-key": this.config.apiKey,
        "anthropic-version": this.config.anthropicVersion ?? "2023-06-01",
      },
      body: {
        model: this.config.model,
        system,
        messages,
        temperature: request.temperature,
        max_tokens: request.maxTokens ?? 4096,
        stop_sequences: request.stopSequences,
      },
    });

    const text = response.content
      ?.filter((item) => item.type === "text" && item.text)
      .map((item) => item.text)
      .join("") ?? "";

    return {
      text,
      model: response.model ?? this.config.model,
      provider: this.config.provider,
      usage: {
        inputTokens: response.usage?.input_tokens,
        outputTokens: response.usage?.output_tokens,
        totalTokens:
          response.usage?.input_tokens !== undefined && response.usage?.output_tokens !== undefined
            ? response.usage.input_tokens + response.usage.output_tokens
            : undefined,
      },
      rawResponse: response,
    };
  }

  async *streamText(_request: LlmGenerateTextRequest): AsyncIterable<LlmGenerateTextStreamChunk> {
    throw new LlmProviderError({
      message: "Anthropic 兼容协议的流式输出尚未实现",
      provider: this.config.provider,
    });
  }
}

function toAnthropicMessages(messages: LlmMessage[]) {
  const system = messages
    .filter((message) => message.role === "system")
    .map((message) => message.content)
    .join("\n\n");

  const nonSystemMessages = messages
    .filter((message) => message.role !== "system")
    .map((message) => ({
      role: message.role,
      content: message.content,
    }));

  return {
    system: system || undefined,
    messages: nonSystemMessages,
  };
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, "");
}
