# Next.js 前端

前端负责浅色极简首页、SSE 思考过程展示、页面创建节点、页面预览和复制链接。

## 交互状态

当前前端分为两种主要状态：

- 未开始生成：左侧展示可折叠 tabbar 和历史创建，主区域保持 Gemini 风格的居中输入框。
- 开始生成后：保留左侧可折叠 tabbar，主区域切换为左右 1:1 分栏，左侧是 LLM 对话流，右侧是页面预览。

左侧 tabbar 贴近视口左边缘，默认折叠，只显示品牌、新对话、搜索和历史入口；点击品牌图标可展开或收起历史列表。输入框优化方向是压薄高度，而不是压窄宽度。

左侧对话流包含：

- 用户需求气泡。提交后输入框会清空，只在对话中保留已提交需求。
- 创建节点：上传文件、解析文件、压缩内容、模型思考、模型输出答案、部署。
- `reasoning_content` 会展示在“模型思考”节点内，默认展开，并支持手动收起。

上传资料当前限制为最多 3 个文件，单文件与单次总大小均不超过 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`pdf`、`txt`、`md`、`html`。PDF 仅保证可复制文本内容的抽取，扫描版图片 PDF 或加密 PDF 可能解析失败。

## 首屏 UI 设计要点

- **品牌主名**："星页 StarPage"。所有面向用户的触点（`<title>` / `meta description` / `og:siteName` / 侧边栏品牌区 / Hero 副标题 / `<img alt>` / systemd Description）统一使用此命名。
- **品牌徽标**：使用 `public/stars-page-logo-simple.png`（简化版，由 `script/preview-logo/process_simple_logo.py` 处理：透明化 + 自动裁剪）。Hero 区域 56×56 直接浮在背景光晕之上，双层 `drop-shadow(0 4px 10px rgba(53,99,233,0.22)) drop-shadow(0 12px 24px rgba(53,99,233,0.12))` 建立空间感，让 logo 在视觉上能"压住"下方 48px 字号 700 字重的大标题。原图 `stars-page-logo.png` 仍保留作为后路，但当前所有页面引用都指向 simple 版。
- **侧边栏 Logo 圆角白底板**：侧边栏 28px logo 在白色 sidebar 底色上"浮不起来"，加 40×40 圆角白底板 + 双层微投影 + 极浅边框，做出 macOS App 图标式的悬浮感。bounding box 严格 40×40、`border-radius: 12px`，与下方 `+` 按钮 / 时钟按钮完全一致 → 展开/收起切换时 logo 容器无任何跳跃。内部 logo 缩到 26×26 留出 7px 内边距。
- **双语品牌文案（侧边栏展开态）**：双层结构 `<span className="brand-name-cn">星页</span><span className="brand-name-en">StarPage</span>`。中文 16px / 800 / `letter-spacing: -0.01em` / primary 色；英文 12px / 700 / `letter-spacing: 0.04em` / secondary 色；容器 `align-items: center` + `line-height: 1` 强制几何中线对齐，避免 baseline 对齐造成文字相对图标偏上 1-2px。
- **Hero 副标题品牌词高亮**：副标题中"星页 StarPage"使用 `<strong className="brand-inline">` 包裹，主题蓝 `var(--color-primary)` + `font-weight: 600`，在长串灰色副标题里形成"一眼锁定"的视觉聚焦点。
- **背景光晕（Hero Aurora）**：固定层叠 3 个 `radial-gradient` 色块（品牌蓝/紫罗兰/天蓝）+ 极淡网格 `mask-image`，搭配缓慢的 `aurora-float-*` 动画，营造专业的空间感；网格通过 `mask-image` 在视觉焦点向外淡出，避免画面"飘"。受 `prefers-reduced-motion` 控制。
- **Prompt Card**：使用半透明白底 + `backdrop-filter: blur(14px) saturate(140%)` 形成玻璃质感浮在光晕之上；阴影改为多层弥散 `0 4px 6px -1px / 0 10px 15px -3px / inset 1px 高光`，比单层 `--shadow-lg` 更轻盈现代。`textarea padding: 12px 12px 10px` 让 placeholder 距卡顶/卡左 30+px，文字"坐"在卡里有呼吸感。底部工具栏只保留"上传资料"按钮和文件类型提示 + 右侧"创建"主 CTA。
- **Prompt Chips**：emoji（🚀 / 📊 / 👤 / 🎉）+ 浅蓝白底圆角卡片按钮，左右 padding `9px 18px`、内部 gap 8px；hover 上浮 -2px、双层阴影（近距品牌蓝 + 远距中性色）；新增 `:active` 反馈让按下感真实。emoji 选择面向年轻白领与学生群体的活泼调性。
- **文件类型提示对比度**：`.file-hint` 颜色从 `--color-text-tertiary` 提到 `--color-text-secondary` (#475569)，强光环境下也清晰可读，达到 WCAG AA。
- **侧边栏垂直节奏**：展开态品牌区与"+ 新对话"操作区间距 ~24px；收起态形成 `[logo] 12px [+] 6px [⌚]` 的 12 : 6 = 2 : 1 黄金分组节奏，把"新建+历史"绑定为紧凑操作组，与品牌锚点自然分层。
- **侧边栏 Active 状态**：当用户处于"新对话"空态时，`.new-chat-button.is-active` 在原本实心蓝色 CTA 之上叠加一圈柔和发光环和脉冲动画 `new-chat-pulse`，明确告诉用户"你在这里"；历史项 active 使用浅品牌色背景 + 左侧 3px 竖条强化定位。
- **历史列表层级**：列表项 `padding: 12px 14px / gap: 6px`；标题 13.5px / 600；日期 11px / 500 / muted (#94a3b8)；标题 vs 日期对比拉满，扫视时一眼锁定标题。
- **滚动条 hover 反馈**：webkit 滑块 6px 宽 + `padding-box` 圆角 + `border: 1px solid transparent`，hover 时 alpha `0.22 → 0.42` 加深一倍，明确"可拖拽"反馈。`.history-list padding-right: 4px / margin-right: -2px` 让 thumb 不紧贴右边。

跨项目复用的设计原则与代码片段沉淀在 `wiki/frontend-design-tokens-and-prompt-card.md`（设计 token / Prompt Card / Header Logo / 侧边栏 / 多层阴影 / Chip Hover / 双语品牌 / 副标题品牌词 / 视觉节奏 / 滚动条 hover 共 11 节）。

右侧预览采用固定 `1200px` 桌面视口宽度渲染 iframe，再整体缩放到预览区域。iframe 高度按预览区域可用高度反算，避免读取生成页整页 `scrollHeight` 导致页面内 `100vh` 被撑大，同时尽量铺满预览卡片。预览容器做成"真实浏览器视窗"：`.preview-window` 外层 1px 细边框 + 弥散阴影，顶部 `.preview-window-bar` 极简控制栏（红黄绿三圆点 + 弱化地址条），缩放舞台用干净浅灰底（去棋盘格）；控制栏在缩放舞台之外，不参与 `scale` 计算。

复制链接在 HTTP 环境下使用降级复制方案，点击后只显示单一反馈文案。
当前成功反馈会直接更新按钮本身：按钮变为绿色并显示“复制成功”。

## 生成视图 UI 要点

提交需求后的工作区视图（thinking / creating / completed）经过两轮精修，要点如下，可复用原则沉淀在 `wiki/ai-generation-progress-ux.md`：

- **创建节点步骤条**：节点圆圈之间有垂直连线——完成=绿色实线、进行中=品牌色向下行进的"流光虚线"(`stepper-flow`)、未开始=浅色实线；进行中节点圆圈内是转圈 `progress-spinner` + 一圈柔光。连线用进度项 `::after` 定位在图标列中心，`top:40px; bottom:-16px` 跨过 16px 卡片间距。
- **状态色克制**：已完成节点回归白底 + 浅边框（不再铺满绿色背景），"成功"只由绿色 Check 圆圈 + 绿色连线表达，把视觉焦点让给右侧预览；进行中保留浅蓝高亮，失败保留红底。
- **"创建节点"标题**：14.5px / 700 / 主文本色 + 左侧品牌色 3px 竖条。**用户需求气泡**：浅蓝描边 + 微阴影 + 右下尖角，"你的需求"标签主题蓝加粗。
- **生成中按钮**：`is-loading` 态——浅蓝底 + 主题色文字 + 转圈 spinner + `cursor: progress`，明确"处理中、不可点"。
- **唯一主 CTA**：完成态下右下"打开页面"是唯一实心主 CTA；底部"迭代修改"入口文案改为"发送"(纸飞机 `SendIcon`)、`is-secondary` 降级为浅蓝底 + 主题色文字。首屏(非 compact)仍保留"创建"主按钮。
- **预览状态点**：动态——生成中蓝色呼吸点(`dot-breathe`) + "生成中"、完成绿点 + "已生成"、失败红点 + "生成失败"。**等待态**用页面线框骨架屏 + `skeleton-shimmer` 流光，而非小图标。
- **思考抽屉**：`.thinking-node-body` 浅灰底 #f9fafb + `inset` 内阴影，内嵌"抽屉"层级。
- **Token 微徽**：`token-meta` 前缀用 `BoltIcon`(⚡) 替代圆点，文字 muted 灰(#94a3b8)，与描述左对齐。
- **侧边栏选中字重**：未选标题 500、`.history-item.active span` 700，用字重对比强化"当前位置"。
- **滚动条**：webkit 5px + 圆角，静止态 alpha 0.16 近隐形、hover 才加深。
- 所有装饰动画（流光 / spinner / 呼吸点 / shimmer）纳入 `prefers-reduced-motion` 降级。

## 首页 ↔ 生成页衔接过渡

首页 hero 与生成工作区是两套条件渲染的 DOM，切换时用一个**命令式过渡协调器**做平滑
衔接，避免「硬闪」。核心在 `app/page.tsx` 顶部的模块级函数与组件内的 `playTransition`：

- **motion 单方案 + 兜底直切**：`runStageTransition()` 中，`prefers-reduced-motion` 或
  motion 库未就绪（动态 import 失败）时直接切换（不报错）；否则用 motion 做 FLIP 输入卡
  飞行 + 文字上浮成气泡 + 内容 stagger 入场。
  > 曾实现「motion → View Transitions → 纯 CSS」三级降级链，因维护成本简化为此；完整版
  > 留档在 git 分支 `full-animation-mode`，取舍原因见 wiki。
- **三处接入**：`handleSubmit` / `startNewChat` / `restoreHistoryItem` 把所有决定新视图
  的 `setState` 包进 `playTransition(() => { ... })` 这一个闭包。
- **稳定舞台**：两态外层包 `<div className="app-stage" ref={stageRef}>`，两态 `<main>`
  各带 `key`（`hero` / `workspace`）。协调器用 `flushSync` 同步提交状态切换，以便 FLIP 在
  切换前后测量位置。
- **motion 动态加载**：`import("motion")` 在 `useEffect` 里异步加载并存入 `motionRef`，
  失败保持为空自动兜底；首屏从 localStorage 恢复非首页态时**不**走过渡（无「从首页来」
  的语境）。
- **动画用内联样式**：动画进行中组件可能因其它 `setState` 重渲染而覆盖 className，所以
  入场/离场全走内联 `style` 或 WAAPI；需要逐项 stagger 入场的容器打 `data-anim-stagger`。
- **DOM 契约集中化**：过渡依赖的选择器/标记集中为 `TRANSITION_DOM` 常量并加注释，未来改
  页面（重命名 class / 调结构）时对照同步，避免动画「静默失效」。

跨项目可复用的设计、降级层级取舍与实现经验沉淀在
`wiki/frontend-home-workspace-transition.md`；选型用的多端口原型在 `script/preview-transition/`。

## 历史记录与本地状态

历史创建列表从后端 `GET /api/conversations` 读取，当前按手机号登录用户查询数据库中的会话、批次和页面节点，因此换设备访问同一账号也能看到自己的历史会话。

侧边栏支持：

- 收藏/取消收藏：调用 `PATCH /api/conversations/{conversation_id}` 更新会话收藏字段。
- 仅看收藏：请求 `GET /api/conversations?favorite_only=true`。
- 检索历史：请求 `GET /api/conversations?q=关键词`，收藏筛选下会同时带上 `favorite_only=true`。
- 删除历史：调用 `DELETE /api/conversations/{conversation_id}` 软删除会话；后端设置 `deleted_at`，列表不再展示。

前端仍使用浏览器 `localStorage` 保存当前设备上的会话细节：

- 当前会话。
- 模型思考内容和创建节点状态。

跨设备点击历史记录时，会根据数据库中的页面、任务、prompt、上传文件名和页面链接重建一个可预览的会话。当前设备上的 `localStorage` 只用于短生命周期 UI 状态，不作为用户历史来源。

## 本地运行

```bash
cd code/frontend
npm install
npm run dev
```

默认会把 `/api/*` 和 `/p/*` 转发到 `http://127.0.0.1:8000`。如需修改后端地址：

```bash
BACKEND_INTERNAL_URL=http://127.0.0.1:8000 npm run dev
```

## Standalone 临时运行注意

使用 `next build` 的 standalone 产物临时运行时，需要把静态资源复制到 standalone 目录，否则页面会退化成裸 HTML：

```bash
npx -p node@22 node node_modules/next/dist/bin/next build
mkdir -p .next/standalone/.next
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/
cp -r public .next/standalone/
HOSTNAME=127.0.0.1 PORT=3000 npx -y -p node@22 node .next/standalone/server.js
```

服务器上已通过 `star-page-frontend.service` 常驻运行：

```bash
systemctl restart star-page-frontend.service
journalctl -u star-page-frontend.service -f
```

重要教训：不要在服务运行中只执行 `npm run build` 后就结束。`next build` 会重新生成 `.next/standalone`，可能清掉运行目录里的 `.next/standalone/.next/static`；此时 HTML 还能返回，但 `/_next/static/*.css` 会 500，页面会退化成裸 HTML。每次构建后必须重启 `star-page-frontend.service`，让 `ExecStartPre` 重新复制 `.next/static`，并用 CSS URL 验证返回 `200 text/css`。

## Docker 镜像构建与推送

镜像推送到阿里云 ACR（个人版，华南 3 广州）：

- 应用镜像仓库：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page`
- tag 约定：`frontend-<git 短 sha>` + `frontend-latest`
- `.dockerignore` 已排除 `node_modules` / `.next`，保证镜像使用 `npm install` 干净安装的依赖（如新增的 `motion`），而不是本地 `node_modules`。

### 基础镜像与「免加速」

`Dockerfile` 的 `FROM` 由 `ARG NODE_IMAGE` 控制，**默认指向 ACR 上预存的基础镜像**
`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/node:22-bookworm-slim`，
所以构建时直接从 ACR 拉 base，不经过 Docker Hub / 镜像加速器。

> 背景：阿里云个人版镜像加速器只对已缓存镜像有效（缺 `node:22` 等较新 tag，拉取报 `not found`），
> 因此把 `node:22-bookworm-slim` 预先上传到 ACR `stars-page/node` 仓库一劳永逸。
> 若本机尚无该 base，可临时用全量回源型加速器（如 DaoCloud `docker.m.daocloud.io`）拉 Docker Hub 镜像。

### 无 ACR 登录环境的处理（重要约定）

默认构建需要先登录 ACR 才能拉到 base 镜像。在**无 ACR 登录**的环境构建时：

1. **先询问用户是否登录 ACR**：
   ```bash
   docker login --username=<阿里云账号> crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com
   ```
2. 仅当**用户明确表示不登录、要求降级**时，才用构建参数回退到 Docker Hub：
   ```bash
   docker build --build-arg NODE_IMAGE=node:22-bookworm-slim -t <repo>:frontend-<sha> .
   ```

不要在未与用户确认的情况下擅自降级到 Docker Hub。

### 构建与推送

```bash
cd code/frontend
ACR=crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page
SHA=$(git rev-parse --short HEAD)

# 默认从 ACR 拉 base（需已登录 ACR）
docker build -t "$ACR:frontend-$SHA" -t "$ACR:frontend-latest" .
docker push "$ACR:frontend-$SHA"
docker push "$ACR:frontend-latest"
```

> 同 VPC 内的 ECS 推送可改用内网域名 `crpi-6w1a91eyh3y1vcd9-vpc.cn-guangzhou.personal.cr.aliyuncs.com` 提速、不耗公网流量。
