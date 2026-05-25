# Next.js 前端

前端负责浅色极简首页、SSE 思考过程展示、页面创建节点、页面预览和复制链接。

## 交互状态

当前前端分为两种主要状态：

- 未开始生成：保持 Gemini 风格的居中窄胶囊输入框。
- 开始生成后：切换为左右 1:1 分栏，左侧是 LLM 对话流，右侧是页面预览。

左侧对话流包含：

- 用户需求气泡。提交后输入框会清空，只在对话中保留已提交需求。
- 创建节点：上传文件、解析文件、压缩内容、模型思考、模型输出答案、部署。
- `reasoning_content` 会展示在“模型思考”节点内，默认展开，并支持手动收起。

上传资料当前限制为 1 个文件、最大 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`txt`、`md`、`html`。

右侧预览采用固定 `1200px` 桌面视口渲染 iframe，再整体缩放到预览区域。这样可以尽量还原用户单独打开页面时的桌面视觉效果，避免因为预览区域较窄触发生成页面的移动端布局。

复制链接在 HTTP 环境下使用降级复制方案，点击后只显示单一反馈文案。
当前成功反馈会直接更新按钮本身：按钮变为绿色并显示“复制成功”。

## 本地状态保存

前端使用浏览器 `localStorage` 保存：

- 当前会话。
- 最近历史创建记录。
- 已完成页面链接、模型思考内容和创建节点状态。

刷新页面后会恢复当前会话；只有点击左侧“新对话”按钮才会回到初始首页。该方案适合当前 demo，后续正式多用户版本应改为从后端用户页面列表读取历史记录。

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
