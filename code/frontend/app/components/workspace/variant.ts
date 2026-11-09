export type VariantId = "A" | "B" | "C";

export const VARIANT_PORTS: { id: VariantId; port: number; name: string }[] = [
  { id: "A", port: 3011, name: "轻量看板" },
  { id: "B", port: 3012, name: "沉浸画廊" },
  { id: "C", port: 3013, name: "赛道对比" },
];

const current =
  VARIANT_PORTS.find((item) => item.id === (process.env.NEXT_PUBLIC_UX_VARIANT ?? "A").toUpperCase()) ?? VARIANT_PORTS[0];

/** 由启动时的 NEXT_PUBLIC_UX_VARIANT 决定当前端口展示哪一套界面。 */
export const VARIANT = {
  id: current.id,
  name: current.name,
  layoutClass: `layout-${current.id.toLowerCase()}`,
};
