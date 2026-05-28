# 生成工作区视图 UI 精修记录（2026-05-29）

## 背景

继首页（idle 首屏）三轮重构之后，本次收到两份针对**生成工作区视图**（即提交需求后进入的 thinking / creating / completed 三态：左侧对话流 + 中栏创建节点 + 右侧页面预览）的设计锐评。两轮目标递进：第一轮解决"层级、空间、质感、状态反馈"的硬伤；第二轮做"克制与精致"——减少冗余色彩、明确唯一焦点、打磨像素细节，让界面从"优秀的后台工具"蜕变为"顶级的 AI 产品"。

## 改造范围

两轮均只触及前端两个文件：

- `code/frontend/app/page.tsx`：新增图标组件、调整进度节点 / 提交按钮 / 预览区 JSX 结构、状态点动态化。
- `code/frontend/app/globals.css`：进度步骤条连线、状态色、加载态、骨架屏、浏览器视窗预览等样式。

后端、数据模型、API、SSE 流程未变动。

---

## 第一轮：层级 · 空间 · 质感 · 状态反馈

| 类别 | 问题 | 处理方式 |
| --- | --- | --- |
| 对比度 | 未激活节点辅助文字偏浅 | 描述统一用 `--color-text-secondary`(#475569，深于建议的 #737373)；未点亮节点改为白底深描边的空心圆 |
| 标题弱化 | "创建节点"标题字号小、字重不足 | 放大到 14.5px / 700 / 主文本色，左侧加品牌色 3px 短竖条强化区域划分 |
| 用户气泡 | "你的需求"区与卡片背景区分度低 | 加浅蓝描边 + 微阴影 + 右下尖角；"你的需求"标签改主题蓝加粗，强化"输入源"语义 |
| 呼吸感 | 步骤卡片排列过紧 | `.progress-list` 间距 10px → 16px，给连线留穿插空间 |
| 底部冗余 | 底部"正在上传并解析文件…"与上方步骤条功能重复 | 移除 compact 形态的 `prompt-status` 渲染，状态统一由步骤条表达 |
| 清空按钮 | 纯文本不够精致 | 加 `CloseIcon` 关闭小图标，hover 变红，升级为次级按钮 |
| 流程感 | 节点像独立卡片堆叠，缺连贯性 | 节点圆圈之间增加垂直连线：完成=绿色实线、进行中=品牌色向下行进"流光虚线"(`stepper-flow`)、未开始=浅色实线；连线定位在图标列水平中心 x≈29px，`top:40px; bottom:-16px` 跨过卡片间距 |
| 进行中节点 | 静态圆点表达力弱 | running 节点内用转圈 `progress-spinner` 替代圆点，并加一圈柔光 |
| 思考抽屉 | 展开区边框生硬 | `.thinking-node-body` 改浅灰底 #f9fafb + `inset` 内阴影，做出内嵌"抽屉"层级 |
| Token 标签 | 像悬浮 Tag、排版随意 | `token-pill` → `token-meta`，从标题行移到卡片描述下方，自然融入 |
| 生成中按钮 | 高亮蓝实心看起来像可点击 | 加 `is-loading` 态：浅蓝底 + 主题色文字 + `SpinnerIcon` 转圈 + `cursor: progress`，明确"处理中" |
| 预览状态点 | 始终绿点，生成中误导成"完成/在线" | 状态点动态：生成中=蓝色呼吸点(`dot-breathe`)、完成=绿、失败=红，并加"生成中/已生成/生成失败"文案 |
| 预览空旷 | 空状态图标过小、重心偏上 | 用更大的"页面线框骨架屏 + 流光动画"(`skeleton-shimmer`)替代小图标，缓解等待焦虑 |
| 无障碍 | — | 所有新增动画纳入 `prefers-reduced-motion` 降级 |

---

## 第二轮：克制 · 精致

| 类别 | 问题 | 处理方式 |
| --- | --- | --- |
| 满屏绿 | 完成后中栏卡片铺满浅绿背景，视觉比重过大，抢走右侧预览焦点 | `.progress-item.completed` 去掉绿底，回归白底 + 默认浅边框；"成功"只由绿色 Check 圆圈 + 绿色连线表达 |
| 双主按钮 | 底部"创建 ⬆"与右下"打开页面"两个高亮主按钮，焦点涣散 | 底部按钮文案 创建 → **发送** + `SendIcon`(纸飞机)，语义贴合"迭代修改"；样式 `is-secondary` 降级为浅蓝底 + 主题色文字；全局唯一主 CTA 留给右下"打开页面"。首屏(非 compact)仍保留"创建"主按钮 |
| 滚动条粗糙 | 思考抽屉默认粗滚动条破坏质感 | webkit 滚动条 6px → 5px，静止态 alpha 0.22 → 0.16(近隐形)，hover 才加深；并把 `.thinking-node-body` 容器也纳入自定义范围 |
| Token 排版 | 前缀小圆点像无序列表默认样式 | 圆点换成 `BoltIcon`(⚡ 闪电微图标)，文字改更弱的 muted 灰(#94a3b8)，与描述文字完美左对齐 |
| 侧边栏选中字重 | 选中项有背景与左竖条，但字重与未选项相同 | 未选标题 600 → 500，`.history-item.active span` 加粗到 700，用字重对比强化"当前位置" |
| 预览画框感 | 深色预览外围一圈厚白边 + 棋盘格，像"挂画"，缺乏浏览器沉浸感 | 包一层 `.preview-window`：1px 细边框 + 弥散阴影 + 顶部极简浏览器控制栏(`win-dot` 红黄绿三圆点 + 弱化地址条)；`.preview-viewport` 去棋盘格改干净浅灰 #f6f7f9；`.preview-scale-shell` 收 10px 圆角 + `overflow:hidden` + 细边框 |

---

## 实施与验证

每轮固定流程：

1. 改 `page.tsx` + `globals.css`。
2. `npm run lint` + `next build` 双校验（仅遗留两处既有 `<img>` 性能 warning，0 error）。
3. **本地可视化验证**：临时加一个 `?__demo` URL 参数触发的 demo seed effect（直接 `applyStoredSession` 注入 thinking / completed 演示会话），用浏览器分别截图中栏对话流与右侧预览，逐项核对。验证后立即删除该 demo 代码。
4. 重新 `next build`，`systemctl restart star-page-frontend.service` 上线，curl 校验 200 + standalone 静态资源含新样式类名。

### 遇到的点

- **dev 端口冲突**：3000 端口被 systemd 生产进程占用，临时调试一律改用 `next dev -p 3100`，全程不影响生产服务。
- **standalone 重启纪律**：构建后必须 `systemctl restart`（其 `ExecStartPre` 会把 `.next/static` 复制进 standalone），不能只 build，详见 `wiki/systemd-nextjs-fastapi-deployment.md` 与 `code/frontend/README.md`。
- **demo seed 的 localStorage 副作用**：演示会话会被会话保存 effect 写入 `localStorage`，导致 `pageUrl="/"` 的预览 iframe 递归渲染出"画中画"。仅 demo 现象，真实使用不会发生；提醒后续做类似验证时，demo 注入应避免落 `localStorage` 或验证后清理。

## 验收

- thinking 态：用户气泡蓝标签 + 阴影；"创建节点"标题带竖条；stepper 连线（绿实线 / 蓝流光 / 浅线）；running 节点 spinner；思考抽屉浅灰底；底部"生成中"加载态；右侧蓝呼吸点 + 骨架屏。
- completed 态：完成节点白底（无满屏绿）；token 行 `⚡ 输出 N tokens` muted 灰左对齐；底部"发送"次级、右下"打开页面"唯一主 CTA；右侧浏览器视窗（三圆点 + 地址条 + 细边框弥散阴影）+ 绿点"已生成"。
- 两轮 `next build` 全绿，服务重启 `active`，HTTP 200。

## 沉淀去向

- 本文档：两轮工作区视图精修完整记录。
- 新增 `wiki/ai-generation-progress-ux.md`：AI 生成类产品的过程可视化与状态反馈 UX 原则（stepper 连线、状态色克制、加载态/呼吸点、唯一主 CTA、骨架屏、token 微徽、选中态字重），可跨项目复用。
- `wiki/generated-page-preview-design.md`：追加"浏览器视窗化外观"一节。
- `code/frontend/README.md`：新增"生成视图 UI 要点"小节并修正预览描述。
