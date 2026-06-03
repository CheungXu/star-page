# 首页 UX 第二轮精修记录（2026-06-04）

## 背景

在完成首轮「对话式三层结构」重构（卡片内快捷入口、高级设置栏、页面模板下拉、VS 分隔、创建禁用态、拖拽上传）后，收到 PM/设计向的第二轮反馈，核心问题：

1. **创建按钮**像半透明禁用，主 CTA 引导力弱
2. **快捷指令**像纯文本，缺少可点击暗示
3. **输入区**底部重心左倾，上传 hint 挤占左栏
4. **高级设置**拼凑感强；VS 不符合「多模型集合」心智，且不可扩展
5. **侧边栏** logo 与「新对话」双高亮，导航含混

后续产品决策：**首页去掉「页面模板」手动选择**，技能全部由后端自动路由；工作区仍展示「技能 · xxx」徽章。

## 改动范围

仅前端：

- `code/frontend/app/page.tsx`
- `code/frontend/app/globals.css`
- `code/frontend/README.md`（首屏要点）

后端 API、page-skills 目录、生成逻辑未改。

## 实施摘要

| 问题 | 处理 |
|------|------|
| 创建按钮「残废感」 | 三态：`is-empty` 灰底 `#e2e8f0`；可提交品牌蓝 + hover 上浮；`is-loading` 浅蓝 spinner。去掉 `is-disabled` 半透明盖蓝 |
| 快捷指令不可点 | `.prompt-preset-pill` 浅灰底 + 12px 圆角，hover 品牌浅蓝 |
| 输入区排版 | `.prompt-card-footer` 两端对齐；文件格式说明收进上传按钮 `title` |
| 高级设置拼凑 / VS | `advanced-setting-group` 成组；**删除 VS**；文案改「生成模型」；≥2 个时「已选 N 个 · 将并排生成」 |
| 控件割裂 | 模型 Chip 与下拉统一 36px、`--radius-md`（后下拉已移除） |
| 侧边栏双高亮 | 去掉 `brand-glyph.is-home-active`；收起 idle 仅「新对话」浅底 Active |
| 页面模板 | 移除首页下拉与 `/api/skills` 拉取；提交默认不传 `skill_keys`（自动路由） |

## 首页信息架构（当前）

```
Hero 标题
  └─ prompt-card
        ├─ textarea
        ├─ prompt-presets-inline（空态 pill）
        └─ prompt-card-footer（上传 | 创建）
  └─ prompt-advanced-bar
        └─ advanced-setting-group（仅「生成模型」多选）
```

## 验收

- 空内容创建按钮明确灰色；输入/快捷词/附件后立刻深蓝 CTA
- 高级设置无 VS、无页面模板
- 侧边栏首页仅「新对话」Active
- `npm run lint` / `next build` 通过
- 本地预览：`npx next dev -H 0.0.0.0 -p 3001`

## 沉淀去向

- `wiki/frontend-design-tokens-and-prompt-card.md`：新增「首页对话式精修（第四轮）」
- `code/frontend/README.md`：首屏 UI 要点已同步

## 备注

- 开发环境下左下角黑色 **N** 为 Next.js Dev Tools，非产品 UI。
- 生产部署后若改 standalone，需 `systemctl restart star-page-frontend.service` 并确保 static 复制，见 `wiki/systemd-nextjs-fastapi-deployment.md`。
