# wiki

团队 wiki 知识库，记录可跨项目复用的精华知识。

- 本仓库通用知识：`wiki/`
- 子项目专有知识：`wiki/{project_name}/`

## 条目

- `aliyun-mvp-deployment-checklist.md`：阿里云 MVP 部署检查清单，覆盖轻量服务器、OSS、ACR、RDS、凭证管理与基础验证顺序；含个人版镜像加速器局限、基础镜像预存 ACR、Dockerfile `ARG` 控制 base 来源与「先登录、拒绝才降级」约定。
- `ai-generation-progress-ux.md`：AI 生成 / 长任务类产品的过程可视化与状态反馈 UX 原则——步骤条流程感连线、状态色克制（只强调进行中/失败）、进行中用动效、唯一主 CTA、按钮文案随状态、等待态骨架屏、辅助数据微徽、选中态字重对比、隐形优雅滚动条、动画无障碍降级，共 11 条。
- `frontend-css-grid-and-class-pitfalls.md`：前端 CSS 网格与类名易错点（均为"默认行为+边界数据/视口"才暴露的坑）：动态状态词别直接当 class（会撞全局工具类，用 `is-${status}` 命名空间）、高容器里的 grid 列表项目少时会被 `align-content` 默认 stretch 等高拉伸（改 `align-content:start` + `grid-auto-rows:max-content`）、响应式把多列 grid 降为少列时要显式重排溢出子项的 `grid-column`/`grid-row`；附"用少量数据/空状态/中间视口宽度专门过一遍 + 实测量尺寸定位"的排查定式。
- `frontend-design-tokens-and-prompt-card.md`：前端设计 Token 体系（圆角 / 阴影 / 文本色四档）、对话式输入卡片、Hero Aurora 光晕、Header Logo、侧边栏 Active；以及多层弥散阴影、Chip Hover 双层阴影 + active、侧边栏 Logo 圆角白底板（macOS App 图标式）、双语品牌文案双层结构（中文主名 + 英文副名，几何中线对齐）、副标题品牌词高亮（主题色 + 中粗）、视觉节奏 2:1 黄金分组、滚动条 hover 反馈共 11 节精修原则。
- `frontend-home-workspace-transition.md`：首页 ↔ 工作区两态切换的平滑衔接过渡——用命令式过渡协调器解耦「改状态」与「怎么动画」；含 React 集成 5 个关键点（稳定舞台容器 + key、`flushSync` 同步提交、动态 import、内联样式防 React 覆盖、首屏恢复不走过渡）、FLIP 飞行 / 文字变气泡 / stagger 入场技巧，以及一条重要经验——**降级层级要按项目阶段权衡**（本项目从三级链简化为「motion + 兜底直切」）与 DOM 契约集中化防「静默失效」。
- `generated-page-preview-design.md`：生成页面在主站内预览时的固定桌面视口缩放方案，包含避免窄 iframe 触发移动端布局、避免 `100vh` 被整页高度撑大的经验，以及把预览容器做成"真实浏览器视窗"（细边框 + 弥散阴影 + 极简控制栏，去厚白边）的外观原则。
- `llm-provider-abstraction.md`：LLM Provider 抽象原则，按 OpenAI / Anthropic 协议族接入不同模型供应商，并统一处理重试和空输出；含"多模型目录 + 仅密钥 env + 参数三层覆盖（defaults/params/extra_body）+ 密钥缺失自动不可选"的配置规划与火山方舟 doubao 接入。
- `multi-model-generation-tree.md`：多模型并行生成与生成树结构（会话=树、批次=一轮、Node=Page 可独立分享），含"合并另起新会话避免 DAG"、N 路 SSE 并行进度展示与批次状态聚合、扩展性注意点。
- `multi-model-preview-comparison.md`：多模型结果预览对比设计——复用固定视口缩放、按 N 自适应网格 + 单元聚焦、对比模式加宽预览栏、各单元独立信息与动作、先完成先展示。
- `multi-port-static-preview-for-design-variants.md`：前端"方案 A/B/C 对比"模式，单进程 Python `http.server` + `ThreadingTCPServer` 同时绑定多个端口、每个端口默认入口指向对应方案 HTML，零依赖、零编译、零构建。
- `png-logo-transparent-and-trim.md`：白底 PNG logo 透明化 + 自动裁剪流程，覆盖 GIMP color-to-alpha 算法、近白色伪影清理、按 alpha bbox 裁剪，配套 CSS `drop-shadow` 最佳实践。本仓库另有"轻量阈值法"版本适用于干净白底无伪影的源图，记录在 `script/README.md`。
- `systemd-nextjs-fastapi-deployment.md`：Next.js + FastAPI 早期 MVP 使用 systemd 常驻运行的部署要点。
- `uploaded-document-ingestion-for-generation.md`：上传资料辅助页面生成时的文件抽取、长文本压缩、LLM 重试、调试记录和节点可视化原则。
- `user-scoped-history-persistence.md`：用户维度历史记录持久化原则，说明跨设备历史应落数据库，浏览器本地只保存设备内临时会话状态。

## 给人类看的简明文档

- `for_human/0_初始化配置.md`：新服务器初始化与各类 key 配置步骤。
- `for_human/1_整体架构.md`：当前整体架构说明。