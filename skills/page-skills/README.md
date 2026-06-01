# page-skills 网页制作技能

面向「星页 StarPage」网页生成的技能库。每个技能描述某一类展示页（落地页、简历、数据报告等）的设计规范与制作要点，在生成时会被自动或手动选用，并注入到大模型的 system 提示中，从而提升对应场景的生成质量。

## 目录结构

```
skills/page-skills/
  <skill_key>/
    SKILL.md      # 单个技能定义（frontmatter + 正文）
```

- 一个技能一个文件夹，文件夹名建议与 `key` 一致。
- 技能正文应保持轻量（建议 1-3KB），因为会被全量注入到生成提示中。

## 内置技能一览

| key | 名称 | 适用场景 | 来源 |
| --- | --- | --- | --- |
| `landing` | 产品落地页 | 营销/转化型单页，强调首屏冲击与 CTA | 自研 |
| `resume` | 简历页 | 个人简历/求职 CV 展示 | 自研 |
| `report` | 数据报告 | 数据分析/统计报告展示 | 自研 |
| `distinctive-design` | 强风格设计 | 追求高辨识度、避免"AI 套路感"的品牌页/作品集/概念站 | 改编自 Anthropic `frontend-design` |
| `creative-poster` | 创意海报 | 海报/主视觉/封面/邀请函等"视觉为主、文字极简"的设计型单页 | 改编自 Anthropic `canvas-design` |
| `ui-polish` | 体验打磨 | 强调可访问性、交互细节、表单与排版精细度的系统性打磨 | 改编自 Vercel `web-design-guidelines` |

> 上游原始技能克隆在 `skills/ori-page-skills/`（已在根 `.gitignore` 忽略，仅作改造参考）。
> 改编时统一收敛到本项目约束：单文件自包含 HTML、禁外部网络/iframe、CSS/JS 内联、仅可信 CDN、内容轻量。
> 未采用的上游技能（`figma-implement-design` 需 Figma 输入、`react-best-practices` 面向 React/Next、`playwright`/`webapp-testing` 为测试执行、`vercel-deploy-claimable` 为部署、`brand-guidelines` 绑定特定品牌且面向 PPTX）与本项目"自然语言→单文件静态展示页"的形态不匹配，故略过。

## SKILL.md 格式

文件由 YAML frontmatter + Markdown 正文组成：

```markdown
---
key: resume                 # 必填，技能唯一标识（英文/数字/下划线/连字符）
name: 简历页                 # 必填，展示名称（中文）
description: 适合制作个人简历/求职 CV 展示页，强调信息层级、时间线与技能可视化  # 必填，用于路由匹配
triggers: [简历, resume, cv, 求职]   # 可选，关键词兜底（LLM 路由失败时按词命中）
enabled: true               # 可选，默认 true；false 表示停用（不出现在列表与路由中）
---

正文：该类网页的设计规范、信息架构建议、视觉与排版要点、推荐模块与注意事项。
正文是写给大模型看的"专项制作指南"，应聚焦"这一类页面该怎么做得专业"，
不要重复通用规则（如"只输出单文件 HTML""禁止外部请求"等已在系统通用提示中约束）。
```

## 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `key` | 是 | 技能唯一标识，需在所有技能中唯一 |
| `name` | 是 | 中文展示名，前端选择器与「已应用技能」展示用 |
| `description` | 是 | 一句话描述适用场景，是 LLM 路由判断的主要依据，务必清晰可区分 |
| `triggers` | 否 | 关键词列表，作为路由兜底（LLM 不可用或超时时按关键词命中） |
| `enabled` | 否 | 是否启用，默认 `true` |

## 路由与注入机制（简要）

- 用户未手动选技能时，后端用轻量 LLM 路由：把各技能的 `key + name + description` 清单交给模型，让它返回最匹配的 `key` 或 `NONE`。
- 命中后把该技能正文注入生成提示；续写时延用首轮选定的技能。
- 路由失败/超时时回退到 `triggers` 关键词匹配，再回退到不注入（正常生成）。

## 编写建议

1. `description` 要能和其它技能明确区分，避免路由歧义。
2. 正文聚焦该类页面的"专业做法"：信息架构、模块顺序、视觉风格、排版与配色倾向、易错点。
3. 可给出推荐的页面结构骨架（用文字描述模块顺序即可，无需贴大段 HTML）。
4. 保持轻量；大段模板代码不建议放入正文（会增加每次生成的 token 成本）。
