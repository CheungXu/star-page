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

上传资料当前限制为 1 个文件、最大 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`txt`、`md`、`html`。

## 首屏 UI 设计要点

- **品牌徽标**：使用 `public/stars-page-logo.png`（透明背景版，由 `script/process_logo.py` 处理）作为 Hero 区域的真正 Header Logo，116×116 直接浮在背景光晕之上，仅用双层 `drop-shadow` 建立空间感，不再使用之前的胶囊小标签或白色卡片，让首屏有镇场的主干感。侧边栏顶部 Logo 容器统一到 40×40，与下方"新对话""历史"按钮严格对齐到同一垂直中线。
- **背景光晕（Hero Aurora）**：固定层叠 3 个 `radial-gradient` 色块（品牌蓝/紫罗兰/天蓝）+ 极淡网格 `mask-image`，搭配缓慢的 `aurora-float-*` 动画，营造专业的空间感；网格通过 `mask-image` 在视觉焦点向外淡出，避免画面"飘"。受 `prefers-reduced-motion` 控制。
- **Prompt Card**：使用半透明白底 + `backdrop-filter: blur(14px) saturate(140%)` 形成玻璃质感浮在光晕之上；底部工具栏只保留"上传资料"按钮和文件类型提示 + 右侧"创建"主 CTA，没有任何冗余 statusText。
- **Prompt Chips**：作为浅蓝白底圆角卡片按钮（轻量阴影 + 微弱品牌色边框），与下方推荐场景的"可点击卡片"语义对齐。
- **侧边栏 Active 状态**：当用户处于"新对话"空态时，`.new-chat-button.is-active` 在原本实心蓝色 CTA 之上叠加一圈柔和发光环和脉冲动画 `new-chat-pulse`，明确告诉用户"你在这里"；历史项 active 使用浅品牌色背景 + 左侧 3px 竖条强化定位。
- **文件类型提示**：从输入框下方独立行，移到上传按钮右侧作为补充说明，字号 12px / `--color-text-tertiary`，避免占用额外视觉空间。

右侧预览采用固定 `1200px` 桌面视口宽度渲染 iframe，再整体缩放到预览区域。iframe 高度按预览区域可用高度反算，避免读取生成页整页 `scrollHeight` 导致页面内 `100vh` 被撑大，同时尽量铺满预览卡片。

复制链接在 HTTP 环境下使用降级复制方案，点击后只显示单一反馈文案。
当前成功反馈会直接更新按钮本身：按钮变为绿色并显示“复制成功”。

## 历史记录与本地状态

历史创建列表从后端 `GET /api/pages` 读取，当前按默认测试用户查询数据库中的页面和生成任务，因此换设备访问也能看到同一测试账号的历史页面。

前端仍使用浏览器 `localStorage` 保存当前设备上的会话细节：

- 当前会话。
- 模型思考内容和创建节点状态。

刷新页面后会优先恢复当前设备上的会话细节；跨设备点击历史记录时，会根据数据库中的页面、任务、prompt、上传文件名和页面链接重建一个可预览的会话。后续正式多用户版本应将默认测试用户替换为真实登录用户。

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
