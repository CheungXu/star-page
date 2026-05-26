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

### 背景

```css
background-image:
  radial-gradient(60% 50% at 50% -10%, rgba(53, 99, 233, 0.06), transparent 70%),
  radial-gradient(circle, rgba(15, 23, 42, 0.04) 1px, transparent 1px);
background-size: auto, 24px 24px;
```

原则：

- 微弱的 radial-gradient 居中放在屏幕**外**（百分比为负），让光晕只露出顶部 1/3，避免居中"屏幕脏了"感。
- 配合 24px 网格点阵营造质感，比纯渐变更克制。

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

## 3. 侧边栏（History Sidebar）

- **顶部禁止孤立 logo**：必须配合品牌文字或承担明确功能（如收起切换三合一）。收起态下让 logo 更克制（透明背景），避免与 CTA 抢戏。
- **列表 Active 状态必须明确**：浅蓝背景 + 左侧 3px 蓝色竖条 + 文字变品牌色，三者组合才能在低对比浅色 UI 中显眼。
- **列表 hover 必须不同于 active**：hover 用 `--color-surface-soft` 浅灰即可。
- **呼吸感**：列表项 padding ≥ 10px 12px，相邻项之间 2-4px gap，行高 1.5+，时间戳与标题之间 4px。
- 收起态下，必备入口：品牌（含切换）、新对话（主 CTA）、历史（点击即展开），不要再放第二个"展开"按钮。

## 4. 适用范围

适合 AI 输入类首页（ChatGPT 风、Claude 风、Gemini 风、Perplexity 风、各类内部 AI 工具）。对内容浏览类（资讯流、电商）不直接适用，但 token 体系部分通用。

## 5. 落地清单

在新项目中应用这套设计 token 时：

1. 先把 `:root` token 段落抄到 `globals.css`，并按品牌色微调 `--color-primary` / `--color-primary-soft`。
2. 所有 `border-radius:` 替换为 `var(--radius-*)`，所有 `box-shadow:` 替换为 `var(--shadow-*)`。
3. 排查所有 `color: #` 写法，归位到四档文本色变量。
4. 检查所有 placeholder：颜色是否为 muted？文案是否过长？
5. 检查所有"卡片"：内部是否有分割线？工具栏是否在卡片底部内嵌？
6. 检查所有列表：active 状态是否有三重视觉信号（背景 + 竖条 + 文字色）？
