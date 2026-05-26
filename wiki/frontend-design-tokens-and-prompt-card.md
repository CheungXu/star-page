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

## 5. 适用范围

适合 AI 输入类首页（ChatGPT 风、Claude 风、Gemini 风、Perplexity 风、各类内部 AI 工具）。对内容浏览类（资讯流、电商）不直接适用，但 token 体系 + Aurora 背景部分通用。

## 6. 落地清单

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
