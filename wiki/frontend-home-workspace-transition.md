# 首页 ↔ 生成页衔接过渡：命令式协调器 + 三级降级链

「一句话生成」「搜索框 → 结果页」这类产品，首页（居中输入）与工作区（结果 +
侧栏）往往是**两套完全不同的 DOM**，靠条件渲染切换。直接 `setState` 切换会让
React 瞬间卸载一棵树、挂载另一棵 → 用户看到「硬闪」，非常突兀。

本条沉淀一套**在 React 里给两态切换加平滑衔接过渡**的通用做法：用一个命令式过渡
协调器统一三处入口，并设计**「主方案 + 多级降级」的运行时降级链**，兼顾「最佳观感」
与「全浏览器可用」。本项目最终用它实现了首页 hero ↔ 生成工作区的 FLIP 飞行过渡。

## 一、为什么是「命令式协调器 + 三级降级链」

声明式动画库（如 framer-motion 的 `AnimatePresence`/`layoutId`）也能做，但它把动画
绑死在一种实现上、无法在运行时降级；而且两态结构差异大时，声明式 layout 动画的
心智成本和耦合都更高。

更稳的做法是：**把「改状态」与「怎么动画」解耦**——所有会切换视图的 `setState`
都包进一个 `mutate` 闭包交给协调器，协调器负责在切换前后测量 DOM、按当前环境能力
选择一种过渡实现来播放动画。这样可以排出一条**降级链**，逐级退而求其次：

```
motion 库（主，最佳观感）
  └─依赖加载失败→ View Transitions API（备，零依赖、原生 morph）
        └─浏览器不支持→ 纯 CSS 交叉过渡（兜底，全浏览器一致）
              └─prefers-reduced-motion→ 直接切换，无动画
```

每一级的**触发条件**与**排序理由**：

| 级别 | 实现 | 何时启用 | 为什么排这个位置 |
| --- | --- | --- | --- |
| 主 | motion 库 | 库加载成功（默认） | 控制力最强：FLIP 飞行、文字变气泡、内容 stagger，最有「作品感」，且 WAAPI 跨浏览器一致 |
| 降级一 | View Transitions API | motion 动态 import 失败 | 原生、零依赖、共享元素自动 morph；作为「库挂了也要有像样过渡」的保底 |
| 降级二 | 纯 CSS 交叉过渡 | 浏览器无 `startViewTransition` | 零依赖、全浏览器一致的最后防线（旧 Safari/Firefox） |
| 兜底 | 直接切换 | `prefers-reduced-motion: reduce` | 无障碍：不做任何动画，等同现状但不报错 |

要点：降级链是**运行时**的（按环境能力挑实现），不是开发期二选一。把它做成一个纯
函数 `runStageTransition(stage, motionLib, mutate)`，依次判断即可。

## 二、统一协调器骨架

```ts
function runStageTransition(stage, motionLib, mutate) {
  // 兜底：无障碍直接切换
  if (!stage || prefersReducedMotion()) { mutate(); return; }

  if (motionLib) {
    motionTransition(stage, motionLib, mutate);          // 主
  } else if (typeof document.startViewTransition === "function") {
    viewTransitionsTransition(mutate);                   // 降级一
  } else {
    cssTransition(stage, mutate);                        // 降级二
  }
}
```

三处会切换视图的入口（本项目是 `handleSubmit` / `startNewChat` /
`restoreHistoryItem`）统一改成：

```ts
playTransition(() => {
  setStatus("thinking");
  setSubmittedPrompt(text);
  // …所有决定新视图的 setState 都放进这一个闭包
});
```

## 三、React 集成的 5 个关键点（坑都在这里）

1. **稳定的舞台容器**：把两态包进一个**始终存在**的容器
   `<div className="app-stage" ref={stageRef}>{idle ? hero : workspace}</div>`，
   协调器靠 `stageRef` 拿到「当前 main」并叠加 overlay。若把 ref 绑在会被卸载的
   节点上，切换瞬间引用就失效了。

2. **两态加不同 `key`**（`key="hero"` / `key="workspace"`）：强制 React 卸载旧
   挂载新，避免它复用同一个 `<main>` 导致内部状态串台；旧视图我们已克隆成 overlay
   独立淡出，不依赖原节点存活。

3. **`flushSync` 同步提交**：动画要在「切换前测 First、切换后测 Last」之间插一次
   **同步**的 DOM 更新。把 `mutate` 用 `react-dom` 的 `flushSync` 包起来，React 立刻
   重渲染出新 DOM，协调器才能马上测到新位置（View Transitions 同理，必须让浏览器在
   `startViewTransition` 回调里捕获到新 DOM）。在事件处理器里调用 `flushSync` 是安全的。

4. **动态 import 实现降级**：`import("motion").then(...).catch(...)`——成功则把
   `animate` 存进 ref，失败保持为空，协调器自动走 View Transitions。动态 import 还顺带
   把库拆成独立 chunk、不阻塞首屏。**首屏从本地存储恢复非首页态时，不要走过渡**
   （没有「从首页来」的语境），只有用户主动的三处切换才包 `playTransition`。

5. **内联样式而非 className 驱动动画**：动画进行中组件可能因别的 `setState` 重渲染，
   会用 JSX 里的 `className` 覆盖你手动加的过渡类。所以新视图入场/旧层淡出**全用内联
   `style`**（`el.style.opacity = ...`）或 WAAPI 驱动，React 不会重置未在 JSX 声明的
   内联样式；动画结束在 `finished.then(cleanup, cleanup)` 里清掉内联样式。

## 四、三种实现各自的核心技巧

**主 · motion 命令式 FLIP**（不用声明式组件，直接 `animate` DOM）：
- 旧视图离场：把当前 `<main>` `cloneNode(true)` 成一个 `position: fixed; inset: 0`
  的 overlay 叠加全屏，`animate` 它淡出上移后 `remove()`——这样新视图挂载后旧画面仍能
  独立淡出（原节点已被 React 卸载）。
- 共享输入卡 FLIP：切换前后各测一次 `getBoundingClientRect()`，对新卡
  `animate(transform: [`translate(dx,dy) scale(sx,sy)`, "none"])`，`transformOrigin:
  top left`，呈现「从旧位置飞到新位置」。
- 文字变气泡：取旧输入框文字与位置，造一个 `fixed` 文字克隆，`animate` 它从输入框
  位置飘到「需求气泡」位置并淡出，同时把真气泡初始 `opacity:0`、动画末清除。
- 内容 stagger：给新视图要逐项入场的容器打 `data-anim-stagger`，
  `querySelectorAll` 后按 `delay: base + i*step` 逐个 `animate(opacity/translateY)`。
  （用 `delay` 数值循环比依赖库的 `stagger()` 函数签名更稳。）

**降级一 · View Transitions**：`startViewTransition(() => flushSync(mutate))`，配合 CSS
给共享元素 `view-transition-name`。命名只在切换瞬间挂上去（给 `<html>` 加一个
`vt-switching` class，用 `html.vt-switching .prompt-card { view-transition-name: ... }`
作用域化），`finished` 后移除——避免命名常驻产生副作用。同名元素任一时刻只能存在一个
（条件渲染天然保证）。`::view-transition-old/new(root)` 写淡入淡出关键帧。

**降级二 · 纯 CSS**：同样 clone 旧 main 当 overlay，但用内联 `transition` + 两次
`requestAnimationFrame`（先设初值、下一帧设目标值触发过渡），新视图从 `translateY(16px)`
+ `opacity:0` 淡入，定时器到点清理。

## 五、无障碍

`prefers-reduced-motion: reduce` 在协调器入口直接 `mutate()` 不做动画；同时 CSS 里给
`::view-transition-*` 也加 reduced 媒体查询把 `animation: none`，双保险。

## 六、选型与原型方法

三套方案先用「多端口静态原型」并排对比选型（见
`multi-port-static-preview-for-design-variants.md`），用一条「导演控制条」反复重播
「生成 / 返回 / 历史进入」三种切换、并带「模拟 reduced-motion」勾选框。**关键经验**：
原型阶段 motion 用浏览器 ESM 的**命令式** API，与最终生产集成一致（都是命令式
`animate` DOM）——如果原型用命令式、生产却改用声明式 `AnimatePresence`，原型就只能
代表「视觉目标」而非「实现验证」。让原型与生产用同一种范式，选型结论才可直接落地。

## 相关条目

- `multi-port-static-preview-for-design-variants.md`：多端口原型对比模式（本过渡的选型工具）。
- `ai-generation-progress-ux.md`：生成/长任务过程可视化 UX（衔接过渡是其入口体验的一环）。
- `frontend-design-tokens-and-prompt-card.md`：输入卡片设计（FLIP 飞行的共享元素）。
