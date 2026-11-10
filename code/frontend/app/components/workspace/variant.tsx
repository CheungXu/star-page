"use client";

import { useEffect, useState } from "react";

export type VariantId = "A" | "C1" | "C2";

export const VARIANT_PORTS: { id: VariantId; port: number; name: string }[] = [
  { id: "A", port: 3011, name: "浏览器顶栏" },
  { id: "C1", port: 3013, name: "胶片联动 · 精修" },
  { id: "C2", port: 3014, name: "聚光胶片" },
];

const current =
  VARIANT_PORTS.find((item) => item.id === (process.env.NEXT_PUBLIC_UX_VARIANT ?? "A").toUpperCase()) ?? VARIANT_PORTS[0];

/** 由启动时的 NEXT_PUBLIC_UX_VARIANT 决定当前端口展示哪一套舞台工具栏。 */
export const VARIANT = { id: current.id, name: current.name };

/** C1 里「继续改」的三种位置：焦点卡右侧竖按钮、按钮行里的主按钮、缩略图底部宽条。地址加 ?cont= 可切换。 */
export type ContinueStyle = "tab" | "row" | "thumb";

export const CONTINUE_STYLES: { id: ContinueStyle; name: string }[] = [
  { id: "tab", name: "侧边" },
  { id: "row", name: "行内" },
  { id: "thumb", name: "缩略图" },
];

export function readContinueStyle(): ContinueStyle {
  if (typeof window === "undefined") return "tab";
  const raw = new URLSearchParams(window.location.search).get("cont");
  return CONTINUE_STYLES.some((item) => item.id === raw) ? (raw as ContinueStyle) : "tab";
}

/** C1 焦点卡里用量三项与下面三个按钮按列对齐的方式：各列居中，或图标靠左对成一条竖线。地址加 ?meta= 可切换。 */
export type MetaAlign = "center" | "left";

export const META_ALIGNS: { id: MetaAlign; name: string }[] = [
  { id: "center", name: "居中" },
  { id: "left", name: "左对齐" },
];

export function readMetaAlign(): MetaAlign {
  if (typeof window === "undefined") return "center";
  const raw = new URLSearchParams(window.location.search).get("meta");
  return raw === "left" ? "left" : "center";
}

function withParam(search: string, key: string, value: string): string {
  const params = new URLSearchParams(search);
  params.set(key, value);
  return `?${params.toString()}`;
}

/** 顶部方案角标：标明当前方案，点 A / C1 / C2 在端口间切换并保留地址参数；C1 还能切换「继续改」的位置。 */
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
  const meta = location ? readMetaAlign() : null;
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
      {location && VARIANT.id === "C1" && (
        <span className="variant-links" title="「继续改」的位置">
          继续改
          {CONTINUE_STYLES.map((item) => (
            <a key={item.id} href={withParam(location.search, "cont", item.id)} className={item.id === cont ? "is-current" : ""}>
              {item.name}
            </a>
          ))}
        </span>
      )}
      {location && VARIANT.id === "C1" && (
        <span className="variant-links" title="用量三项与下面三个按钮的对齐方式">
          数据对齐
          {META_ALIGNS.map((item) => (
            <a key={item.id} href={withParam(location.search, "meta", item.id)} className={item.id === meta ? "is-current" : ""}>
              {item.name}
            </a>
          ))}
        </span>
      )}
    </div>
  );
}
