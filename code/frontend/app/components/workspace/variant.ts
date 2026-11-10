/**
 * 结果区舞台工具栏方案。C1（胶片联动 · 缩略图上「继续改」、用量居中、名称左对齐）为线上方案；
 * A（浏览器顶栏）、C2（聚光胶片）保留为 AB 测试备选，只有前端实现，构建时用 NEXT_PUBLIC_UX_VARIANT 切换。
 * 方案说明、截图与完整演示版本见 alternatives/preview-toolbar/。
 */
export type UxVariant = "A" | "C1" | "C2";

const requested = (process.env.NEXT_PUBLIC_UX_VARIANT ?? "").toUpperCase();

export const UX_VARIANT: UxVariant = requested === "A" || requested === "C2" ? requested : "C1";
