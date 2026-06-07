# 首页模型菜单与灵感 chips 重叠修复 — 实施记录

## 背景

首屏输入区采用「卡片内 composer 工具栏 + 卡片下方居中灵感 chips」布局。模型选择器为向下弹出的 `model-menu` 浮层（避免向上遮挡 textarea）。用户反馈：展开模型列表时，浮层与下方「试试 · 产品介绍页 / 工作汇报 …」chips 发生重叠，标题与选项难以辨认。

## 根因

- `model-menu` 使用 `position: absolute; top: calc(100% + 10px)` 相对 `model-picker` 向下展开。
- `prompt-inspirations` 紧跟在 `prompt-card` 表单之后，垂直间距仅 `margin-top: 16px`。
- 四模型浮层高度约 200px+，必然压住 chips 区域。

这是「向下弹不挡输入」与「下方辅助内容紧邻」之间的布局冲突，不是 z-index 或点击穿透问题。

## 方案

采用**状态驱动预留空间**（最小改动）：

1. 模型菜单打开时，给首页输入外层 `hero-wrap` 增加 `model-menu-open` 类。
2. 该类下将 `.prompt-inspirations` 的 `margin-top` 增至 `230px`，为浮层腾出空间。
3. 增加 `transition: margin-top 0.18s ease`，减轻 chips 位移突兀感。

未采用：向上弹（挡输入）、Portal（改动大）、打开菜单时隐藏 chips（损失可发现性）。

## 改动文件

- `code/frontend/app/page.tsx`：`renderPromptForm` 组装 `promptWrapClassName`，菜单打开时附加 `model-menu-open`。
- `code/frontend/app/globals.css`：`.hero-wrap.model-menu-open .prompt-inspirations` 预留间距。

## 验收

- 首页展开「4 个模型」菜单时，chips 下移至浮层下方，无文字叠压。
- 关闭菜单后 chips 回到原位；开始输入后 chips 仍按原逻辑隐藏。
- `npm run lint` / `npm run build` 通过。
- 生产：`systemctl restart star-page-frontend.service`（构建后重启，见 `wiki/systemd-nextjs-fastapi-deployment.md`）。

## 沉淀去向

- `code/frontend/README.md`：首屏模型选择器与灵感 chips 说明已同步。
- `wiki/frontend-design-tokens-and-prompt-card.md`：新增「向下弹出浮层与相邻 Chips 的层叠冲突」可复用条目。
