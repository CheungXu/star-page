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

## 使用约定

- `doc/` 记录本项目阶段性工作过程、实施状态和问题处理记录。
- 可跨项目复用的通用经验沉淀到 `wiki/`，例如 systemd 常驻部署、生成页预览方案、LLM Provider 抽象原则、设计 token 与输入卡片设计模式、PNG logo 透明化处理。

