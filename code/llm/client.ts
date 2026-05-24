import { AnthropicCompatibleClient } from "./providers/anthropic";
import { OpenAiCompatibleClient } from "./providers/openai";
import type { LlmClient, LlmProviderConfig } from "./types";

type Env = Record<string, string | undefined>;

declare const process: { env: Env };

export function createLlmClient(config: LlmProviderConfig): LlmClient {
  assertProviderConfig(config);

  switch (config.protocol) {
    case "openai":
      return new OpenAiCompatibleClient(config);
    case "anthropic":
      return new AnthropicCompatibleClient(config);
    default:
      return assertNever(config.protocol);
  }
}

export function createLlmClientFromEnv(env: Env = process.env): LlmClient {
  const provider = requiredEnv(env, "LLM_PROVIDER");

  return createLlmClient({
    provider,
    protocol: requiredEnv(env, "LLM_PROTOCOL") as LlmProviderConfig["protocol"],
    apiKey: requiredEnv(env, "LLM_API_KEY"),
    model: requiredEnv(env, "LLM_MODEL"),
    baseUrl: optionalEnv(env, "LLM_BASE_URL"),
    timeoutMs: optionalNumberEnv(env, "LLM_TIMEOUT_MS"),
    anthropicVersion: optionalEnv(env, "LLM_ANTHROPIC_VERSION"),
    openaiExtraBody: buildOpenAiExtraBody(env),
  });
}

function assertProviderConfig(config: LlmProviderConfig): void {
  if (config.protocol !== "openai" && config.protocol !== "anthropic") {
    throw new Error(`不支持的 LLM 协议：${config.protocol}`);
  }
}

function requiredEnv(env: Env, key: string): string {
  const value = env[key];

  if (!value) {
    throw new Error(`缺少环境变量：${key}`);
  }

  return value;
}

function optionalEnv(env: Env, key: string): string | undefined {
  const value = env[key];
  return value ? value : undefined;
}

function optionalNumberEnv(env: Env, key: string): number | undefined {
  const value = env[key];

  if (!value) {
    return undefined;
  }

  const numberValue = Number(value);

  if (!Number.isFinite(numberValue)) {
    throw new Error(`环境变量 ${key} 必须是数字`);
  }

  return numberValue;
}

function buildOpenAiExtraBody(env: Env): Record<string, unknown> | undefined {
  const extraBody: Record<string, unknown> = {};
  const extraBodyJson = optionalEnv(env, "LLM_OPENAI_EXTRA_BODY_JSON");
  const enableThinking = optionalBooleanEnv(env, "LLM_ENABLE_THINKING");

  if (extraBodyJson) {
    const parsed = JSON.parse(extraBodyJson);

    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("环境变量 LLM_OPENAI_EXTRA_BODY_JSON 必须是 JSON 对象");
    }

    Object.assign(extraBody, parsed);
  }

  if (enableThinking !== undefined) {
    extraBody.enable_thinking = enableThinking;
  }

  return Object.keys(extraBody).length > 0 ? extraBody : undefined;
}

function optionalBooleanEnv(env: Env, key: string): boolean | undefined {
  const value = optionalEnv(env, key);

  if (value === undefined) {
    return undefined;
  }

  if (["1", "true", "yes", "on"].includes(value.toLowerCase())) {
    return true;
  }

  if (["0", "false", "no", "off"].includes(value.toLowerCase())) {
    return false;
  }

  throw new Error(`环境变量 ${key} 必须是布尔值`);
}

function assertNever(value: never): never {
  throw new Error(`未处理的 LLM 协议：${value}`);
}
