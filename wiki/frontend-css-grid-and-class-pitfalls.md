# 前端 CSS 网格与类名易错点

几条在"多模型并行生成"里踩到、且可跨项目复用的前端坑与定式。共同特征：都不是写错样式，而是**默认行为 + 特定数据/视口下才暴露**，单测和常规预览容易漏。

## 1. 动态状态词不要直接当 class（会和全局工具类撞名）

把组件状态值（如 `creating` / `idle` / `loading` / `active`）直接拼进 className，很容易和项目里某个全局工具类同名。例如状态 `creating` 撞上旧代码遗留的全局 `.creating { min-height: 240px; display: grid }`，导致一个小状态胶囊被撑成 240px 高的竖条，且只在该状态下出现、极难联想。

- 定式：状态类一律加命名空间前缀，`is-${status}` / `state-${status}`，CSS 写 `.chip.is-completed`，绝不写裸 `.completed` / `.creating`。
- 对关键小组件再加防御样式（`display:inline-flex; align-self:center; min-height:0; white-space:nowrap`），即便撞名也不至于变形。
- 排查手法：用 DevTools/CDP 量该元素 `getBoundingClientRect`，发现异常高度后查它的 className 是否命中了非预期的全局规则。

## 2. 高容器里的 grid 列表，项目少时会被 align-content 默认拉伸

grid 容器的 `align-content` 默认是 `normal`（对 grid 等同 `stretch`）。当容器有固定高度、内容（隐式行）不足以填满时，stretch 会把每个 auto 行**等高拉伸**填满容器。表现：侧栏历史列表只有 2~3 条时，每条被撑得很高，项内"标题/副标题"两行也被上下拉开。

- 定式：纵向列表类 grid 一律 `align-content: start;`，需要时配 `grid-auto-rows: max-content;`，让行按内容高、从顶部排列。
- 项内若也是 grid（标题+副标题），同样加 `align-content: start;` 兜底，避免被外层拉高时两行被扯开。
- 这个坑只在"项目少 + 容器高"时出现，项目多到填满时看不出来，所以要专门用少量数据测。

## 3. 响应式把多列 grid 降为少列时，要重排"溢出的子项"

三栏布局（如 侧栏 | 主区 | 预览）在窄屏媒体查询里改成两列时，若只改 `grid-template-columns` 而不重新指定第三个子项的位置，它会按 auto-flow 落到下一格——可能正好落进很窄的侧栏列里被压扁（标题被挤成竖排）。

- 定式：媒体查询改列数的同时，显式给溢出子项 `grid-column`，给需要跨行的元素 `grid-row: 1 / span N`，必要时放开 `height: auto` 让其纵向堆叠。
- 验证要跨多个视口宽度（尤其是介于两个断点之间的"中间宽度"），不要只看最宽和最窄。

## 4. 纵向滚动的 flex 容器里，带溢出裁剪的子项会被压扁

对话栏这类 `display:flex; flex-direction:column; overflow:auto` 容器，内容超过栏高时本应滚动。但子项若设了 `overflow: hidden/auto`，它的最小高度会变成 0（不再按内容撑开），于是被 `flex-shrink: 1` 压扁、和相邻气泡叠在一起；没有裁剪的子项反而正常。

- 定式：滚动列表里的直接子项统一 `flex-shrink: 0`（`.scroll > * { flex-shrink: 0 }`）。
- 只在"内容超过容器"时出现，要用矮窗口（如 1366×640）和多条内容专门测。

## 5. `flex: 1` 会让按内容取宽失效

`flex: 1` 等于 `flex: 1 1 0%`，基础宽度是 0。父元素想用 `width: fit-content` 按内容取宽时，这个子项按 0 参与计算，结果整体偏窄、文字被截断。

- 定式：需要"按内容宽、有剩余再伸展"时写 `flex: 1 1 auto`。如果整体宽度本来就要固定（例如展开、收起要同宽），就直接给容器定宽，不依赖内容。

## 6. flex 容器里的文字会丢掉首尾空格

`<span style="display:flex">12k tokens · 30 积分</span>` 里，文字和行内元素被拆成多个 flex 项，每段首尾的空格被吃掉，「·」两边会贴在一起。

- 定式：纯文字行用普通块级或行内布局，图标做成 `inline-flex` 小块嵌在文字里；只有需要排列多个块时才用 flex。

## 7. 窄空间里的标签：要么整个显示，要么整个隐藏

一行放多个标签（如「最快」「最省」「→第2轮」），空间不够时 `overflow: hidden` 会切出半个标签，比不显示更难看。

- 定式：标签容器 `display:flex; flex-wrap:wrap; height:<一行高>; overflow:hidden`，放不下的标签整个折到第二行并被裁掉。
- 容器查询（`@container`）要先给祖先 `container-type: inline-size`；阈值要用实际渲染宽度量，不要按设计稿估。

## 通用经验

- 这类 bug 多由"CSS 默认值 + 边界数据/视口"触发，写完要用**少量数据、空状态、中间视口宽度**专门过一遍。
- 排查首选实测：在真实页面用 DevTools/CDP 量目标元素与其父容器的尺寸、`display`/`align-content`/`grid-template-rows`，比盯着源码猜更快定位。
