export type LlmProviderProtocol = "openai" | "anthropic";

export type LlmMessageRole = "system" | "user" | "assistant";

export interface LlmMessage {
  role: LlmMessageRole;
  content: string;
}

export interface LlmGenerateTextRequest {
  messages: LlmMessage[];
  temperature?: number;
  maxTokens?: number;
  stopSequences?: string[];
  providerOptions?: {
    openai?: {
      extraBody?: Record<string, unknown>;
    };
  };
}

export interface LlmUsage {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
}

export interface LlmGenerateTextResponse {
  text: string;
  reasoningText?: string;
  model: string;
  provider: string;
  usage?: LlmUsage;
  rawResponse?: unknown;
}

export type LlmStreamChunkType = "reasoning_delta" | "text_delta" | "done";

export interface LlmGenerateTextStreamChunk {
  type: LlmStreamChunkType;
  text?: string;
  reasoningText?: string;
  model?: string;
  provider: string;
  usage?: LlmUsage;
  rawChunk?: unknown;
}

export interface LlmProviderConfig {
  provider: string;
  protocol: LlmProviderProtocol;
  apiKey: string;
  model: string;
  baseUrl?: string;
  timeoutMs?: number;
  anthropicVersion?: string;
  openaiExtraBody?: Record<string, unknown>;
}

export interface LlmClient {
  generateText(request: LlmGenerateTextRequest): Promise<LlmGenerateTextResponse>;
  streamText(request: LlmGenerateTextRequest): AsyncIterable<LlmGenerateTextStreamChunk>;
}

export class LlmProviderError extends Error {
  readonly status?: number;
  readonly provider: string;
  readonly responseBody?: unknown;

  constructor(params: {
    message: string;
    provider: string;
    status?: number;
    responseBody?: unknown;
  }) {
    super(params.message);
    this.name = "LlmProviderError";
    this.provider = params.provider;
    this.status = params.status;
    this.responseBody = params.responseBody;
  }
}
