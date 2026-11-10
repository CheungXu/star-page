"use client";

import { useEffect, useState } from "react";

export type VariantId = "A" | "C";

export const VARIANT_PORTS: { id: VariantId; port: number; name: string }[] = [
  { id: "A", port: 3011, name: "浏览器顶栏" },
  { id: "C", port: 3013, name: "胶片联动" },
];

const current =
  VARIANT_PORTS.find((item) => item.id === (process.env.NEXT_PUBLIC_UX_VARIANT ?? "A").toUpperCase()) ?? VARIANT_PORTS[0];

/** 由启动时的 NEXT_PUBLIC_UX_VARIANT 决定当前端口展示哪一套舞台工具栏。 */
export const VARIANT = { id: current.id, name: current.name };

/** 顶部方案角标：标明当前方案，点 A / C 在两个端口间切换并保留地址参数。 */
export function VariantBadge() {
  const [location, setLocation] = useState<{ hostname: string; protocol: string; search: string } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLocation({ hostname: window.location.hostname, protocol: window.location.protocol, search: window.location.search });
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const demo = location ? new URLSearchParams(location.search).get("demo") : null;
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
    </div>
  );
}
