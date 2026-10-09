// 前端运营埋点：经 navigator.sendBeacon 异步上报漏斗事件，绝不阻塞交互、失败静默。
// 事件名必须命中后端白名单（见 app/services/analytics/tracking.py），否则被丢弃。

const SESSION_KEY = "sp_ops_sid";

function getClientSessionId(): string {
  if (typeof window === "undefined") return "";
  try {
    let sid = window.sessionStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid =
        (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : "") ||
        Math.random().toString(36).slice(2) + Date.now().toString(36);
      window.sessionStorage.setItem(SESSION_KEY, sid);
    }
    return sid;
  } catch {
    return "";
  }
}

export function track(event: string, props?: Record<string, unknown>): void {
  if (typeof window === "undefined") return;
  try {
    const body = JSON.stringify({ event, props: props ?? {}, client_session_id: getClientSessionId() });
    const url = "/api/analytics/collect";
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: "application/json" });
      if (navigator.sendBeacon(url, blob)) return;
    }
    void fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "include",
    });
  } catch {
    // 埋点失败不影响主流程
  }
}
