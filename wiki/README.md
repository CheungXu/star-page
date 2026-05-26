# wiki

团队 wiki 知识库，记录可跨项目复用的精华知识。

- 本仓库通用知识：`wiki/`
- 子项目专有知识：`wiki/{project_name}/`

## 条目

- `aliyun-mvp-deployment-checklist.md`：阿里云 MVP 部署检查清单，覆盖轻量服务器、OSS、ACR、RDS、凭证管理与基础验证顺序。
- `frontend-design-tokens-and-prompt-card.md`：前端设计 Token 体系（圆角 / 阴影 / 文本色四档）、对话式输入卡片设计模式、Hero Aurora 氛围光晕、Header Logo 与侧边栏 Active 状态设计要点。
- `generated-page-preview-design.md`：生成页面在主站内预览时的固定桌面视口缩放方案，包含避免窄 iframe 触发移动端布局和避免 `100vh` 被整页高度撑大的经验。
- `llm-provider-abstraction.md`：LLM Provider 抽象原则，按 OpenAI / Anthropic 协议族接入不同模型供应商，并统一处理重试和空输出。
- `png-logo-transparent-and-trim.md`：白底 PNG logo 透明化 + 自动裁剪流程，覆盖 GIMP color-to-alpha 算法、近白色伪影清理、按 alpha bbox 裁剪，配套 CSS `drop-shadow` 最佳实践。
- `systemd-nextjs-fastapi-deployment.md`：Next.js + FastAPI 早期 MVP 使用 systemd 常驻运行的部署要点。
- `uploaded-document-ingestion-for-generation.md`：上传资料辅助页面生成时的文件抽取、长文本压缩、LLM 重试、调试记录和节点可视化原则。
- `user-scoped-history-persistence.md`：用户维度历史记录持久化原则，说明跨设备历史应落数据库，浏览器本地只保存设备内临时会话状态。

## 给人类看的简明文档

- `for_human/0_初始化配置.md`：新服务器初始化与各类 key 配置步骤。
- `for_human/1_整体架构.md`：当前整体架构说明。