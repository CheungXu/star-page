# 首页 ↔ 生成页 衔接过渡动画原型（多端口对比）

为「首页 idle hero ↔ 生成页 workspace」的切换设计三套衔接动画，平行于
[../preview-logo/](../preview-logo/) 复用「多端口静态原型对比」模式，分别跑在
3001 / 3002 / 3003，供选型对比。**这是选型用的临时原型，不会进入生产代码。**

## 启动

```bash
cd script/preview-transition
python3 serve.py
```

- http://localhost:3001/ — 方案一 · 纯 CSS 交叉过渡
- http://localhost:3002/ — 方案二 · 原生 View Transitions API
- http://localhost:3003/ — 方案三 · motion 库精细编排

> 3001-3003 与 preview-logo 共用同一组端口，二者不要同时启动。

## 怎么对比

每个页面顶部有一条**导演控制条**（仅原型用），三个按钮分别触发三种切换，可反复重播：

- **生成 → 进入工作区**：首页 hero → 生成中工作区（输入卡飞向底部、骨架屏入场）
- **历史进入（直达完成态）**：任意态 → 已完成工作区（预览视窗 + 已完成步骤条）
- **返回首页（新对话）**：工作区 → 首页 hero

控制条还有 **「模拟 reduced-motion」** 勾选框：勾上即可在不改系统设置的情况下，
验证三套方案的无障碍降级（均退化为「直接切换、无动画」，等同现状但不报错）。

## 三套方案

| 端口 | 方案 | 机制 | 优点 | 取舍 |
| --- | --- | --- | --- | --- |
| 3001 | 纯 CSS 交叉过渡 | 旧层淡出上移收缩 + 新层淡入上浮，两层叠放交叉，`transitionend`/定时协调 DOM 切换 | 零依赖、全浏览器一致 | 共享元素不连续飞行 |
| 3002 | View Transitions API | `document.startViewTransition` 包裹切换，`view-transition-name` 标记输入卡 / 侧边栏 / logo 自动 morph | 惊艳、侵入小、零依赖 | 兼容性需降级（Safari 18+ / Firefox 进行中） |
| 3003 | motion 库 | ESM CDN 命令式 FLIP（输入卡飞行）、文字克隆上浮成气泡、内容 stagger 入场 | 控制力最强、最有作品感 | 最终集成需引入依赖；原型命令式与最终声明式写法不同 |

## 文件结构

- `app.js` — 公共骨架：用与生产一致的 className 重建 hero / workspace 两态 DOM，
  渲染导演控制条与状态机，把「如何过渡」委托给各 `variant-*.js` 注入的 `transition()`。
- `director.css` — 原型专用样式：导演条 + 舞台/视图容器 + 三套方案各自的过渡 CSS（`sp-` 命名空间）。
- `variant-css.js` / `variant-vt.js` / `variant-motion.js` — 三套方案各自的过渡实现。
- `globals.css` — 从生产 [../../code/frontend/app/globals.css](../../code/frontend/app/globals.css)
  复制，保证视觉 1:1（属临时双维护，选定即可弃用或归档）。
- `demo-page.html` — 完成态预览 iframe 里的占位「生成结果页」。
- `stars-page-logo-simple.png` — 品牌 logo。
- `serve.py` — 三端口静态服务。

## 选型结论（已落地）

经对比后确定采用**运行时三级降级链**：

1. **主：方案三（motion 库）** —— 默认，观感最佳。
2. **降级一：方案二（View Transitions）** —— motion 依赖加载失败等情况时启用。
3. **降级二：方案一（纯 CSS）** —— View Transitions 兼容性等问题时启用。
4. **兜底**：`prefers-reduced-motion` 直接切换、无动画。

已正式集成到生产 [../../code/frontend/app/page.tsx](../../code/frontend/app/page.tsx)
与 `globals.css`，覆盖 `handleSubmit` / `startNewChat` / `restoreHistoryItem` 三处切换，
并统一做 `prefers-reduced-motion` 降级。决策与可复用实现经验见
[../../wiki/frontend-home-workspace-transition.md](../../wiki/frontend-home-workspace-transition.md)。

本原型目录使命完成，作为选型留档保留；后续如需重新对比可直接复用，不再用于生产。
