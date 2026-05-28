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

对比后**先**定为三级降级链（motion 主 + View Transitions 备 + 纯 CSS 兜底 +
reduced-motion 直切）并集成；随后因可维护性，**主线简化为仅方案三（motion）+ 兜底直接
切换**（reduced-motion 或 motion 加载失败时）。

- 放弃方案二 / 一的原因：motion 已用 WAAPI 跨浏览器一致，两级降级收益小、DOM 耦合多，
  对早期快迭代不划算。
- 已集成到生产 [../../code/frontend/app/page.tsx](../../code/frontend/app/page.tsx)，覆盖
  `handleSubmit` / `startNewChat` / `restoreHistoryItem` 三处切换。
- 完整三级版留档在 git 分支 `full-animation-mode`；决策与可复用经验见
  [../../wiki/frontend-home-workspace-transition.md](../../wiki/frontend-home-workspace-transition.md)。

本原型三套实现保留作选型留档，后续如需重新对比可直接复用，不用于生产。
