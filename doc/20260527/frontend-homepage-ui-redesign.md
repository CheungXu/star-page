# 首页 UI 重构记录（2026-05-27）

## 背景

收到针对首页的详细设计反馈，集中指出五类问题：

1. **核心输入区结构混乱**：示例文字反客为主，placeholder 像内容而不是提示；卡片内部分割线带来"上下两个区"的错误隐喻。
2. **视觉一致性差**：圆角值混用（胶囊 / 28px / 20px / 999px 同框出现），阴影生硬单层。
3. **色彩与对比度问题**：背景渐变光晕像"屏幕污渍"；辅助文本对比度过低，存在可访问性风险。
4. **侧边栏粗糙**：列表项之间间距过小、缺少 Active/Hover 状态、顶部星号 Icon 孤立。
5. **操作按钮视觉权重不当**：主 CTA 纯黑过于"黑洞"，上传按钮像 Tag 而非操作入口。

## 改造范围

只触及前端两个文件：

- `code/frontend/app/globals.css`：完整重写，引入设计 token 体系。
- `code/frontend/app/page.tsx`：调整 JSX 结构、新增推荐场景 chips、替换图标、统一组件命名。

后端、数据模型、API、SSE 流程未变动。

## 处理思路


| 问题               | 处理方式                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------ |
| placeholder 反客为主 | 缩短文案并用最浅的灰（`--color-text-muted`）渲染，明确"提示"角色；去掉卡片内分割线，工具栏紧贴卡片底部，让整张卡片成为一个清晰输入区        |
| 长串示例占据卡片         | 抽出为 4 个推荐场景 chips（产品介绍页 / 工作汇报 / 个人简历 / 活动邀请）放在卡片下方，点击直接填入 prompt                    |
| 圆角混乱             | 引入 `--radius-xs/sm/md/lg/xl`（8/10/12/16/20px）token；所有按钮统一 12px，卡片 16-20px，胶囊全部下线     |
| 阴影生硬             | 引入 `--shadow-sm/md/lg`（多层柔和阴影）token，并在 focus 状态加 `--shadow-focus` 蓝光圈                |
| 背景"屏幕脏了"         | 移除原 radial 渐变光晕，改为淡蓝顶部氛围 + 24px 网格点阵                                                 |
| 辅助文字对比度低         | 文本色按 primary / secondary / tertiary / muted 分级，所有 hint / 时间戳升级到 secondary 或 tertiary |
| 侧边栏孤立星号          | 顶部加 `sidebar-brand` 行（图标 + "Star Page" 文字 + 收起切换三合一）；hero 区也改为胶囊 logo + 文字           |
| 列表呼吸感 / 状态缺失     | 列表项 padding 重排；新增 hover（浅灰）和 active（蓝色背景 + 左侧 3px 蓝条）状态                              |
| 创建按钮过黑           | 改为品牌蓝 `#3563e9`，加 `ArrowUpIcon` 强化"发送"语义                                             |
| 上传按钮存在感弱         | 描边轻量按钮 + 回形针图标，常驻输入框内左下角                                                             |


## 实施步骤

1. 阅读 `globals.css`、`page.tsx`、`layout.tsx` 与 `package.json`，理解组件结构。
2. 重写 `globals.css`，建立 `:root` token，按设计令牌重新声明所有圆角、阴影、文本色、组件样式。
3. 修改 `page.tsx`：
  - 新增 `AttachmentIcon`、`ArrowUpIcon`；
  - 新增 `PROMPT_PRESETS` 配置（4 个场景），渲染 `prompt-chip` 行；
  - `renderPromptForm` 改写提交按钮 className 与图标；
  - `renderHistorySidebar` 顶部改为 `sidebar-brand`，去掉原"独立星号" toggle 按钮；
  - hero `brand-mark` 改为胶囊 logo + 文字。
4. `npm run lint` + `next build` 双重校验。
5. 用浏览器在 `http://localhost:3002` 验证 idle / sidebar-expanded 两个状态。

## 实施中遇到的问题

### Standalone 旧进程导致 CSS 500，页面"裸奔"

完成重构后通过浏览器访问 `http://localhost:3000`，所有元素退化到浏览器默认样式。

排查发现：3000 端口跑的是 `star-page-frontend.service` 拉起的 standalone 进程，PID 16415，其 cwd 是 `/root/star-page/code/frontend/.next/standalone (deleted)`。我在 dev 调试过程中重新跑过 `next build`，覆盖了 `.next/standalone`，老进程引用的 CSS chunk hash 全部失效，所有 `*.css` 请求返回 500，HTML 仍可返回，触发的就是 wiki 中 `systemd-nextjs-fastapi-deployment.md` 早已记录的"裸 HTML"教训。

处理：`pkill -9 -f "next-server"` 让 systemd 自动拉起新 standalone 进程（PID 17739），新进程读取最新 `.next` 产物，CSS 恢复 200。

正确做法应是 `systemctl restart star-page-frontend.service`，让 service 的 `ExecStartPre` 把 `.next/static` 复制到 `.next/standalone/.next/` 后再启动。

## 验收

- 首页 idle 状态：placeholder 真正像 placeholder、推荐 chips 出现在卡片下方、主 CTA 品牌蓝、背景干净、品牌徽章清晰。
- 侧边栏展开：列表呼吸感改善，hover 与 active 状态明确，顶部 brand 行不再孤立。
- 侧边栏收起：brand-glyph 视觉权重降低，避免与"新对话"主 CTA 抢戏。
- `next build` 0 错误 0 警告，lint 0 问题。

## 沉淀去向

- 通用知识沉淀到 `wiki/frontend-design-tokens-and-prompt-card.md`：设计 token 体系与对话式输入卡片设计模式。
- standalone CSS 500 教训不重复记录，已在 `wiki/systemd-nextjs-fastapi-deployment.md` 和 `code/frontend/README.md` 中存在。

---

# 第二轮迭代（2026-05-27 凌晨）

## 背景

第一轮重构上线后收到第二份"锐评"，集中在 6 个细节硬伤：

1. **输入框底部仍有"描述你想创建的页面"残留**：占位符已经在顶部，底部再放一句小灰字像是没删干净的代码。
2. **Hero Logo 是胶囊小标签**：胶囊样式在 UI 规范里用于"状态/次要"，作为页面核心品牌过于单薄。
3. **侧边栏当前状态不明**：用户处于"新对话"空态时，没有任何视觉提示告诉他"你在这里"。
4. **Chips 像纯文本而非按钮**：白底无背景、缺少卡片感。
5. **文件类型提示孤立成行**：占用一整行视觉空间，应该作为上传按钮的补充说明。
6. **背景过于干净，缺少空间感与品牌氛围**：第一轮把光晕"擦"得太干净。

另外还有一个延伸需求：把原始 logo PNG 的白底处理掉（裁剪 + 透明），让 logo 真正能融入任何背景。

## 实施


| 问题               | 处理方式                                                                                                                                                                                                                        |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 输入框底部残留          | `renderPromptForm` 删除 `<span className="prompt-status">{statusText}</span>` 的渲染；hero 状态下底部只有"上传资料 + 文件类型提示 → 创建"                                                                                                            |
| Hero Logo 单薄     | 删除胶囊样式；先尝试白底卡片（drop-shadow + 圆角），最终用透明 PNG + 双层 drop-shadow 直接浮在背景光晕上，116×116                                                                                                                                               |
| 侧边栏 active 不明    | 新增 `isOnNewChat = status === "idle" && !currentSessionId` 判断，给 `.new-chat-button.is-active` 加柔光环 `box-shadow: 0 0 0 3px rgba(53,99,233,0.18)` + `new-chat-pulse` 缓慢脉冲动画 + `aria-current="page"`，遵循 `prefers-reduced-motion` |
| Chips 像纯文本       | 改为 `rgba(247,249,255,0.85)` 浅蓝白底 + `backdrop-blur(8px)` + `--radius-md` 圆角 + `--shadow-sm`，hover 上浮一格                                                                                                                       |
| 文件提示孤立           | 从下方独立行移到上传按钮右侧；文案精简为 `docx · pptx · xlsx · txt · md · html，单文件 ≤ 50MB`；字号 12px / `--color-text-tertiary`                                                                                                                    |
| 背景光晕单薄           | 新增 `.hero-aurora` 固定层：3 个 radial-gradient 色块（品牌蓝 / 紫罗兰 / 天蓝）+ `blur(90px)` + 缓慢飘动动画 `aurora-float-`*，叠加极淡网格 + `mask-image` 焦点向外淡出                                                                                           |
| Logo 处理          | 新增 `script/process_logo.py`：清理近白色伪影 → GIMP 风格 color-to-alpha → 按 alpha bbox 裁剪。原 1400×752 → 614×577，写入 `image/stars-page-logo-transparent.png` 和 `code/frontend/public/stars-page-logo.png`                                 |
| Prompt Card 玻璃质感 | `background: rgba(255,255,255,0.92)` + `backdrop-filter: blur(14px) saturate(140%)`，让卡片像玻璃浮在光晕之上                                                                                                                            |
| 侧边栏 logo 与按钮对齐   | `.sidebar-brand` 和 `.brand-glyph` 都统一到 40×40，与下方 `.new-chat-button` / `.sidebar-icon-button` 严格在同一垂直中线                                                                                                                      |


## 实施中遇到的问题

### Logo 自动裁剪只裁了右边

第一次跑 `process_logo.py` 输出 1003×752（只裁了右边 397px），左边没动。排查发现原图最右侧有一列 RGB≈228 的浅灰色扫描伪影线，color-to-alpha 给了它约 28 的 alpha，超过裁剪阈值 8，导致这一列被视为"有内容"。

解决：在 color-to-alpha 之前加 `clean_near_white(threshold=235)` 预处理，把 R/G/B 三通道最小值都 ≥ 235 的像素一律置为纯白；并把裁剪阈值从 8 提升到 32。处理后输出 614×577，正方形紧贴星形组合。

### Logo 容器宽高比与 logo 不匹配

第一版 CSS 用 `aspect-ratio: 1400 / 752` 给 brand-mark 加白底卡片样式，但 logo 透明化后实际是 614×577（接近 1:1），原来的 1400/752 比例不再合适。改为 `width: 116px; height: 116px;` 正方形容器，object-fit: contain，并去掉白底卡片背景（透明 logo 直接浮在背景上）。

## 验收

- 主区域：116px 透明 logo 浮在三色 aurora 光晕之上，柔和投影建立空间感；输入框玻璃质感，底部干净；4 个 chips 像可点击卡片。
- 侧边栏：logo / + / 时钟三个 40×40 图标按钮严格对齐；"新对话"按钮处于 active 时有柔光环 + 缓慢脉冲。
- `next build` 0 错误 0 警告，lint 0 问题。

## 沉淀去向

- `wiki/frontend-design-tokens-and-prompt-card.md`：新增 "Hero Aurora 背景光晕"、"Header Logo 透明化优先"、"Active 状态可叠加柔光环 + 脉冲"三节。
- 新增 `wiki/png-logo-transparent-and-trim.md`：跨项目可复用的 PNG logo 透明化 + 自动裁剪流程（color-to-alpha 算法 + 近白伪影清理 + alpha bbox 裁剪）。
- `script/README.md` 与 `code/frontend/README.md` 同步登记本次产物。

---

# 第三轮迭代（2026-05-27 凌晨 — 方案对比、品牌强化、节奏精修）

## 背景

第二轮上线后用户给出"中央 logo 太大、喧宾夺主"的进一步反馈，并要求**同时实现三种处理方案、监听三个不同端口对比**：

- 方案一：大幅简化并缩小 logo（删掉 JS / TS / CSS / `</>` 字符与 4 颗附属小星）
- 方案二：直接去掉中央 logo，让标题与输入框成为唯一焦点
- 方案三：原 logo 缩小 + 半透明，作为氛围水印退到标题之后

随后在方案一选定后做了多轮精修，目标依次是：阴影/留白/对比度 → Chips 风格 → 侧边栏 logo 底板 → 中央 logo 尺寸演进 → 品牌强化 → 视觉节奏。

## 1. 多端口方案对比预览（script/preview-logo/）

为了让用户直接在浏览器对比"同一首页 + 不同 logo 处理"的视觉差异，建立了一套**轻量静态预览基础设施**，避免每个方案都跑一份完整 Next.js dev / build：

- `script/preview-logo/_styles.css`：从生产 `globals.css` 中精简提取首屏需要的 hero / aurora / prompt-card / chips / submit-button 等片段，作为公用样式。
- `preview-a.html / preview-b.html / preview-c.html`：三个 self-contained HTML，分别对应三个方案，顶部带"方案标签"胶囊明确身份。
- `serve.py`：用 Python `http.server` + `ThreadingTCPServer` 同时绑定 3001 / 3002 / 3003 三个端口，每个端口默认入口指向对应预览 HTML，静态资源（CSS、PNG）按相对路径访问。
- `process_simple_logo.py`：把用户提供的简化版 logo 做白底透明化 + 自动裁剪，输出 `stars-page-logo-simple.png`（318×306，64KB）。

**与第二轮 `script/process_logo.py` 的区别**：第二轮用 GIMP 风格 `color-to-alpha` 处理带扫描伪影的设计稿；本轮源图是干净的白底渲染图，直接用**单参数亮度阈值法**（`LUM_HIGH=245 / LUM_LOW=220`）线性过渡 alpha + `getbbox()` 自动裁剪即可，约 30 行代码完成。两套脚本各有适用场景，互不替代。

这套"多端口静态预览"模式独立沉淀到 `wiki/multi-port-static-preview-for-design-variants.md`，可跨项目复用。

## 2. Logo 演进：116 → 44 → 56


| 轮次           | 中央 hero logo   | 侧边栏 logo             | 触发反馈             |
| ------------ | -------------- | -------------------- | ---------------- |
| 第二轮          | 116×116（完整原图）  | 28×28（同图）            | 第三轮反馈"喧宾夺主"      |
| 第三轮 - A1     | 44×44（简化版）     | 28×28（简化版）           | 反馈"压不住 48px 大标题" |
| 第三轮 - A2（最终） | **56×56**（简化版） | 28×28 在 40×40 圆角白底板内 | 视觉重心稳            |


`page.tsx` 中央 hero `<img>` width/height 同步从 56 → 44 → 56。`globals.css` 中 `.brand-mark .brand-logo` 的 width/height 跟随。

简化版 logo `stars-page-logo-simple.png` 与原图 `stars-page-logo.png` 同时保留在 `code/frontend/public/`，前端两处引用都改成 simple 版，原图作为后路。

## 3. 中央输入卡精修


| 维度                          | 之前                                 | 现在                                                      | 设计原理                                |
| --------------------------- | ---------------------------------- | ------------------------------------------------------- | ----------------------------------- |
| 卡片阴影                        | `var(--shadow-lg)`（单层 12-32px 远投影） | `0 4px 6px -1px / 0 10px 15px -3px / inset 1px 高光` 三层叠加 | 多层弥散更现代轻盈，参考 Tailwind UI            |
| 卡片底部 padding                | 12px                               | 18px                                                    | 工具栏与卡底 padding 统一 18px，呼吸感          |
| `textarea` 内 padding        | `6px 4px 8px`                      | `12px 12px 10px`                                        | placeholder 距卡顶/卡左从 24px 推到 30/32px |
| `.file-hint` 颜色             | `--color-text-tertiary` (#64748b)  | `--color-text-secondary` (#475569)                      | 强光环境下也可读，达到 WCAG AA                 |
| `.brand-mark margin-bottom` | 22px                               | 14px                                                    | logo 与 H1 形成更紧凑的"标题组"               |


## 4. Chips：emoji 改 SVG，又改回 emoji

经历了两次互斥的反馈：

1. 第一次反馈："图标风格不一致（emoji 各自风格）"→ 改为 4 个 Feather 风格 SVG（Layers / BarChart / User / Calendar）。
2. 第二次反馈："面向年轻白领与学生，emoji 更活泼"→ 删除 4 个 SVG 组件，恢复 `🚀 / 📊 / 👤 / 🎉`。

**保留下来的优化（emoji / SVG 都适用）**：

- `padding: 9px 14px → 9px 18px`
- `gap: 6px → 8px`
- `transform: -1px → -2px`
- 单层 `var(--shadow-md)` hover 阴影 → 双层 `0 6px 14px -6px rgba(53, 99, 233, 0.35), 0 2px 4px -1px rgba(15, 23, 42, 0.06)`，近距品牌蓝 + 远距中性色。
- 新增 `:active` 反馈：上浮收回到 -1px、阴影减半，按下感更真实。

**教训**：emoji vs 单色线性图标是一个目标用户画像问题，不是单纯的"统一风格"问题。AI 工具面向年轻群体时，emoji 的活泼性 > 风格一致性。

## 5. 侧边栏 Logo 底板（macOS App 图标风）

侧边栏 28px logo 在白色侧边栏底色上"浮不起来"。引入**圆角白底板 + 微投影**方案：

- `brand-glyph` 容器 40×40、`border-radius: 12px`（与下方 `+` 按钮 / `🕐` 按钮**完全一致**），底色纯白 + 极浅边框 `1px solid rgba(15, 23, 42, 0.05)` + 双层阴影 `0 1px 2px / 0 2px 8px`。
- 内部 logo 缩小到 26×26，留出 7px 内边距，避免顶到边角。
- hover 时底板 scale 1.04、阴影染上品牌蓝 `rgba(53, 99, 233, 0.12)`，与 logo 颜色呼应。

**关键约束**：bounding box 严格 40×40、border-radius 12px，与下方按钮一致 → 展开/收起切换时 logo 容器无任何"跳跃"。

## 6. 滚动条 hover 反馈

第二轮的滚动条已经做到"极细 + 圆角 + 半透明"。本轮在此基础上增加：

- `::-webkit-scrollbar-thumb` 用 `padding-box` 圆角 + 1px 透明边框，让滑块视觉更"漂浮"。
- 新增 `::-webkit-scrollbar-thumb:hover`：alpha 从 `0.22 → 0.42`，鼠标悬停时颜色加深，明确"可拖拽"反馈。
- `.history-list` 增加 `padding-right: 4px / margin-right: -2px`，让 thumb 不紧贴右边缘，又不增加 list 占位。

## 7. 历史列表层级

- `.history-list gap: 2px → 4px`
- `.history-item padding: 10px 12px → 12px 14px`，`gap: 4px → 6px`
- `.history-item span`（标题）：保持 13.5px / 600，新增 `letter-spacing: -0.005em / line-height: 1.35`
- `.history-item small`（日期）：`11.5px → 11px`，颜色从 `tertiary` 进一步降到 `muted` (#94a3b8)，`line-height: 1.3`

效果：标题 vs 日期对比拉满，扫视时一眼锁定标题。

## 8. 品牌强化（"星页 StarPage"）

第三轮明确品牌主名为 **"星页 StarPage"**（中文主名 + 英文副名）。改造覆盖：


| 触点                                | 文案                                                                                              |
| --------------------------------- | ----------------------------------------------------------------------------------------------- |
| `<title>`                         | `星页 StarPage · 一句话生成可分享的网页`                                                                     |
| meta description                  | `星页 StarPage —— 用一句话或一份文档，把你的想法变成一个可分享的精致网页。`                                                   |
| `applicationName` / `og:siteName` | `星页 StarPage`                                                                                   |
| `keywords`                        | `星页 / StarPage / AI 网页生成 / 一句话生成网页 / HTML 落地页 / 可分享网页`                                          |
| 侧边栏品牌区                            | 双层结构：`<span className="brand-name-cn">星页</span><span className="brand-name-en">StarPage</span>` |
| Hero 副标题                          | `说说你的想法，<strong className="brand-inline">星页 StarPage</strong> 帮你生成一个可分享的精致网页。`                  |
| `<img alt>`                       | `星页 StarPage`                                                                                   |
| systemd service                   | `Description=星页 StarPage · Next.js Frontend` / `· FastAPI Backend`                              |


**双层品牌文案的视觉权重**：

- 中文 `星页`：16px / 800 / `letter-spacing: -0.01em` / primary 色
- 英文 `StarPage`：12px / 700 / `letter-spacing: 0.04em` / secondary 色
- 容器 `display: inline-flex; align-items: center; line-height: 1`，**不用 baseline 对齐**——baseline 会让文字相对图标偏上 1-2px。强制几何中线居中后整组与图标完美对齐。

**副标题品牌词高亮**：

- `.subtitle .brand-inline`：从 `font-weight: 700; color: --color-text-primary` → `font-weight: 600; color: --color-primary` (#3563e9 主题蓝)。
- 蓝色已经吸睛，weight 从 700 降到 600 反而更平衡，避免"双重加粗"压过整句。

**保持简洁的部分**：placeholder、`statusText`、H1 大标题保留原样，避免品牌名在每个触点重复出现造成"啰嗦"。

## 9. 侧边栏垂直节奏


| 状态  | 节奏                      | 数学                                                                      |
| --- | ----------------------- | ----------------------------------------------------------------------- |
| 展开  | 品牌区 → 操作区 ≈ 23px        | `sidebar-brand margin-bottom: 16px` + divider 1px + flex gap 6px = 23px |
| 收起  | [logo] 12px [+] 6px [⌚] | `sidebar-brand margin-bottom: 12px` + flex gap 6px                      |


收起状态形成 **12 : 6 = 2 : 1** 黄金分组节奏：品牌锚点独立呼吸，"新建 + 历史"绑定为紧凑操作组。

`sidebar-section-divider` 的 `margin: 8px 4px → 0 4px`，所有上下间距由 `sidebar-brand margin-bottom` 集中控制，避免双重 margin 叠加。

## 验收

- 三个预览端口 3001 / 3002 / 3003 同时返回 200，HTML 中可见对应方案标签。
- 生产 [http://localhost:3000：HTML](http://localhost:3000：HTML) 中只引用 `stars-page-logo-simple.png`、中央 `width="56"` / 侧边栏 `width="28"`、`<title>` 与 `meta description` 含品牌主名、`brand-inline` 出现在副标题、`brand-name-cn / brand-name-en` 在侧边栏展开时渲染。
- `next build` + TypeScript 全绿。
- `star-page-frontend.service` 重启 active。

## 实施中遇到的问题

### React 19 + TS 6 下 `JSX.Element` 不再全局可见

把 chip emoji 改为 SVG 时定义了 `Icon: () => JSX.Element`，`next build` 报 `Cannot find namespace 'JSX'`。原因：React 19 + TypeScript 6 下 `JSX` 命名空间不再自动全局暴露，必须从 `react` 显式导入。修法：`import type { ReactElement } from "react"`，类型改为 `() => ReactElement`。

后续 emoji 恢复后这个 import 已被一并删除。

## 沉淀去向

- 本文档：第三轮迭代记录（即本节）。
- `wiki/frontend-design-tokens-and-prompt-card.md`：追加"多层弥散阴影"、"Chip Hover 双层阴影"、"侧边栏 Logo 圆角底板"、"双语品牌文案双层结构（几何中线对齐）"、"副标题品牌词高亮"、"视觉节奏 2:1 黄金分组"、"滚动条 hover 反馈"七节。
- 新增 `wiki/multi-port-static-preview-for-design-variants.md`：多端口静态预览对比模式（Python http.server + ThreadingTCPServer）。
- `code/frontend/README.md`：同步最新首屏 UI 要点（56px logo / 双层品牌文案 / 副标题品牌词 / 节奏 2:1）。
- `script/README.md`：登记 `preview-logo/` 目录的用途。