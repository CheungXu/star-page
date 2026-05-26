# 首页 UI 重构记录（2026-05-27）

## 背景

收到针对首页的详细设计反馈，集中指出五类问题：

1. **核心输入区结构混乱**：示例文字反客为主，placeholder 像内容而不是提示；卡片内部分割线带来"上下两个区"的错误隐喻。
2. **视觉一致性差**：圆角值混用（胶囊 / 28px / 20px / 999px 同框出现），阴影生硬单层。
3. **色彩与对比度问题**：背景渐变光晕像"屏幕污渍"；辅助文本对比度过低，存在可访问性风险。
4. **侧边栏粗糙**：列表项之间间距过小、缺少 Active/Hover 状态、顶部星号 Icon 孤立。
5. **操作按钮视觉权重不当**：主 CTA 纯黑过于"黑洞"，上传按钮像 Tag 而非操作入口。

## 改造范围

只触及前端两个文件：

- `code/frontend/app/globals.css`：完整重写，引入设计 token 体系。
- `code/frontend/app/page.tsx`：调整 JSX 结构、新增推荐场景 chips、替换图标、统一组件命名。

后端、数据模型、API、SSE 流程未变动。

## 处理思路

| 问题 | 处理方式 |
|---|---|
| placeholder 反客为主 | 缩短文案并用最浅的灰（`--color-text-muted`）渲染，明确"提示"角色；去掉卡片内分割线，工具栏紧贴卡片底部，让整张卡片成为一个清晰输入区 |
| 长串示例占据卡片 | 抽出为 4 个推荐场景 chips（产品介绍页 / 工作汇报 / 个人简历 / 活动邀请）放在卡片下方，点击直接填入 prompt |
| 圆角混乱 | 引入 `--radius-xs/sm/md/lg/xl`（8/10/12/16/20px）token；所有按钮统一 12px，卡片 16-20px，胶囊全部下线 |
| 阴影生硬 | 引入 `--shadow-sm/md/lg`（多层柔和阴影）token，并在 focus 状态加 `--shadow-focus` 蓝光圈 |
| 背景"屏幕脏了" | 移除原 radial 渐变光晕，改为淡蓝顶部氛围 + 24px 网格点阵 |
| 辅助文字对比度低 | 文本色按 primary / secondary / tertiary / muted 分级，所有 hint / 时间戳升级到 secondary 或 tertiary |
| 侧边栏孤立星号 | 顶部加 `sidebar-brand` 行（图标 + "Star Page" 文字 + 收起切换三合一）；hero 区也改为胶囊 logo + 文字 |
| 列表呼吸感 / 状态缺失 | 列表项 padding 重排；新增 hover（浅灰）和 active（蓝色背景 + 左侧 3px 蓝条）状态 |
| 创建按钮过黑 | 改为品牌蓝 `#3563e9`，加 `ArrowUpIcon` 强化"发送"语义 |
| 上传按钮存在感弱 | 描边轻量按钮 + 回形针图标，常驻输入框内左下角 |

## 实施步骤

1. 阅读 `globals.css`、`page.tsx`、`layout.tsx` 与 `package.json`，理解组件结构。
2. 重写 `globals.css`，建立 `:root` token，按设计令牌重新声明所有圆角、阴影、文本色、组件样式。
3. 修改 `page.tsx`：
   - 新增 `AttachmentIcon`、`ArrowUpIcon`；
   - 新增 `PROMPT_PRESETS` 配置（4 个场景），渲染 `prompt-chip` 行；
   - `renderPromptForm` 改写提交按钮 className 与图标；
   - `renderHistorySidebar` 顶部改为 `sidebar-brand`，去掉原"独立星号" toggle 按钮；
   - hero `brand-mark` 改为胶囊 logo + 文字。
4. `npm run lint` + `next build` 双重校验。
5. 用浏览器在 `http://localhost:3002` 验证 idle / sidebar-expanded 两个状态。

## 实施中遇到的问题

### Standalone 旧进程导致 CSS 500，页面"裸奔"

完成重构后通过浏览器访问 `http://localhost:3000`，所有元素退化到浏览器默认样式。

排查发现：3000 端口跑的是 `star-page-frontend.service` 拉起的 standalone 进程，PID 16415，其 cwd 是 `/root/star-page/code/frontend/.next/standalone (deleted)`。我在 dev 调试过程中重新跑过 `next build`，覆盖了 `.next/standalone`，老进程引用的 CSS chunk hash 全部失效，所有 `*.css` 请求返回 500，HTML 仍可返回，触发的就是 wiki 中 `systemd-nextjs-fastapi-deployment.md` 早已记录的"裸 HTML"教训。

处理：`pkill -9 -f "next-server"` 让 systemd 自动拉起新 standalone 进程（PID 17739），新进程读取最新 `.next` 产物，CSS 恢复 200。

正确做法应是 `systemctl restart star-page-frontend.service`，让 service 的 `ExecStartPre` 把 `.next/static` 复制到 `.next/standalone/.next/` 后再启动。

## 验收

- 首页 idle 状态：placeholder 真正像 placeholder、推荐 chips 出现在卡片下方、主 CTA 品牌蓝、背景干净、品牌徽章清晰。
- 侧边栏展开：列表呼吸感改善，hover 与 active 状态明确，顶部 brand 行不再孤立。
- 侧边栏收起：brand-glyph 视觉权重降低，避免与"新对话"主 CTA 抢戏。
- `next build` 0 错误 0 警告，lint 0 问题。

## 沉淀去向

- 通用知识沉淀到 `wiki/frontend-design-tokens-and-prompt-card.md`：设计 token 体系与对话式输入卡片设计模式。
- standalone CSS 500 教训不重复记录，已在 `wiki/systemd-nextjs-fastapi-deployment.md` 和 `code/frontend/README.md` 中存在。
