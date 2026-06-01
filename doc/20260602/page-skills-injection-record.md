# 网页制作技能（page-skills）发现与注入 实施记录

## 背景与目标

为提升不同类型展示页（落地页、简历、数据报告等）的生成质量，引入"网页制作技能"机制：在 `skills/page-skills/` 下维护一批轻量技能（设计规范/制作指令），让模型在首次生成与续写时自动应用最匹配的技能。

默认行为：技能能力默认开启；用户未手动选择时由模型自动选择（LLM 路由），模型判定无合适技能则不强加、正常生成；用户手动选择则以手动为准。

## 方案（方案 B，预留演进到 C）

- 技能目录扫描 → 轻量 LLM 路由选技能 → 全量注入生成提示 → 持久化延用。
- 选择器抽象为 `SkillSelector` 接口，方案 B 用 `LlmClassifierSelector`；后续可平滑演进到方案 C（Agentic 工具发现：模型按需 `read_skill`，需为 LLM 客户端补 tools/tool_calls 与生成端多轮循环）。
- 取舍：路由放在 `create_batch` 同步执行（batch 级，一批所有模型一致），给较短超时 + 关键词/None 兜底，避免拖慢与误判阻断生成。

## 已交付能力

- 技能目录与示例：`skills/page-skills/` 约定与 README；技能 `resume`（简历页）、`landing`（产品落地页）、`report`（数据报告页）、`distinctive-design`（强风格设计）、`creative-poster`（创意海报）、`ui-polish`（体验打磨）共 6 个。
- 后端技能服务 `app/services/skills/`：
  - `registry.py`：扫描目录 + 解析 `SKILL.md`（frontmatter YAML + 正文）+ 进程级缓存。
  - `selector.py`：`SkillSelector` 协议 + `LlmClassifierSelector`（非流式 `complete_text` 路由，12s 超时，`triggers` 关键词兜底，再回退 None）。
- 路由接入 `generation_service.create_batch`：优先级 手动具体技能 > 显式关闭(`__none__`) > 续写延用 parent 链路 > 新建自动路由；选定 `skill_key` 写入 batch/page/task。
- 注入：`prompt.build_skill_system_message` 把技能正文包装为一条 system 消息；`_build_llm_messages` 首轮与续写均注入。
- 持久化：迁移 `006_page_skills.sql` 给 `generation_batches`/`pages`/`generation_tasks` 加 `skill_key`；实体同步。
- API：新增 `GET /api/skills`；`POST /api/generations` 支持 `skill_keys`（表单/JSON），响应回带 `skill_key`/`skill_name`。
- 前端 `page.tsx`：拉取 `/api/skills`，新增「网页技能」选择器（自动(默认)/各技能/不使用）+ localStorage 记忆；FormData 透传；工作区展示「技能 · XX」；刷新会话恢复选择与已应用技能。
- 配置：`PAGE_SKILLS_ENABLED`（默认开）、`PAGE_SKILLS_DIR`、`SKILL_ROUTER_MODEL`；`config/env.example` 同步。

## 引入外部开源技能（改编）

参考用户提供的优秀开源 skill，浅克隆到 `skills/ori-page-skills/`（已在根 `.gitignore` 忽略，仅作改造参考），从中筛选适配"自然语言→单文件自包含 HTML 展示页"形态的内容，改编为本项目 `page-skills`：

| 新增技能 | 来源 | 改编要点 |
| --- | --- | --- |
| `distinctive-design` 强风格设计 | Anthropic `frontend-design` | 提炼"先定大胆美学方向 + 字体/配色/动效/构成/背景质感 + 反 AI 套路感"，收敛到单文件、CSS/SVG 自包含、`prefers-reduced-motion`。 |
| `creative-poster` 创意海报 | Anthropic `canvas-design` | 保留"设计哲学先行、90% 视觉/10% 文字、精致工艺、版面安全"，把输出从 PNG/PDF 改为单页视觉 HTML（CSS/SVG 表达，不依赖外部图片）。 |
| `ui-polish` 体验打磨 | Vercel `web-design-guidelines` | 取其规则集中适用于静态 HTML 的子集（a11y/焦点态/动效/排版/内容健壮性/表单/图片/触控/暗色/反馈），剔除 React/Next/构建期专属项。 |

未采用及原因（与本项目形态不匹配）：`figma-implement-design`（需 Figma 输入）、`react-best-practices`（面向 React/Next）、`playwright`/`webapp-testing`（测试执行层）、`vercel-deploy-claimable`（部署）、`brand-guidelines`（绑定特定品牌且面向 PPTX）。OpenAI `frontend-skill` 链接在上游已不存在（curated 列表中无该项）。

### 通用质量基线并入基础系统提示

`distinctive-design`/`ui-polish` 的本质是"对所有页面都成立的质量底线"。为让每个页面（无论是否命中技能）都受益，已把其核心条目提炼进基础系统提示 `HTML_PAGE_SYSTEM_PROMPT`，分两块：

- 设计质量：清晰视觉方向与层级、主色+点缀色的连贯配色（CSS 变量、避免"白底紫渐变"）、有性格的字体与排版节奏、用渐变/几何/SVG/阴影营造质感、动效聚焦入场且 `transform/opacity` + `prefers-reduced-motion`。
- 可访问性与细节：语义化标签 + 必要 ARIA、图片 alt/宽高、标题层级、`:focus-visible` 焦点态与 hover/active 反馈、排版细节（`…`/弯引号/`tabular-nums`/`text-wrap: balance`）、超长/空内容不破版、暗色 `color-scheme`。

层叠关系：基础提示给"底线"；命中 `distinctive-design`（更激进的强风格）或 `ui-polish`（更系统的细则）时，技能正文在底线之上进一步强化，二者互补不冲突。均收敛在"单文件自包含、离线、CSS/SVG 优先、可信 CDN"等硬约束之内。

## 关键决策与注意点

- 续写不重新路由：自动模式下续写沿 parent 链路延用首轮技能，保证同一会话技能一致、省一次路由调用。
- 路由用短的意图 prompt（`title_prompt`/`user_prompt`）而非含上传资料的超长 `model_prompt`，并截断到 2000 字符，省 token、提速。
- 前端"自动"用空串、不透传；"不使用"用 `__none__` 显式关闭，二者在后端区分（自动会路由，关闭不路由）。
- 部署注意：技能目录在仓库根，不在后端 Docker 构建上下文内；容器部署需把技能目录提供给后端并用 `PAGE_SKILLS_DIR` 指向，否则技能列表为空、退化为通用生成。

## 验证

- 技能加载通过：`skills/page-skills` 解析出 6 个技能（含新增 3 个），各正文 0.6–1.4KB 符合轻量约束；关键词路由对 6 类典型需求 + 无命中场景 7/7 正确。`/api/skills` 已注册；`_extract_key`/`_keyword_match` 行为正确。
- 前端 `tsc --noEmit` 与 lint 通过。
- 待联调（依赖线上模型与数据库）：自动命中正确技能、手动覆盖、续写延用、无命中降级。

## 上线记录（2026-06-02）

systemd 常驻部署（不开 `--reload`），改动需重启才生效。本次完整发布并验证：

- `systemctl daemon-reload`（清理 unit 文件变更告警）。
- 数据库迁移：以服务同环境（source `config/{db,oss,llm}.env`）执行 `.venv/bin/python -m app.db.migrate`，`006` 幂等应用，`skill_key` 列就绪。
- 重启后端 `star-page-backend.service`：`/api/skills` 返回 6 个技能（含新增 3 个），基础 prompt 基线与 registry 缓存随之刷新。
- 前端 `npm run build` 后重启 `star-page-frontend.service`：首页 `200`、CSS 资源 `200 text/css`、经前端代理的 `/api/skills` 同样返回 6 个技能。

注意：技能文件增删改后，因 registry 为进程级缓存，必须重启后端才会重新扫描生效。
