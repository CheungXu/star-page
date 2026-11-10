"use client";

import { useEffect, useState } from "react";

export type VariantId = "A" | "C1" | "C2";

export const VARIANT_PORTS: { id: VariantId; port: number; name: string }[] = [
  { id: "A", port: 3011, name: "浏览器顶栏" },
  { id: "C1", port: 3013, name: "胶片联动 · 精修" },
  { id: "C2", port: 3014, name: "胶片联动 · 极简" },
];

const current =
  VARIANT_PORTS.find((item) => item.id === (process.env.NEXT_PUBLIC_UX_VARIANT ?? "A").toUpperCase()) ?? VARIANT_PORTS[0];

/** 由启动时的 NEXT_PUBLIC_UX_VARIANT 决定当前端口展示哪一套舞台工具栏。 */
export const VARIANT = { id: current.id, name: current.name, filmCard: current.id !== "A" };

/** 缩略图上「继续改」的三种样式：底部胶囊、底部通栏、右下角标。地址加 ?cont= 可切换。 */
export type ContinueStyle = "pill" | "bar" | "corner";

export const CONTINUE_STYLES: { id: ContinueStyle; name: string }[] = [
  { id: "pill", name: "胶囊" },
  { id: "bar", name: "底条" },
  { id: "corner", name: "角标" },
];

export function readContinueStyle(): ContinueStyle {
  const fallback: ContinueStyle = VARIANT.id === "C2" ? "bar" : "pill";
  if (typeof window === "undefined") return fallback;
  const raw = new URLSearchParams(window.location.search).get("cont");
  return CONTINUE_STYLES.some((item) => item.id === raw) ? (raw as ContinueStyle) : fallback;
}

function withParam(search: string, key: string, value: string): string {
  const params = new URLSearchParams(search);
  params.set(key, value);
  return `?${params.toString()}`;
}

/** 顶部方案角标：标明当前方案，点 A / C1 / C2 在端口间切换并保留地址参数；C 系列还能切换「继续改」的样式。 */
export function VariantBadge() {
  const [location, setLocation] = useState<{ hostname: string; protocol: string; search: string } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLocation({ hostname: window.location.hostname, protocol: window.location.protocol, search: window.location.search });
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const demo = location ? new URLSearchParams(location.search).get("demo") : null;
  const cont = location ? readContinueStyle() : null;
  return (
    <div className="variant-tag" aria-label="当前预览方案">
      <span className="variant-dot" />
      <span>
        方案 {VARIANT.id} · {VARIANT.name}
      </span>
      {location && (
        <span className="variant-links">
          {VARIANT_PORTS.map((item) => (
            <a
              key={item.id}
              href={`${location.protocol}//${location.hostname}:${item.port}/${location.search}`}
              className={item.id === VARIANT.id ? "is-current" : ""}
              title={`方案 ${item.id} · ${item.name}`}
            >
              {item.id}
            </a>
          ))}
          {!demo && (
            <a href="?demo=9" title="用 9 个模型的演示数据查看">
              演示
            </a>
          )}
        </span>
      )}
      {location && VARIANT.filmCard && (
        <span className="variant-links" title="缩略图上「继续改」的样式">
          继续改
          {CONTINUE_STYLES.map((item) => (
            <a key={item.id} href={withParam(location.search, "cont", item.id)} className={item.id === cont ? "is-current" : ""}>
              {item.name}
            </a>
          ))}
        </span>
      )}
    </div>
  );
}
