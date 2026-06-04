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

---

## 2026-06-04 追加：首屏输入区精修与上线

### 背景

继续围绕首页首屏做产品、设计、交互层面的精修。核心判断从「把配置都露出来」调整为「让输入成为焦点，把低频配置收起」：

1. 原模型选择常驻在输入卡片底部，造成卡片臃肿、空洞感明显。
2. 模型选择向上弹出会遮挡输入框，影响用户继续编辑。
3. 上传文件是产品卖点之一，但纯图标入口不够明确。
4. 灵感建议上方左对齐，与整体居中 hero 结构不一致。

### 当前首页信息架构

```
Hero Logo / 标题 / 副标题
  └─ prompt-card（轻量输入条）
        ├─ textarea（2 行起）
        └─ composer-toolbar
              ├─ composer-upload（上传文件）
              ├─ model-picker（常态 pill，点击向下弹出）
              └─ composer-send（圆形提交按钮）
  └─ prompt-inspirations（居中灵感 chips，空态显示）
```

### 产品与设计决策

| 问题 | 决策 | 原因 |
|------|------|------|
| 输入框臃肿 | 模型列表从常驻基座改为 `model-picker` 弹出浮层 | 低频配置不应长期占用主输入区空间，输入本身应是焦点 |
| 浮层遮挡输入 | 模型浮层改为向下弹出 | 工具栏在输入卡片底部，向上弹必然遮挡 textarea；向下弹只覆盖辅助内容，不影响输入 |
| 上传入口不明显 | 上传从纯图标升级为「上传文件」文字胶囊 | 上传文件是核心路径，需要可发现、可理解，而不是隐藏在工具图标里 |
| 副标题过度营销 | 副标题改为「说说你的想法，或上传文件，星页 StarPage 帮你生成可对比的网页。」 | 保持简洁，品牌高亮仍落在「星页 StarPage」，避免卖点文案抢主标题 |
| 灵感 chips 割裂 | chips 移到输入卡片下方并居中 | Hero 视觉轴线统一：Logo → 标题 → 副标题 → 输入 → 灵感 |

### 交互与视觉细节

- `prompt-card` 聚焦态增加流动渐变描边：蓝 / 紫 / 天蓝渐变边环仅在 focus 时浮现，并受 `prefers-reduced-motion` 控制。
- `prompt-inspirations` 首屏使用错峰浮现：引导词与 4 个 chip 依次自下而上淡入。
- `composer-upload` 使用品牌蓝弱底 + 描边 + 回形针图标 +「上传文件」文字，视觉权重高于模型选择器。
- `model-picker` 常态只显示一个紧凑 pill；展开浮层为毛玻璃卡片，带模型色点与选中勾。
- `composer-send` 使用圆形品牌 CTA，空态灰禁用，输入后切换为蓝色渐变按钮。

### 上线验证

- `npm run build` 通过。
- `systemctl restart star-page-frontend.service` 已执行。
- 首页 `200`，静态 CSS `200 text/css`。
- 公网入口：`http://8.138.118.232/`。

### 后续可选

- 若要进一步强化上传卖点，可考虑首次访问一次性轻脉冲、拖拽文件进入页面时全局高亮、或上传后展示「将结合文件内容生成」的状态说明。
