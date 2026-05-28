# 首页 ↔ 生成页衔接过渡：命令式协调器与降级层级取舍

「一句话生成」「搜索框 → 结果页」这类产品，首页（居中输入）与工作区（结果 +
侧栏）往往是**两套完全不同的 DOM**，靠条件渲染切换。直接 `setState` 切换会让
React 瞬间卸载一棵树、挂载另一棵 → 用户看到「硬闪」，非常突兀。

本条沉淀两件事：① 在 React 里给两态切换加平滑衔接的**通用结构**（命令式过渡协调器）；
② 一个更现实的工程经验——**「降级层级」不是越多越好，要按维护成本权衡**。

## 一、核心结构：命令式过渡协调器

关键是**把「改状态」与「怎么动画」解耦**：所有会切换视图的 `setState` 都包进一个
`mutate` 闭包交给协调器，协调器负责在切换前后测量 DOM、播放动画。三处会切换视图的
入口（本项目是 `handleSubmit` / `startNewChat` / `restoreHistoryItem`）统一改成：

```ts
playTransition(() => {
  setStatus("thinking");
  setSubmittedPrompt(text);
  // …所有决定新视图的 setState 都放进这一个闭包
});
```

协调器入口（本项目最终形态：motion 主 + 兜底直接切换）：

```ts
function runStageTransition(stage, motionLib, mutate) {
  // 兜底：无障碍 / motion 库未就绪 → 直接切换（等同改造前的瞬切，但不报错）
  if (!stage || !motionLib || prefersReducedMotion()) { mutate(); return; }
  motionStageTransition(stage, motionLib, mutate);
}
```

## 二、React 集成的 5 个关键点（坑都在这里）

无论用哪种动画实现，这 5 点都适用：

1. **稳定的舞台容器**：把两态包进一个**始终存在**的容器
   `<div className="app-stage" ref={stageRef}>{idle ? hero : workspace}</div>`，
   协调器靠 `stageRef` 拿到「当前 main」。若把 ref 绑在会被卸载的节点上，切换瞬间引用
   就失效了。

2. **两态加不同 `key`**（`key="hero"` / `key="workspace"`）：强制 React 卸载旧挂载新，
   避免它复用同一个 `<main>` 导致内部状态串台；旧视图我们已克隆成 overlay 独立淡出，
   不依赖原节点存活。

3. **`flushSync` 同步提交**：动画要在「切换前测 First、切换后测 Last」之间插一次
   **同步**的 DOM 更新。把 `mutate` 用 `react-dom` 的 `flushSync` 包起来，React 立刻
   重渲染出新 DOM，协调器才能马上测到新位置。在事件处理器里调用 `flushSync` 是安全的。

4. **动态 import 库**：`import("motion").then(...).catch(...)`——成功把 `animate` 存进
   ref，失败保持为空、协调器自动走兜底直切。动态 import 还顺带把库拆成独立 chunk、不
   阻塞首屏。**首屏从本地存储恢复非首页态时不要走过渡**（没有「从首页来」的语境），只有
   用户主动的切换才包 `playTransition`。

5. **动画用内联样式而非 className**：动画进行中组件可能因别的 `setState` 重渲染，会用
   JSX 里的 `className` 覆盖你手动加的过渡类。所以入场/离场全用内联 `style` 或 WAAPI
   驱动，React 不会重置未在 JSX 声明的内联样式；动画结束在 `finished.then(cleanup,
   cleanup)` 里清掉内联样式。

## 三、降级层级的取舍（本条最值得记住的经验）

做这套过渡时一度实现了**三级运行时降级链**：

```
motion 库（主）→ View Transitions API（备，库加载失败时）
            → 纯 CSS 交叉过渡（兜底，浏览器不支持 VT 时）→ reduced-motion 直接切换
```

**但最终主线砍到只剩 motion + 兜底直切（即上面的 `runStageTransition`）**，原因是
权衡了维护成本与实际收益：

- **motion 本身就是用 WAAPI 实现、跨所有现代浏览器一致的**，所以「motion 可用」是
  绝大多数情况。View Transitions 那一层的「原生 morph」优势在 motion 可用时根本用不到；
  在 motion 罕见地加载失败时，退到纯 CSS 淡入淡出对用户观感差异也极小。
- **三套实现并存是 3 倍概念量**，而 View Transitions 这一级**收益最小、DOM 耦合却最多**
  （要给共享元素加 `view-transition-name`、写一整段 `::view-transition-*` CSS）。
- 对一个**还在快速迭代 UI** 的早期产品，多级降级带来的「健壮性」收益，不抵它对后续改
  页面的维护负担。

教训：**降级链是「渐进增强」的好思路，但层级数要按项目阶段定**。早期快迭代期，「主方案
+ 一个不报错的兜底」通常就够；等产品稳定、确实遇到目标浏览器缺失能力时，再按需加中间级
不迟。完整三级实现已留档在 git 分支 `full-animation-mode`，需要时可直接取回参考。

## 四、降低「静默失效」风险：DOM 契约集中化

命令式过渡靠选择器抓共享元素（`.prompt-card`、`.user-message p`、`[data-anim-stagger]`
等），它**耦合了 DOM 结构**。最大的隐患是**静默失效**：未来重命名 class 或调结构后，
动画悄悄没了，但不报错、不影响功能，不易第一时间发现。

缓解办法：把过渡依赖的所有选择器/标记**集中成一份带注释的「契约」常量**，改页面时有
一处清单可同步：

```ts
/* 改这些 class / 标记时需同步这里，否则过渡会「静默失效」（只是没动画，不报错） */
const TRANSITION_DOM = {
  sharedCard: ".prompt-card",          // FLIP 飞行的共享输入卡
  promptText: ".prompt-card textarea", // 文字「变气泡」的源文字
  bubbleTarget: ".user-message p",     // 文字「变气泡」的落点
  staggerItems: "[data-anim-stagger]", // 逐项 stagger 入场的内容
} as const;
```

## 五、motion 命令式编排技巧

不用声明式组件（`AnimatePresence`/`layoutId`），直接 `animate` DOM，便于与命令式协调器
配合，也便于运行时降级：

- **旧视图离场**：把当前 `<main>` `cloneNode(true)` 成 `position: fixed; inset: 0` 的
  overlay 叠加全屏，`animate` 它淡出上移后 `remove()`——这样新视图挂载后旧画面仍能独立
  淡出（原节点已被 React 卸载）。
- **共享输入卡 FLIP**：切换前后各测一次 `getBoundingClientRect()`，对新卡
  `animate(transform: [`translate(dx,dy) scale(sx,sy)`, "none"])`，`transformOrigin:
  top left`，呈现「从旧位置飞到新位置」。
- **文字变气泡**：取旧输入框文字与位置，造一个 `fixed` 文字克隆，`animate` 它从输入框
  位置飘到「需求气泡」位置并淡出，同时把真气泡初始 `opacity:0`、动画末清除。
- **内容 stagger**：给新视图要逐项入场的容器打 `data-anim-stagger`，`querySelectorAll`
  后按 `delay: base + i*step` 逐个 `animate`（用 `delay` 数值循环比依赖库的 `stagger()`
  函数签名更稳）。

## 六、无障碍

`prefers-reduced-motion: reduce` 在协调器入口直接 `mutate()` 不做动画。motion 单方案下
这一处判断即可，无需再在 CSS 里给动画做降级。

## 七、选型方法

三套方案先用「多端口静态原型」并排对比选型（见
`multi-port-static-preview-for-design-variants.md`），用一条「导演控制条」反复重播
「生成 / 返回 / 历史进入」三种切换、并带「模拟 reduced-motion」勾选框。**关键经验**：
让原型里 motion 的写法（命令式 ESM `animate` DOM）与最终生产集成保持**同一种范式**，
选型结论才能直接落地，而非只是「视觉目标」。

## 相关条目

- `multi-port-static-preview-for-design-variants.md`：多端口原型对比模式（本过渡的选型工具）。
- `ai-generation-progress-ux.md`：生成/长任务过程可视化 UX（衔接过渡是其入口体验的一环）。
- `frontend-design-tokens-and-prompt-card.md`：输入卡片设计（FLIP 飞行的共享元素）。
