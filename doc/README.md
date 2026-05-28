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
- `20260529/frontend-transition-animation-plan.md`：首页 ↔ 生成页衔接过渡动画的规划与实施记录。三套方案（纯 CSS / View Transitions / motion）多端口原型对比选型，最终定为「motion 主 → View Transitions 备 → 纯 CSS 兜底 → reduced-motion 直接切换」的运行时三级降级链，并在生产 `page.tsx`/`globals.css` 的三处切换入口集成、lint+build+重启上线、浏览器端到端验证通过。

## 使用约定

- `doc/` 记录本项目阶段性工作过程、实施状态和问题处理记录。
- 可跨项目复用的通用经验沉淀到 `wiki/`，例如 systemd 常驻部署、生成页预览方案、LLM Provider 抽象原则、设计 token 与输入卡片设计模式、PNG logo 透明化处理。

