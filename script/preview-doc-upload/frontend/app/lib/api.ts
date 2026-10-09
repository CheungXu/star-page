// 统一的前端请求与错误读取工具，供首页与计费/后台等页面复用。

export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: init.credentials ?? "include",
  });
}

export async function readErrorMessage(response: Response, fallback = "请求失败"): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  } catch {
    return fallback;
  }
  return fallback;
}

// 读取后端 402/403 等业务错误的结构化 detail（{ code, message, need_login }）。
export async function readBillingError(
  response: Response,
): Promise<{ code?: string; message?: string; needLogin?: boolean }> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object") {
      const d = detail as { code?: string; message?: string; need_login?: boolean };
      return { code: d.code, message: d.message, needLogin: d.need_login };
    }
  } catch {
    // ignore
  }
  return {};
}
