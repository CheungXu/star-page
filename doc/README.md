# doc

日常工作文档，包含分析报告、工作记录等。

按日期组织：`doc/YYYYMMDD/`。

## 当前记录

- `20260522/html-page-builder-architecture-plan.md`：自然语言 HTML 页面生成网站架构规划。
- `20260524/cloud-resource-setup-record.md`：阿里云云资源与服务器运行环境配置记录。
- `20260524/mvp-main-flow-plan.md`：Next.js 前端 + FastAPI 后端的 MVP 主流程实施计划。
- `20260524/mvp-main-flow-implementation-record.md`：MVP 主流程实现记录，包含数据库迁移、LLM 生成、OSS 存储、上传资料处理、页面预览、历史会话、systemd 常驻运行等实施状态。
- `20260527/frontend-homepage-ui-redesign.md`：首页 UI 重构记录，分三轮迭代：
  - 第一轮：按设计反馈引入设计 token 体系、重构输入卡片、新增推荐场景 chips、优化侧边栏状态与对比度。
  - 第二轮：清理输入框残留、Hero Logo 透明化、"新对话"Active 柔光环+脉冲、Chips 卡片化、文件提示位置调整、Aurora 氛围光晕、侧边栏 logo 与按钮对齐。
  - 第三轮：多端口方案对比预览（3001/3002/3003）、中央 logo 116→44→56 演进、多层弥散阴影、Chip Hover 双层阴影、emoji vs SVG 决策、侧边栏 Logo 圆角白底板、品牌强化（"星页 StarPage" 双语品牌文案 + 副标题品牌词高亮 + metadata 全套）、视觉节奏 2:1 黄金分组、滚动条 hover 反馈、文字几何中线对齐。
- `20260529/generation-workspace-ui-polish.md`：生成工作区视图（thinking/creating/completed）UI 精修记录，分两轮：
  - 第一轮（层级·空间·质感·状态反馈）：未激活文字加深、"创建节点"标题放大加粗+竖条、用户气泡阴影+蓝标签、步骤卡片间距加大、移除底部冗余状态、清空按钮加图标、stepper 连线（完成实线/进行流光/未开始浅线）+ running spinner、思考抽屉浅灰底+内阴影、token 标签融入、生成中按钮加载态、预览状态点动态、骨架屏、`prefers-reduced-motion` 降级。
  - 第二轮（克制·精致）：完成节点去满屏绿回归白底、底部按钮"创建→发送"并降级保唯一主 CTA、滚动条进一步细化、Token 圆点→闪电微徽+muted 灰、侧边栏选中项加粗、预览区窗口化（去厚白边、1px 边框+弥散阴影+浏览器三圆点顶栏）。
- `20260529/frontend-transition-animation-plan.md`：首页 ↔ 生成页衔接过渡动画的规划与实施记录。三套方案（纯 CSS / View Transitions / motion）多端口原型对比选型；先集成三级降级链，后因可维护性简化为「motion + 兜底直切」主线（完整三级版留档在 `full-animation-mode` 分支），在生产 `page.tsx`/`globals.css` 三处切换入口集成并验证。
- `20260529/frontend-docker-image-build-and-acr.md`：前端 Docker 镜像构建与 ACR 发布记录。`motion` 依赖经 `npm install` 自动进镜像、补 `.dockerignore`；个人版镜像加速器缺 `node:22` 改用 DaoCloud 拉取并预存基础镜像到 `stars-page/node`；Dockerfile 用 `ARG` 默认走 ACR、可降级 Docker Hub；ACR 命名最终更正为 `stars-page`，镜像清单与 tag 约定。
- `20260531/upload-pdf-and-backend-image-record.md`：上传资料支持最多 3 个文件与 PDF 文本抽取的实施记录；包含“点击生成时再上传”的取舍、`markitdown[pdf]` 新依赖、后端 Dockerfile 使用阿里云 PyPI 源、`backend-7d58e50-pdf` / `backend-latest` 镜像推送、Next standalone CSS 静态资源恢复和后端重启加载新白名单等问题处理。
- `20260531/multi-model-parallel-generation-plan.md`：多模型并行生成实施计划（Scheme A：会话=生成树、Node=Page、合并另起新会话）。新对话多选模型并行生成、预览并排对比、支持从节点多轮续写，合并预留 schema；模型走"可提交模型目录 + 三层参数覆盖（defaults/params/extra_body）+ 密钥缺失自动不可选"的配置规划。
- `20260531/multi-model-parallel-generation-implementation-record.md`：上述规划的实施记录。含已交付能力、与规划的增量决策（续写两种语义=并行续写/分支、续写改为"指令全带+只裁最近一版答案"的内容感知、max_tokens 上调到 65536）、过程中发现并修复的 4 个问题（状态词撞类名、并行续写血缘、中等宽度布局塌陷、侧栏历史项被网格拉伸）、线上验证与当前限制。
- `20260602/generated-page-js-css-sandbox-record.md`：为展示页安全放开 CSS/JS 的实施记录。安全范式从"清洗掉 JS"切换为"隔离优先"（沙箱 CSP + sandboxed iframe + `connect-src 'none'` 默认禁网 + 可信 CDN 白名单）；顺带把页面访问链接升级为 `/p/{conversation_id}/{page_id}` 并修复删会话后页面仍可访问。
- `20260602/generated_page_js_sandbox.plan.md`：上述实施对应的方案归档（plan 同步留档便于回溯）。
- `20260602/page-skills-injection-record.md`：网页制作技能（page-skills）发现与注入实施记录。方案 B（技能目录扫描 + 轻量 LLM 路由 + 全量注入 + 持久化延用，选择器抽象预留演进到 Agentic 方案 C）；含默认开启/未选由模型自动选/可手动覆盖与显式关闭、续写延用 parent 链路、batch 级一次路由、路由短超时+关键词兜底、技能目录不在后端构建上下文的部署注意点。
- `20260604/frontend-homepage-ux-round2-record.md`：首页 UX 第二轮精修（创建三态、preset pill、高级设置成组去 VS、侧边栏单一 Active、移除首页页面模板选择）。
- `20260610/production-https-and-ux-defaults.md`：生产 HTTPS 上线、首屏默认双模型勾选与 ICP 备案号展示状态汇总。
- `20260610/icp-filing-footer-implementation.md`：ICP 备案号首页页脚实施记录（idle 静态沉底、工作区不展示、systemd 上线验证）。
- `20260614/billing-system-plan.md`：星页积分计费系统设计方案（积分单位、匿名策略、复式记账、接口规划）。
- `20260614/billing-system-implementation-record.md`：星页积分计费系统实施记录——已交付的数据/会计内核、匿名→注册闭环、生成链路计费、用户购买页与管理员财务后台，安全加固要点、上线验证结果与后续可迭代项。
- `20260614/domestic-llm-pricing-and-integration.md`：国内旗舰大模型定价调研、百炼主路接入方案与模型分档建议。
- `20260614/domestic-llm-integration-record.md`：上述方案的实施与上线记录——GLM-5.2 / Kimi K2.7 Code 落地、百炼 model ID 坑（Kimi 勿用 `kimi/` 前缀）、M3 暂缓、探测脚本与 systemd 重启验证。
- `20260615/billing-finance-enhancements-record.md`：计费财务后台增强记录——财务总览三段式+营业利润、模型倍率可视化配置（即时生效）、**预付费记账改造（应付→预付资产）**、供应商真实余额对账（阿里云/火山）、阿里云账单基础设施成本入账与百炼成本偏差对账，含成本口径（应付 vs 现金支付）决策。
- `20260617/wechat-native-pay-integration-plan.md`：微信 Native（PC 扫码）支付充值接入方案与进展——Native 选型、微信支付公钥验签、`wechatpayv3` 接入、1002 应收第三方支付记账口径 + 手动结算入口 + 资金账单自动对账、回调安全与查单兜底、交付范围与所需凭据。
- `20260627/sidebar-account-area-polish-record.md`：侧边栏账号区交互与排版精修记录——折叠态用户头像/空白区展开、展开宽度 252px、底部积分卡片与账号行重排、线上构建重启和静态资源验证。

## 使用约定

- `doc/` 记录本项目阶段性工作过程、实施状态和问题处理记录。
- 可跨项目复用的通用经验沉淀到 `wiki/`，例如 systemd 常驻部署、生成页预览方案、LLM Provider 抽象原则、设计 token 与输入卡片设计模式、PNG logo 透明化处理。

