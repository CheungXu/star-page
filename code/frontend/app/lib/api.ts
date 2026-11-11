// 统一的前端请求与错误读取工具，供首页与计费/后台等页面复用。

export type BillingErrorDetail = {
  code?: string;
  message?: string;
  needLogin?: boolean;
  availableCredits?: number;
  requiredHoldCredits?: number;
  debtCredits?: number;
  suggestSingleModel?: string | null;
  suggestLightPair?: string[];
  recommendedPackageKey?: string;
  isPaidUser?: boolean;
  lockedModels?: string[];
};

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

// 读取后端 402/403 等业务错误的结构化 detail。
export async function readBillingError(response: Response): Promise<BillingErrorDetail> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      return {
        code: typeof d.code === "string" ? d.code : undefined,
        message: typeof d.message === "string" ? d.message : undefined,
        needLogin: Boolean(d.need_login),
        availableCredits: typeof d.available_credits === "number" ? d.available_credits : undefined,
        requiredHoldCredits: typeof d.required_hold_credits === "number" ? d.required_hold_credits : undefined,
        debtCredits: typeof d.debt_credits === "number" ? d.debt_credits : undefined,
        suggestSingleModel: typeof d.suggest_single_model === "string" ? d.suggest_single_model : null,
        suggestLightPair: Array.isArray(d.suggest_light_pair)
          ? d.suggest_light_pair.filter((x): x is string => typeof x === "string")
          : undefined,
        recommendedPackageKey: typeof d.recommended_package_key === "string" ? d.recommended_package_key : undefined,
        isPaidUser: typeof d.is_paid_user === "boolean" ? d.is_paid_user : undefined,
        lockedModels: Array.isArray(d.locked_models)
          ? d.locked_models.filter((x): x is string => typeof x === "string")
          : undefined,
      };
    }
  } catch {
    // ignore
  }
  return {};
}
