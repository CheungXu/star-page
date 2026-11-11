# wiki

团队 wiki 知识库，记录可跨项目复用的精华知识。

- 本仓库通用知识：`wiki/`
- 子项目专有知识：`wiki/{project_name}/`

## 条目

- `aliyun-mvp-deployment-checklist.md`：阿里云 MVP 部署检查清单，覆盖轻量服务器、OSS、ACR、RDS、凭证管理与基础验证顺序；含个人版镜像加速器局限、基础镜像预存 ACR、Dockerfile `ARG` 控制 base 来源与「先登录、拒绝才降级」约定。
- `cursor-remote-agent-egress-and-model-region.md`：Cursor Remote SSH 窗口按出口 IP 隐藏 Gemini / Claude / GPT 的原因与改回本机转发的做法。含「IDE 浏览器出口 ≠ 主机出口」、Gemini 网页版与 API 名单不同、`cursorAgentHost.remoteInferenceRoute=always` 写在远程 `Machine/settings.json` 且需重新加载窗口。
- `credit-billing-and-double-entry-accounting.md`：AIGC/按次消耗类产品的积分计费 + 复式记账 + 匿名风控通用经验——积分整数双桶(充值/赠送)、ceil 取整 + 最小 1 兜底、积分=预收账款且消费时确认收入的复式分录、**供应商预付费用「预付资产」而非「应付负债」(误用会让资金状况反向)**、财务总览三段式 + 营业利润、全链路幂等(idempotency_key + 凭证唯一 + 原子状态流转 + 借贷必平)、匿名用真实用户行建模 + 读写两套 actor 解析、签名 cookie + IP 天花板 + 后端强校验模型白名单的分层围栏、价格服务端权威 + mock 生产禁用、**供应商真实余额/账单对账(BSS QueryAccountBalance/QueryBillOverview、火山 QueryBalanceAcct、按平台域名归集多家模型做偏差对账、成本取应付而非现金口径)**，以及「服务内 commit 污染测试库 / 默认模型含旗舰被白名单误伤 / lru_cache 配置需 cache_clear / router 重复 prefix」等坑。
- `aliyun-ssl-cert-nginx-deployment.md`：阿里云 SSL 证书在 ECS + Nginx 上的自动部署流程；含证书路径约定、`fullchain` 合并脚本、`www` 归一根域、HSTS、`PUBLIC_BASE_URL` 与 iframe 同源、续期检查清单。
- `ai-generation-progress-ux.md`：AI 生成 / 长任务类产品的过程可视化与状态反馈 UX 原则——步骤条流程感连线、状态色克制（只强调进行中/失败）、进行中用动效、唯一主 CTA、按钮文案随状态、等待态骨架屏、辅助数据微徽、选中态字重对比、隐形优雅滚动条、动画无障碍降级；第 12 条是多模型多轮场景下的"进度回复气泡"（展开收起同宽、点对话栏回看某轮、来源写在需求气泡上、历史用时取服务端时间）；第 13 条是选择下一轮起点时保留已写好的草稿；第 14 条是「排队中」（写明原因、不计用时）与「已停止」（中性灰、不按失败）的呈现以及发送按钮原地变成停止。
- `frontend-css-grid-and-class-pitfalls.md`：前端 CSS 网格与类名易错点（均为"默认行为+边界数据/视口"才暴露的坑）：动态状态词别直接当 class（会撞全局工具类，用 `is-${status}` 命名空间）、高容器里的 grid 列表项目少时会被 `align-content` 默认 stretch 等高拉伸（改 `align-content:start` + `grid-auto-rows:max-content`）、响应式把多列 grid 降为少列时要显式重排溢出子项的 `grid-column`/`grid-row`；以及纵向滚动 flex 容器里带裁剪的子项被压扁（`flex-shrink:0`）、`flex:1` 让按内容取宽失效、flex 容器吃掉文字空格、窄空间标签整个隐藏、父级 `:last-child` 盖掉子组件状态色、贴底窄输入条的浮层改为 `position:static` 以整张卡片向上等宽弹出、窄屏文档被撑宽时 `fixed; inset:0` 遮罩超出屏幕（改 `width:100vw; right:auto`）、`<label>` 内同时包输入框与其它按钮会抢焦（改 `htmlFor` 只包标题文字）；附"用少量数据/空状态/中间视口宽度专门过一遍 + 实测量尺寸定位"的排查定式。
- `document-first-ai-input-card-design.md`：文档与文字共用一张输入卡。上传是强调过的副入口，书写区只略高一档（约 8px）；有色块、虚线框和按钮比同样高的空白输入框更重，不能靠把输入框拉得很高来体现「为主」。
- `frontend-design-tokens-and-prompt-card.md`：前端设计 Token 体系（圆角 / 阴影 / 文本色四档）、对话式输入卡片、Hero Aurora 光晕、Header Logo、侧边栏 Active；以及多层弥散阴影、Chip Hover 双层阴影 + active、侧边栏 Logo 圆角白底板（macOS App 图标式）、双语品牌文案双层结构（中文主名 + 英文副名，几何中线对齐）、副标题品牌词高亮（主题色 + 中粗）、视觉节奏 2:1 黄金分组、滚动条 hover 反馈。模型菜单向下弹出时，下方 chips 的让位高度和浮层宽度都要按当前模型数量实量，不能沿用旧像素。
- `frontend-home-workspace-transition.md`：首页 ↔ 工作区两态切换的平滑衔接过渡——用命令式过渡协调器解耦「改状态」与「怎么动画」；含 React 集成 5 个关键点（稳定舞台容器 + key、`flushSync` 同步提交、动态 import、内联样式防 React 覆盖、首屏恢复不走过渡）、FLIP 飞行 / 文字变气泡 / stagger 入场技巧，以及一条重要经验——**降级层级要按项目阶段权衡**（本项目从三级链简化为「motion + 兜底直切」）与 DOM 契约集中化防「静默失效」。
- `generated-page-preview-design.md`：生成页面在主站内预览时的固定桌面视口缩放方案，包含避免窄 iframe 触发移动端布局、避免 `100vh` 被整页高度撑大的经验，以及把预览容器做成"真实浏览器视窗"（细边框 + 弥散阴影 + 极简控制栏，去厚白边）的外观原则。
- `generated-page-js-sandbox-and-security.md`：LLM 生成页放开 CSS/JS 时的"隔离优先"安全原则——不清洗用户 JS 而用浏览器原生沙箱隔离；两道独立的墙（`sandbox` 不透明 origin 防偷主站凭证、`connect-src 'none'`/`form-action 'none'` 防钓鱼/信标）、sandbox 管不到 fetch 的关键认知、无需新域名的响应头落地方式、清洗定位转变、默认禁网 + 未来 opt-in 与独立内容域名的演进。
- `koc-distribution-and-anti-ban-sop.md`：通告平台 KOC 派单冷启动与防封引流方法论——物料有限时先「少量垂直中号一人一套」测款再放量；渠道梯队（螃蟹/红通告等通告小程序、数据商、蒲公英、避开野生兼职与已下架代发 App）；合规与 UTM 前置、30 秒筛号、防查重交付纪律、正文去公关化与置顶评论引导、双端对账结算。
- `llm-provider-abstraction.md`：LLM Provider 抽象原则，按 OpenAI / Anthropic 协议族接入不同模型供应商，并统一处理重试和空输出；含"多模型目录 + 仅密钥 env + 参数三层覆盖（defaults/params/extra_body）+ 密钥缺失自动不可选"的配置规划、火山方舟 doubao 接入，以及**百炼托管第三方模型的 model ID 约定**和**模型换代检查清单**（先区分「未开通」与「ID 不存在」，空间授权成功不等于商品已开通，裸名和日期快照单价不同就是两个商品，思考模型探活要留足输出额度，高峰/低峰只按高峰入账，槽位 key 与展示名可以分开，现网用公网模型列表核对）。
- `multi-model-generation-tree.md`：多模型并行生成与生成树结构（会话=树上的一条线、批次=一轮、Node=Page 可独立分享），含"从更早一轮继续 = 自动分叉成新会话（继承不复制、指针指向真正主人、删除按引用保留、并行续写只看最近一轮以支持每轮自由换模型）"、"合并另起新会话避免 DAG"、生成交给后台执行而不挂在浏览器连接上、一轮一条可续传事件流、显式停止与重启自愈、批次状态聚合、多轮回看与三种来源标识（新建/基于/各自续改）、节点 `duration_ms` 持久化、恢复时不自动预填待续改基点、选基点不清空已写好的修改说明。
- `product-ops-metrics-and-north-star.md`：产品运营指标体系与北极星方法论（创作/分享/按次消耗类产品通用）——北极星用「价值闭环复合指标」而非 DAU/注册数、11 个指标域分层、明细+聚合两层数据架构与读取分流、埋点采集(白名单+限频+sendBeacon 静默+IP 仅哈希)，以及「注册用户须与匿名分开、区间 UV/distinct 不可逐日相加、聚合总量行与维度行分存、cohort 未到期 period 留空、活跃口径去重、成功率分母口径统一」等坑。
- `multi-model-preview-comparison.md`：多模型结果预览对比设计——复用固定视口缩放、1-2 个并排、3 个以上大舞台加缩略条、左侧进度与右侧联动、各单元独立信息与动作、先完成先展示；大舞台不放浮层的三种操作位置（顶栏 / 胶片焦点卡 / 聚光胶片）、焦点卡按列对齐与标题竖向居中的排版细节、胶片条滚轮横滑、展开收起同时过渡、滑动高亮底板、大预览点击即淡入不等 iframe 加载。
- `share-card-customization.md`：可改标题的分享卡片——一个「分享」按钮完成发布、贴按钮弹出的浮层（分享图整列、链接只留复制按钮、按钮与对齐开关的顺序）、用户改写值与生效值分存且重新发布保留、PATCH 只改出现字段、防抖保存与关闭时 keepalive 补存、前端 canvas 与后端 Pillow 同版式（Noto CJK 要用简体字形索引）、自定义标题时连 `<title>` 一起替换、分享埋点带 AB 分组。
- `multi-port-static-preview-for-design-variants.md`：前端"方案 A/B/C 对比"模式——静态视觉对比用单进程 Python `http.server` + `ThreadingTCPServer` 多端口绑定；复杂 React 交互对比用同一 Worktree + `NEXT_DIST_DIR` 多端口 `next dev` + `cp -al` 硬链接 `node_modules`（绕过 Turbopack 软链根路径限制）+ 确定性 Demo URL 参数（`?demo=N&rounds=R&at=S`）；选定后先打标签存档再清理对比工具、主线只留有 AB 计划的备选并写进 `alternatives/` 备选方案库；以及同一文件混用编辑器工具与外部脚本写盘会被旧缓存覆盖的坑。
- `png-logo-transparent-and-trim.md`：白底 PNG logo 透明化 + 自动裁剪流程，覆盖 GIMP color-to-alpha 算法、近白色伪影清理、按 alpha bbox 裁剪，配套 CSS `drop-shadow` 最佳实践。本仓库另有"轻量阈值法"版本适用于干净白底无伪影的源图，记录在 `script/README.md`。
- `real-screenshot-promo-assets-and-html-video.md`：用真实界面做宣传素材。AI 封面后必须跟真实截图；记录了无头浏览器截图的坑（emoji 字体、屏幕外 iframe、坐标换算），用 hyperframes 把截图做成配音讲解视频的规范要点，中文 TTS 读网址的问题，以及低配服务器上的渲染耗时。
- `scene-positioning-framework.md`：生成类产品的场景化增长与落地框架——配置驱动解耦场景、拒绝生硬表单向导的“Prompt 阈值预填 + 素材槽位分解”交互范式、所见即所得真实案例渲染与主体点击直达、节点链接 vs 固定发布链接双层架构、Shadow DOM 隔离的自传播浮标与微信长图海报服务端生成，以及场景端到端闭环度量。
- `sse-background-job-streaming.md`：长任务后台执行 + SSE 可续传进度——执行与观看分离（执行不能挂在请求上）、原子认领 / 个人 + 全局两层先来后到名额（可调上限的闸门、入队须同步、排队凭证中途作废、排队中可见且不计用时） / 兜底写终态 / 开跑前取消不走 finally / 两段式停止 / 退出不改状态 + 启动扫尾；事件流每任务一个游标（全局自增序号不等于提交顺序）、终态与收尾事件同事务、续传先补已结束集合、碎片合并与唤醒先取 waiter；EventSource 遇非 200 永久关闭需手动退避重连、中间层 gzip 攒住实时事件（`no-transform`）、开发代理测不出断线、HTTP/2 与 6 连接限制、uvicorn 优雅退出等 SSE；附验证清单。
- `skill-discovery-and-injection.md`：技能（Skill）发现与注入的通用做法——技能目录(SKILL.md=元数据+正文) + 选择器(关键词/轻量 LLM 路由/Agentic 工具发现三范式) + 注入(包装成 system 叠加)；含默认开启+自动选+手动覆盖、路由兜底不可阻断、batch 级一次定调、持久化多轮延用、渐进式披露省成本、选择器做接口平滑演进(B→C)、技能目录不在构建上下文的部署陷阱。
- `site-icp-filing-footer.md`：网站 ICP 备案号首页页脚展示——合规要点、fixed vs 静态沉底取舍、SPA idle/工作区分离策略与视觉克制原则。
- `systemd-nextjs-fastapi-deployment.md`：Next.js + FastAPI 早期 MVP 使用 systemd 常驻运行的部署要点；含 standalone 静态资源教训、"代码更新需重启（无 `--reload`）"、SSE 长连接导致停止卡 90s 应设 `TimeoutStopSec`（强杀安全的前提是后台任务退出不改状态、靠启动扫尾）、`print` 日志在非终端下被缓冲需 `flush=True`。
- `training-consent-discount-and-page-moderation.md`：AIGC 产品的训练授权、授权优惠与公开页举报通用做法——「用户当前开关 × 单条数据快照」双重判定、把生成产出拆成「内容许可 / 当场提取的匿名结构样本 / 去标识化原文」三层以兼顾撤回权与防薅羊毛、新用户/老用户/匿名访客三套同意路径（首页页脚只同意协议，训练授权不写在页脚）、用户侧只展示积分与优惠而隐藏真实金额成本、CSP 禁网公开页经主站 `/report` 举报与 451 下架，以及同机 worktree 预览的库与端口隔离要点。
- `uploaded-document-ingestion-for-generation.md`：上传资料辅助页面生成时的文件抽取、长文本压缩、LLM 重试、调试记录和节点可视化原则。
- `user-scoped-history-persistence.md`：用户维度历史记录持久化原则，说明跨设备历史应落数据库，浏览器本地只保存设备内临时会话状态。
- `xhs-growth-cold-start-data-and-positioning.md`：小红书冷启动数据探测、爆款画像与避坑指南——商业数据平台误区（蝉小红关停/千瓜禁止API）、轻量按次 API（DataXAPI）与无头浏览器 MCP 双轨组合、登录态双重寿命与安全隔离铁律、求职者（网页简历）与创意生（作品集）两大高爆画像提纯、三大避坑流量陷阱，以及「收藏数远大于点赞数」的核心度量标准。

## 给人类看的简明文档

- `for_human/0_初始化配置.md`：新服务器初始化与各类 key 配置步骤。
- `for_human/1_整体架构.md`：当前整体架构说明。