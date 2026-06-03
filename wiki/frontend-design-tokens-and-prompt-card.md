# 前端设计 Token 体系与对话式输入卡片模式

针对 AI 工具类首页（输入 prompt → 生成产物）做 UI 改版时，常见的视觉债务集中在三处：圆角混乱、阴影生硬、placeholder 反客为主。本条记录一套可复用的设计 token 体系与输入卡片设计原则。

## 1. 设计 Token 体系

在 `globals.css` 的 `:root` 集中声明，全站只允许引用 token，禁止散落硬编码。

### 圆角五档

```css
--radius-xs: 8px;   /* chip 内部小元素、token-pill */
--radius-sm: 10px;  /* chip、tag */
--radius-md: 12px;  /* 所有按钮、history-item、progress-item */
--radius-lg: 16px;  /* 侧边栏、会话面板、预览面板 */
--radius-xl: 20px;  /* 主输入卡片 */
```

原则：

- 全站禁止 `border-radius: 999px`（胶囊），与矩形圆角混用会破坏秩序感。
- 圆角档差最小 4px，避免相邻档难以分辨。
- 按"控件越大圆角越大"递增（chip < button < card < panel）。

### 阴影三档（多层柔和）

```css
--shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04);
--shadow-md:
  0 1px 2px rgba(15, 23, 42, 0.04),
  0 6px 16px -6px rgba(15, 23, 42, 0.1);
--shadow-lg:
  0 2px 4px rgba(15, 23, 42, 0.04),
  0 12px 32px -12px rgba(15, 23, 42, 0.14);
--shadow-focus: 0 0 0 4px rgba(53, 99, 233, 0.16);  /* 焦点蓝光圈 */
```

原则：

- 单层 `0 18px 60px rgba(...)` 容易"脏"。改成"近距离细投影 + 远距离散光"两层。
- 永远不要超过两层叠加（除 focus）。
- focus 状态用品牌色 4px 实心光圈替代默认浏览器 outline。

### 文本色四档

```css
--color-text-primary:   #0f172a;  /* 标题、正文、按钮文字 */
--color-text-secondary: #475569;  /* 副标题、副文字、文件提示 */
--color-text-tertiary:  #64748b;  /* 时间戳、列表小字、标签 */
--color-text-muted:     #94a3b8;  /* 仅 placeholder、空态文字 */
```

原则：

- placeholder **只能**用 muted 档；任何"看上去像内容"的灰都会触发反客为主。
- secondary 是 hint 文字的下限，再淡就过不了 WCAG AA。
- 时间戳、标签等"次要但要看清"的字使用 tertiary。

### 背景：氛围光晕（Hero Aurora）

针对 AI 首页这类"内容稀少 + 强调氛围"的场景，纯白底会显得空，密集渐变又会"屏幕脏了"。一个折中且专业的方案是**多色 Aurora 光晕 + 焦点向外淡出的网格**，独立成一个 `.hero-aurora` 固定层。

```css
.hero-aurora {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.hero-aurora .aurora-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.85;
}

/* 3 个色块：品牌色 + 邻近色 + 互补色，错开位置 */
.hero-aurora .aurora-blob-1 { /* 品牌蓝，左上 */ }
.hero-aurora .aurora-blob-2 { /* 紫罗兰，右上 */ }
.hero-aurora .aurora-blob-3 { /* 天蓝，中下 */ }

/* 网格只在中央焦点附近显示，向外淡出 */
.hero-aurora .aurora-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(15, 23, 42, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(15, 23, 42, 0.04) 1px, transparent 1px);
  background-size: 56px 56px;
  mask-image: radial-gradient(closest-side at 50% 40%, #000 55%, transparent 100%);
}

/* 缓慢飘动动画，整体增加生命感但不分散注意力 */
@keyframes aurora-float-a {
  0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
  50% { transform: translate3d(40px, 30px, 0) scale(1.05); }
}

@media (prefers-reduced-motion: reduce) {
  .hero-aurora .aurora-blob { animation: none; }
}
```

原则：

- 3 个色块即可，少于 2 个不够丰富，多于 4 个画面会乱。
- `blur(80~100px)` + `opacity ≤ 0.9`，必须用 `filter: blur`，CSS 渐变本身的过渡远远不够柔和。
- 网格必须用 `mask-image` 焦点向外淡出，否则填满全屏会显得"教练场"，破坏氛围。
- 动画周期 18~26s，三个色块用不同周期避免同步导致的"齐步走"。
- 必须遵循 `prefers-reduced-motion`，否则会被无障碍审查扣分。
- 内容层（`.hero` / `.home-shell`）记得 `position: relative; z-index: 1;`，否则会被 aurora 盖掉。

## 2. 对话式输入卡片（Prompt Card）设计原则

AI 产品的核心输入卡片必须满足"整张卡片是一个输入区"的认知，否则一定会被吐槽。

### 反例（要避免）

1. 卡片中间画一条分割线，上半部分是示例文字、下半部分是真正的输入框 → 用户会把"示例文字"误认成多行 textarea 的内容。
2. placeholder 文字过深、过长、字号过大 → 看起来像已经填好的内容。
3. 上传按钮做成饱和大色块、放在卡片下方独立位置 → 像导航 tag 而不是工具。
4. 主 CTA 用纯黑 → 在浅色背景里像黑洞，跟品牌色无关。

### 正面做法

1. **整张卡片即 textarea**：去掉卡内分割线，只在卡片底部内嵌轻量工具栏，工具栏与 textarea 共享同一个 `prompt-card` 容器。
2. **placeholder 用最浅灰**（`--color-text-muted`），文案保留一句引导：`"说说你想做的页面，例如「面向客户的产品介绍页」"`。
3. **上传按钮在卡片内左下角，图标 + 简短文字**，描边 + 12px 圆角 + 浅色填充。具体 affordance 来自图标（如回形针），不依赖背景饱和度。
4. **主 CTA 在卡片内右下角**，使用品牌色填充 + 12px 圆角 + 文字"创建"+ 上箭头图标。配合 hover 加深一档、active 再加深一档。
5. **focus 反馈**：`prompt-card:focus-within` 给品牌色描边 + 蓝光圈，让用户明确"卡片处于输入状态"。

### 推荐场景 Chips 代替"卡内示例"

把原本塞在卡片里的"长串示例文字"抽出来，做成 4 个独立 chip 放在卡片**下方**：

```tsx
const PROMPT_PRESETS = [
  { id: "product", emoji: "🚀", label: "产品介绍页", prompt: "结合我上传的产品资料..." },
  { id: "report",  emoji: "📊", label: "工作汇报",   prompt: "根据我的内容，做一份图文..." },
  { id: "resume",  emoji: "👤", label: "个人简历",   prompt: "帮我生成一个精致的个人..." },
  { id: "event",   emoji: "🎉", label: "活动邀请",   prompt: "做一个活动邀请落地页..." },
];
```

- 点击 chip 直接填入 prompt，零成本试用。
- chip 视觉权重低于卡片（描边 + 浅色），不抢戏。
- 数量控制在 3-5 个，多了会显得拥挤。

## 3. Header Logo

Header / Hero 区域的品牌 logo 是首屏视觉重心，原则：

- **优先使用透明 PNG 或 SVG**：白底 PNG 直接贴上去一定会有可见白色矩形，与背景光晕格格不入。若只有白底 PNG，先用一次性脚本透明化处理（参见 `png-logo-transparent-and-trim.md`）。
- **空间感用 `filter: drop-shadow` 双层叠加，不要用容器 `box-shadow`**：drop-shadow 跟随 alpha 形状走，五角星会有星形阴影；box-shadow 是矩形的，会暴露图像边界。

  ```css
  .brand-logo {
    filter:
      drop-shadow(0 6px 12px rgba(53, 99, 233, 0.22))    /* 近距离定位 */
      drop-shadow(0 20px 44px rgba(53, 99, 233, 0.2));   /* 远距离散光 */
  }
  ```

- **禁止用胶囊（`border-radius: 999px`）包裹小 logo 当作品牌标**：胶囊是状态/次要操作的视觉语言，做品牌标会显得单薄。Hero logo 用 96~128px 单图标即可。
- **侧边栏 / 工具栏的小 logo 容器，尺寸必须与同列图标按钮严格对齐**（如统一 40×40），避免视觉断裂。

## 4. 侧边栏（History Sidebar）

- **顶部禁止孤立 logo**：必须配合品牌文字或承担明确功能（如收起切换三合一）。收起态下让 logo 更克制（透明背景），避免与 CTA 抢戏。
- **当前会话/空态 Active 必须明确**：
  - 历史列表项 active：浅蓝背景 + 左侧 3px 蓝色竖条 + 文字变品牌色，三者组合才能在低对比浅色 UI 中显眼。
  - "新对话"按钮处于当前态：保留实心填充作为 CTA，叠加一圈柔光环 + 缓慢脉冲动画作为"你在这里"的提示，避免 CTA 性丢失：

    ```css
    .new-chat-button.is-active {
      box-shadow:
        0 4px 14px -4px rgba(53, 99, 233, 0.55),
        0 0 0 3px rgba(53, 99, 233, 0.18);
    }
    .new-chat-button.is-active::after {
      content: "";
      position: absolute;
      inset: -2px;
      border-radius: inherit;
      border: 1px solid rgba(53, 99, 233, 0.35);
      animation: new-chat-pulse 2.4s ease-in-out infinite;
      pointer-events: none;
    }
    @media (prefers-reduced-motion: reduce) {
      .new-chat-button.is-active::after { animation: none; }
    }
    ```

  - 同时配上 `aria-current="page"`，让屏幕阅读器也能识别"当前位置"。
- **列表 hover 必须不同于 active**：hover 用 `--color-surface-soft` 浅灰即可。
- **呼吸感**：列表项 padding ≥ 10px 12px，相邻项之间 2-4px gap，行高 1.5+，时间戳与标题之间 4px。
- 收起态下，必备入口：品牌（含切换）、新对话（主 CTA）、历史（点击即展开），不要再放第二个"展开"按钮。

## 5. 多层弥散阴影（精修版）

第一版的 `--shadow-lg` 是"细投影 + 远散光"两层，但所有卡片都用同一个 token 时，主输入卡片仍会显得"硬"。AI 工具首页的核心输入卡建议**单独使用一组更柔和的弥散阴影**：

```css
.prompt-card {
  box-shadow:
    0 4px 6px -1px rgba(15, 23, 42, 0.05),    /* 近距：建立轮廓 */
    0 10px 15px -3px rgba(15, 23, 42, 0.05),  /* 远距：营造悬浮 */
    0 0 0 1px rgba(255, 255, 255, 0.5) inset; /* inset 高光：玻璃质感 */
}

.prompt-card:focus-within {
  box-shadow:
    0 4px 6px -1px rgba(15, 23, 42, 0.05),
    0 10px 15px -3px rgba(15, 23, 42, 0.05),
    var(--shadow-focus);  /* 替换 inset 高光为蓝光圈 */
}
```

要点：
- `-1px / -3px` 的负 spread：让阴影向内收，避免"溢出"卡片轮廓。
- 三层阴影都用同一个低 alpha（0.05），靠多层叠加形成"近距清晰 + 远距弥散"的渐变。
- `inset` 高光只在白底卡片上有效，深色卡片改为 `rgba(255, 255, 255, 0.08) inset`。

对比 Tailwind UI / shadcn 的 `shadow` / `shadow-md` 组合，这套阴影最大优势是 `-3px` spread + 低 alpha，让远投影更"散"而不"脏"。

## 6. Chip Hover 双层阴影 + active 反馈

Chip 作为可点击卡片按钮，hover 反馈不能只用 `transform: -1px + var(--shadow-md)`，否则在已经有大量阴影的页面里会"被淹没"。

```css
.prompt-chip:hover {
  border-color: rgba(53, 99, 233, 0.4);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  transform: translateY(-2px);
  /* 双层 hover 阴影：近距品牌蓝（彩色阴影） + 远距中性黑（建立深度） */
  box-shadow:
    0 6px 14px -6px rgba(53, 99, 233, 0.35),
    0 2px 4px -1px rgba(15, 23, 42, 0.06);
}

.prompt-chip:active {
  /* active 反馈必须真实"按下感"：transform 收回 1px、阴影减半 */
  transform: translateY(-1px);
  box-shadow:
    0 3px 8px -3px rgba(53, 99, 233, 0.3),
    0 1px 2px -1px rgba(15, 23, 42, 0.06);
}
```

要点：
- **彩色阴影**：第一层用品牌色阴影 (`rgba(53, 99, 233, 0.35)`)，让 hover 与品牌色绑定，比纯灰阴影更有产品个性。
- **transform 上浮 -2px**：精修后必须 ≥ 2px，否则在密集页面里反馈不明显。
- **`:active` 必须有**：上浮回收 1px + 阴影减半，让按下感真实而不是"硬切"。

## 7. 侧边栏 Logo 圆角底板（macOS App 图标式）

侧边栏小尺寸 logo（28-32px）在白色 sidebar 底色上"浮不起来"，常见的做法是加白色底板 + 微投影：

```css
.sidebar-brand .brand-glyph {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;          /* 与同列 + 按钮、🕐 按钮严格一致 */
  height: 40px;
  border-radius: var(--radius-md);  /* 与同列按钮一致的圆角 */
  background: #ffffff;
  border: 1px solid rgba(15, 23, 42, 0.05);
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 2px 8px rgba(15, 23, 42, 0.06);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.sidebar-brand .brand-glyph img {
  width: 26px;          /* 内部 logo 留出 ~7px 边距，避免顶到边角 */
  height: 26px;
  object-fit: contain;
}

.sidebar-brand:hover .brand-glyph {
  transform: scale(1.04);
  /* hover 阴影染上品牌蓝，与 logo 颜色呼应 */
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 4px 14px rgba(53, 99, 233, 0.12);
}
```

**关键约束**（来自第三轮反馈）：
- bounding box 必须严格 40×40、`border-radius` 必须等于同列其它按钮 → 展开/收起切换时无任何"跳跃感"。
- 内部 img 留 padding 而不是让 img 撑满底板，否则 logo 会贴边显得"局促"。
- hover 阴影从纯灰升级为品牌蓝，只在 logo 自身是蓝色时使用，否则会显得"染色错误"。

## 8. 双语品牌文案双层结构

中文产品做品牌标识时，常见处理"中文主名 + 英文副名"两段：

```tsx
<span className="brand-text">
  <span className="brand-name-cn">星页</span>
  <span className="brand-name-en">StarPage</span>
</span>
```

```css
.brand-text {
  display: inline-flex;
  align-items: center;   /* 关键：center 而不是 baseline */
  gap: 6px;
  line-height: 1;        /* 关键：1，避免行高放大造成偏移 */
}

.brand-name-cn {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.01em;  /* 中文用负字距收紧 */
  color: var(--color-text-primary);
  line-height: 1;
}

.brand-name-en {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;   /* 英文用正字距放松，对比中文 */
  color: var(--color-text-secondary);
  line-height: 1;
}
```

要点：
- **垂直对齐用 `align-items: center` + `line-height: 1`**，不要用 `baseline`。中英文 baseline 差异会让整组文字相对图标偏上 1-2px，是"看着不太对"的真凶。
- **字距对比**：中文负字距收紧、英文正字距放松，强化"中紧英松"的节奏感，让组合像 logo 字而不是普通正文。
- **字重对比**：中文 800、英文 700，主名比副名重一档；色彩对比：中文 primary、英文 secondary，避免英文喧宾夺主。

## 9. 副标题品牌词高亮

长串副标题里嵌入品牌名时，单纯加粗（`font-weight: 700` + primary 色）反而像"视觉污渍"。更精致的做法是**主题色 + 中粗**：

```css
.subtitle .brand-inline {
  font-weight: 600;             /* 中粗，不是 700 */
  color: var(--color-primary);  /* 主题蓝，不是 primary 文本色 */
  letter-spacing: 0.01em;
}
```

```html
<p class="subtitle">说说你的想法，<strong class="brand-inline">星页 StarPage</strong> 帮你生成一个可分享的精致网页。</p>
```

要点：
- 颜色用品牌蓝形成"一眼锁定"的聚焦点，文字字重不必再加到 700（双重强调反而压过整句）。
- 仅用于副标题、引导文案；不要在长段正文里这样做，会显得啰嗦。

## 10. 视觉节奏 2:1 黄金分组

侧边栏（或任何"品牌区 + 操作区"的纵向布局）做收起态时，让"品牌锚点"与"操作组"产生 2:1 节奏：

```css
.history-sidebar.collapsed {
  align-items: center;
  padding: 14px 10px;
  gap: 6px;                /* 操作组内间距 */
}

.history-sidebar.collapsed .sidebar-brand {
  margin-bottom: 12px;     /* 品牌区与操作组间距 */
}
```

效果：`[logo] 12px [+] 6px [⌚]` —— 12 : 6 = 2 : 1 的视觉分组节奏。比所有间距相等的 6px 排列**显著更有秩序感**，符合视觉节奏的黄金分组原则。

展开态需要拉得更开（约 24px），保留 `sidebar-section-divider` 作为视觉分隔，divider 自身不带 margin，所有间距由 `.sidebar-brand margin-bottom` 集中控制：

```css
.history-sidebar:not(.collapsed) .sidebar-brand {
  margin-bottom: 16px;
}
.sidebar-section-divider {
  height: 1px;
  margin: 0 4px;           /* 不带上下 margin，避免双重 */
  background: var(--color-border);
}
/* 实际间距 = 16px (margin) + 1px (divider) + 6px (flex gap) ≈ 23px */
```

## 11. 滚动条 hover 反馈

第二轮已经做到"极细 + 圆角 + 半透明"，本轮在此基础上加 hover 反馈：

```css
*::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: rgba(100, 116, 139, 0.22);
  border: 1px solid transparent;
  background-clip: padding-box;     /* 让圆角更"漂浮" */
  transition: background 0.15s ease;
}

*::-webkit-scrollbar-thumb:hover {
  background: rgba(100, 116, 139, 0.42);  /* 悬停加深一倍 */
}
```

要点：
- `background-clip: padding-box` + `border: 1px solid transparent`：让滑块视觉上比 track 窄 2px，更"漂浮"。
- hover 时 alpha `0.22 → 0.42`，明确"可拖拽"反馈。
- 容器 `padding-right: 4px / margin-right: -2px`：让 thumb 不紧贴右边缘，又不增加 list 占位。

## 13. 首页对话式精修（第四轮：意图优先 + 多模型集合）

在「标签海」重构为三层结构之后，仍要避免次级控件抢主路径。可复用原则：

### 主 CTA 三态（禁止半透明盖蓝）

空态不要用 `opacity` 把实心蓝按钮洗淡——用户会误判为「可点但发虚」。应拆成独立 class：

| 状态 | class | 视觉 |
|------|-------|------|
| 不可提交 | `.submit-button.is-empty` | 灰底 `#e2e8f0`、字 `#94a3b8`、无阴影 |
| 可提交 | 默认 | `--color-primary` 实心 + `--shadow-sm`，hover `translateY(-1px)` |
| 处理中 | `.is-loading` | 浅蓝底 + 主题色字 + spinner |

空态若需点击反馈（聚焦输入框），用 `type="button"` + `is-empty`，勿与 `disabled` 混用导致无法触发。

### 快捷场景：空态 Tag，非幽灵链

空态推荐场景用 `.prompt-preset-pill`（`#f1f5f9` 底、`--radius-md`），输入后隐藏。幽灵文字链在 AI 首页上点击率偏低。

### 卡片内 footer 网格

```
textarea → presets（空态）→ .prompt-card-footer（border-top + space-between）
  左：上传（hint 放 title，勿占行宽）
  右：创建
```

### 多模型 = Chip 集合，禁止 VS

并行对比卖点由 **副标题文案** + **「已选 N 个 · 将并排生成」** 承担，不要在 Chip 间插 `VS`（强化 1v1 错觉、不可扩展 N 个）。Chip 用 `--radius-md` 矩形，与下拉等同高（36px），放进 `.advanced-setting-group` 浅底块。

### 侧边栏：单一 Active

- 品牌区 = 展开/收起，**不做** Tab 选中高亮
- 首页 idle：仅「新对话」`is-active`（收起浅底描边，展开可保留柔光环）
- 工作区：「新对话」恢复实心蓝 CTA

### 进阶选项是否外露

若产品选择「技能/模板全自动」，首页可去掉手动选择器，仅在工作区展示后端 `appliedSkill` 徽章；提交时不传 `skill_keys` 即走自动路由。

## 14. 适用范围

适合 AI 输入类首页（ChatGPT 风、Claude 风、Gemini 风、Perplexity 风、各类内部 AI 工具）。对内容浏览类（资讯流、电商）不直接适用，但 token 体系 + Aurora 背景 + 双语品牌文案 + 视觉节奏 2:1 部分通用。

## 15. 落地清单

在新项目中应用这套设计 token 时：

1. 先把 `:root` token 段落抄到 `globals.css`，并按品牌色微调 `--color-primary` / `--color-primary-soft`。
2. 所有 `border-radius:` 替换为 `var(--radius-*)`，所有 `box-shadow:` 替换为 `var(--shadow-*)`。
3. 排查所有 `color: #` 写法，归位到四档文本色变量。
4. 检查所有 placeholder：颜色是否为 muted？文案是否过长？
5. 检查所有"卡片"：内部是否有分割线？工具栏是否在卡片底部内嵌？
6. 检查所有列表：active 状态是否有三重视觉信号（背景 + 竖条 + 文字色）？
7. 检查所有 CTA：是否品牌色实心填充？是否带方向性图标（箭头）？
8. 检查 Hero 区域：是否有 Aurora 光晕层？背景内容是否记得 `position: relative; z-index: 1;` 避免被光晕盖住？
9. 检查 Header Logo：是否透明 PNG / SVG？是否用 `drop-shadow` 而非 `box-shadow`？侧边栏小 logo 是否与同列图标按钮尺寸对齐？
10. 检查当前会话状态：用户是否能在 1 秒内识别"我现在在哪个会话"？（CTA 是否有 active 态叠加？）
