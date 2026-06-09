# Tailwind 输出策略与后续编译规划

## 背景

近期多模型生成对比中，豆包 Seed 2.0 Code 输出了依赖 Tailwind Play CDN 的页面：HTML 内容完整，但主要视觉依赖 `cdn.tailwindcss.com` 运行时生成 CSS。当前生成页网关采用沙箱 CSP，且清洗器只放行可信 CDN，Tailwind Play CDN 不在白名单内，因此页面发布后大量工具类无法生效，看起来像 CSS 丢失。

## 当前处理

短期采用"普通内联 CSS"策略：

- 系统提示词明确要求所有样式写成普通 `<style>` CSS。
- 禁止引用 `https://cdn.tailwindcss.com`。
- 禁止输出 `type="text/tailwindcss"`。
- 禁止依赖 Tailwind、Bootstrap 等运行时/工具类 CSS 框架完成主要样式。
- 上传前检测 Tailwind 运行时依赖；首次命中时追加修正提示，要求模型保持内容与设计意图，改写为普通内联 CSS 后自动重试一次。
- 第二次仍命中时认为生成不合规，任务失败，避免发布裸样式页面。

该策略不改变现有 `/p` 网关、CSP 沙箱和 HTML 清洗边界。

## 后续规划

未来如需支持 Tailwind class，可通过配置开关实现"服务端编译后内联 CSS"，但不放开浏览器端 Tailwind Play CDN：

- 新增 `GENERATED_PAGE_TAILWIND_COMPILE_ENABLED=false`，默认关闭。
- 可选新增编译超时、输入 HTML 大小、输出 CSS 大小和并发限制配置。
- 开关开启时，生成后检测 Tailwind 用法，调用受限的本地 Tailwind 编译器扫描 HTML。
- 将编译后的 CSS 注入普通 `<style>`，移除 Tailwind CDN 和 `text/tailwindcss`。
- 编译后继续执行 CSS/HTML 安全检查，禁止 `@import`，限制或清理 `url(...)`。
- 编译失败时不发布半成品，回退为要求模型重试普通 CSS。

安全边界仍以最终发布"单文件、自包含 HTML + 普通内联 CSS"为目标。
