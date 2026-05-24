import { postJson, postJsonSse } from "../http";
import type {
  LlmClient,
  LlmGenerateTextRequest,
  LlmGenerateTextResponse,
  LlmGenerateTextStreamChunk,
  LlmMessage,
  LlmProviderConfig,
} from "../types";

interface OpenAiChatCompletionResponse {
  model?: string;
  choices?: Array<{
    message?: {
      content?: string | null;
      reasoning_content?: string | null;
    };
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

interface OpenAiChatCompletionChunk {
  model?: string;
  choices?: Array<{
    delta?: {
      content?: string | null;
      reasoning_content?: string | null;
    };
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

export class OpenAiCompatibleClient implements LlmClient {
  private readonly config: LlmProviderConfig;

  constructor(config: LlmProviderConfig) {
    this.config = config;
  }

  async generateText(request: LlmGenerateTextRequest): Promise<LlmGenerateTextResponse> {
    const response = await postJson<OpenAiChatCompletionResponse>({
      url: `${normalizeBaseUrl(this.config.baseUrl ?? "https://api.openai.com/v1")}/chat/completions`,
      provider: this.config.provider,
      timeoutMs: this.config.timeoutMs ?? 60_000,
      headers: {
        authorization: `Bearer ${this.config.apiKey}`,
      },
      body: buildOpenAiRequestBody(this.config, request),
    });

    return {
      text: response.choices?.[0]?.message?.content ?? "",
      reasoningText: response.choices?.[0]?.message?.reasoning_content ?? undefined,
      model: response.model ?? this.config.model,
      provider: this.config.provider,
      usage: {
        inputTokens: response.usage?.prompt_tokens,
        outputTokens: response.usage?.completion_tokens,
        totalTokens: response.usage?.total_tokens,
      },
      rawResponse: response,
    };
  }

  async *streamText(request: LlmGenerateTextRequest): AsyncIterable<LlmGenerateTextStreamChunk> {
    let yieldedDone = false;

    for await (const chunk of postJsonSse<OpenAiChatCompletionChunk>({
      url: `${normalizeBaseUrl(this.config.baseUrl ?? "https://api.openai.com/v1")}/chat/completions`,
      provider: this.config.provider,
      timeoutMs: this.config.timeoutMs ?? 60_000,
      headers: {
        authorization: `Bearer ${this.config.apiKey}`,
      },
      body: {
        ...buildOpenAiRequestBody(this.config, request),
        stream: true,
      },
    })) {
      const delta = chunk.choices?.[0]?.delta;

      if (delta?.reasoning_content) {
        yield {
          type: "reasoning_delta",
          reasoningText: delta.reasoning_content,
          model: chunk.model ?? this.config.model,
          provider: this.config.provider,
          rawChunk: chunk,
        };
      }

      if (delta?.content) {
        yield {
          type: "text_delta",
          text: delta.content,
          model: chunk.model ?? this.config.model,
          provider: this.config.provider,
          rawChunk: chunk,
        };
      }

      if (chunk.usage) {
        yieldedDone = true;
        yield {
          type: "done",
          model: chunk.model ?? this.config.model,
          provider: this.config.provider,
          usage: {
            inputTokens: chunk.usage.prompt_tokens,
            outputTokens: chunk.usage.completion_tokens,
            totalTokens: chunk.usage.total_tokens,
          },
          rawChunk: chunk,
        };
      }
    }

    if (!yieldedDone) {
      yield {
        type: "done",
        model: this.config.model,
        provider: this.config.provider,
      };
    }
  }
}

function toOpenAiMessages(messages: LlmMessage[]) {
  return messages.map((message) => ({
    role: message.role,
    content: message.content,
  }));
}

function buildOpenAiRequestBody(config: LlmProviderConfig, request: LlmGenerateTextRequest) {
  return {
    model: config.model,
    messages: toOpenAiMessages(request.messages),
    temperature: request.temperature,
    max_tokens: request.maxTokens,
    stop: request.stopSequences,
    ...config.openaiExtraBody,
    ...request.providerOptions?.openai?.extraBody,
  };
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, "");
}
