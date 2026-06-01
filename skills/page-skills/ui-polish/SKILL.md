---
key: ui-polish
name: 体验打磨
description: 当用户强调可访问性、交互细节、表单可用性、排版精细度，或希望对页面做系统性质量打磨时使用；为展示页补齐 a11y、焦点态、动效、排版与内容健壮性等容易遗漏的细节
triggers: [可访问性, 无障碍, a11y, 交互细节, 打磨, 体验优化, 表单, 排版细节, 规范, 细节]
enabled: true
---

# 体验打磨专项指南

目标：在不破坏视觉表现的前提下，系统性补齐易被忽略的质量细节。改编自 Vercel Web Interface Guidelines，仅保留适用于本项目单文件静态 HTML 的条目。

## 可访问性

- 语义化标签优先（`header/nav/main/section/footer/button` 等），其次才用 ARIA。
- 仅图标的按钮加 `aria-label`；纯装饰图标加 `aria-hidden="true"`。
- 图片有 `alt`（装饰性图片用 `alt=""`）。
- 标题层级从 `h1` 起逐级递进，不跳级。
- 动态更新（提示、校验结果）容器加 `aria-live="polite"`。

## 焦点态

- 可交互元素必须有清晰可见的焦点样式（`:focus-visible`）。
- 不要 `outline: none` 而不提供替代焦点样式。
- 复合控件可用 `:focus-within` 统一聚焦反馈。

## 动效

- 遵循 `prefers-reduced-motion`，提供降级或关闭方案。
- 只动 `transform` / `opacity`（合成器友好）；不要 `transition: all`，逐项列出过渡属性。
- 设置正确的 `transform-origin`；动画应可被用户操作打断。

## 排版细节

- 用 `…` 而非 `...`；用弯引号 `""` 而非直引号。
- 数字列/对比场景用 `font-variant-numeric: tabular-nums`。
- 标题用 `text-wrap: balance` 避免孤字。
- 品牌名、代码标识等加 `translate="no"` 防止被自动翻译打乱；必要处用不换行空格（如 `10 MB`、`⌘ K`）。

## 内容健壮性

- 文本容器要能处理超长内容：`truncate` / `line-clamp` / `break-words`；flex 子项需要 `min-w-0` 才能截断。
- 处理空状态，不要为""/空数组渲染出破碎 UI。
- 预想短/中/超长三种内容长度都不破版。

## 表单（若页面含表单）

- 输入框配 `label`（可点击）、正确的 `type`/`inputmode`、有意义的 `name` 与 `autocomplete`。
- 不要拦截粘贴（`onpaste` + `preventDefault`）。
- 错误就近显示在字段旁，提交时聚焦第一个错误项；占位符以 `…` 结尾并给出示例格式。

## 图片与触控

- `img` 写明 `width`/`height` 防止布局抖动（CLS）；首屏以下图片 `loading="lazy"`。
- 可交互元素加 `touch-action: manipulation`，按需设置 `-webkit-tap-highlight-color`。
- 暗色主题在 `html` 上设 `color-scheme: dark`，修正滚动条与原生控件外观。

## 反馈状态

- 按钮/链接要有 `hover` 与 `active` 反馈；hover/active/focus 的对比应比静止态更明显。
- 加载/保存等过渡文案以 `…` 结尾（如"加载中…"）。
