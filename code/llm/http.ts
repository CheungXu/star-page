import { LlmProviderError } from "./types";

export async function postJson<TResponse>(params: {
  url: string;
  headers: Record<string, string>;
  body: unknown;
  timeoutMs: number;
  provider: string;
}): Promise<TResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), params.timeoutMs);

  try {
    const response = await fetch(params.url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...params.headers,
      },
      body: JSON.stringify(params.body),
      signal: controller.signal,
    });

    const responseBody = await readJsonOrText(response);

    if (!response.ok) {
      throw new LlmProviderError({
        message: `${params.provider} 请求失败：HTTP ${response.status}`,
        provider: params.provider,
        status: response.status,
        responseBody,
      });
    }

    return responseBody as TResponse;
  } catch (error) {
    if (error instanceof LlmProviderError) {
      throw error;
    }

    throw new LlmProviderError({
      message: `${params.provider} 请求异常：${error instanceof Error ? error.message : String(error)}`,
      provider: params.provider,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function* postJsonSse<TChunk>(params: {
  url: string;
  headers: Record<string, string>;
  body: unknown;
  timeoutMs: number;
  provider: string;
}): AsyncGenerator<TChunk> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), params.timeoutMs);

  try {
    const response = await fetch(params.url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
        ...params.headers,
      },
      body: JSON.stringify(params.body),
      signal: controller.signal,
    });

    if (!response.ok) {
      const responseBody = await readJsonOrText(response);
      throw new LlmProviderError({
        message: `${params.provider} 流式请求失败：HTTP ${response.status}`,
        provider: params.provider,
        status: response.status,
        responseBody,
      });
    }

    if (!response.body) {
      throw new LlmProviderError({
        message: `${params.provider} 流式请求没有返回响应体`,
        provider: params.provider,
      });
    }

    yield* readSseChunks<TChunk>(response.body);
  } catch (error) {
    if (error instanceof LlmProviderError) {
      throw error;
    }

    throw new LlmProviderError({
      message: `${params.provider} 流式请求异常：${error instanceof Error ? error.message : String(error)}`,
      provider: params.provider,
    });
  } finally {
    clearTimeout(timeout);
  }
}

async function readJsonOrText(response: Response): Promise<unknown> {
  const text = await response.text();

  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function* readSseChunks<TChunk>(body: ReadableStream<Uint8Array>): AsyncGenerator<TChunk> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();

    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();

      if (!trimmed.startsWith("data:")) {
        continue;
      }

      const data = trimmed.slice("data:".length).trim();

      if (!data || data === "[DONE]") {
        continue;
      }

      yield JSON.parse(data) as TChunk;
    }
  }
}
