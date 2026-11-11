# doc

日常工作文档，包含分析报告、工作记录等。

按日期组织：`doc/YYYYMMDD/`。

## 当前记录

- `20261112/generation-events-write-coalesce.md`：生成过程不再逐段写库（2026-11-12 已上线）。思考凑满 5 秒或 8,000 字再写一行，输出进度至少隔 2 秒。历史思考和重复的输出进度已合并，事件从 77.5 万行收到 2,801 行，每个任务中位约 12 行。
- `20261112/billing-unit-economics-and-preauth-plan.md`：计费单位经济重构与防透支预授权改造方案及上线记录（已上线）。基于现网账本审计落地「双轨分层稳健增长型」（轻量 2.0x / 主力 1.65x / 顶配 1.8x、92 折 + 1.25x 成本地板、¥19.9 首充包与 6%~15% 阶梯返赠、匿名 1 轮 + 注册 450 积分 + 1 次旗舰双模首秀券），配套提交即冻结积分（排队机制不变）、超长页 ≤100 积分欠费兜底、首充防重、管理端「可用+冻结」口径及三处算力漏损修补。首秀用完且未充值时默认两个轻量模型；积分被在途任务全部冻住时直接去充值页。
- `20261112/document-parsing-multimodal-upgrade.md`：上传资料解析与图片理解升级（2026-11-12 已上线）。坏文件提交时即提示、上限更严；后台解析一次全批共用；各格式专门解析，文档里的图保存下来供网页使用，扫描页只给模型看；每一轮都由所选模型结合全部资料和历次要求重新提取，无关时不带资料；能看图的模型直接看到图片。PDF 未用 AGPL 的 PyMuPDF。
- `20261111/per-user-generation-limit.md`：每个用户同时最多生成 10 个模型（可按用户单独设置，会员放宽时改一处），超出的按提交顺序排队，界面显示「排队中」并写明原因；排队时间不计入用时，排队中可直接停止。全站总上限 48 保留。
- `20261111/generation-background-runner.md`：生成任务不再随浏览器断开而中断。11-11 凌晨四轮 9 模型生成卡住 28 路，原因是生成绑在页面连接上、取消后状态没记下来、浏览器同站连接数有限。改为服务器后台跑完、每轮一条可续传连接、加「停止生成」、重启后自动重跑，网站开启 HTTP/2。11-11 13:50 上线，卡住的 28 路已自动重跑，更早遗留的 32 个标失败；同时生成上限由 24 提到 48。
- `20261111/preview-toolbar-and-share-variants.md`：结果预览工具栏遮挡页面的方案对比与上线记录。11-11 上线 C1 胶片联动（缩略图上「继续改」、用量与按钮按列对齐），分享浮层可改标题 / 副标题 / 对齐并接入后端（链接卡片与公开页海报同步）；A 浏览器顶栏、C2 聚光胶片留作 AB 备选，见 `alternatives/preview-toolbar/`。
- `20261111/promo-packs-for-promoter.md`：五套小红书图文已分别打成压缩包，给推广人转发。视频另发。压缩包不进版本库。
- `20261110/llm-model-catalog-update.md`：模型目录换到 11 月版本并已在现网生效。DeepSeek Flash 换成 1017 快照，Kimi 换成 K3.1 Pro，新增 GLM-5.3 Flash（百炼未开通，改走方舟）。通义、豆包、DeepSeek V4 Pro、GLM-5.3 未换。
- `20261110/hero-composer-height-preview.md`：首页输入区改为书写为主、上传为副，空态只高出约 8px。模型增到 9 个后，选择浮层加宽，打开时把下方场景芯片推到浮层下面。已构建并重启前端。
- `20261110/footer-consent-copy.md`：首页页脚「使用即同意」只保留协议链接，去掉训练授权那句。数据设置仍在侧边栏。
- `20261110/keep-draft-when-picking-base.md`：选择「以此结果继续」时保留输入框里已经写好的修改说明。原先一点选或更换基础，文字会被清掉；11-10 已上线，只改了页面。
- `20261110/conversation-fork-as-new-session.md`：会话分叉自动成为新会话。从中间某轮继续时，分出一条新会话，前面的历史一致、新结果接在后面，原会话不变；侧边栏标"分叉自哪个会话第几轮"。顺带修复新加入的模型从零生成的问题；同日追加会话内每轮可自由换模型（11-10 已上线）。
- `20261109/generation-progress-compare-variants.md`：生成进度与多模型结果的三套前端方案（轻量看板、沉浸画廊、赛道对比）选型记录。已选方案 B「沉浸画廊」并于 11-09 上线：对话栏是需求气泡加进度回复气泡，支持多轮回看（打开历史会话会还原所有轮次）；后端会话详情补充了每个结果的生成用时。
- `20261109/growth-materials-pushed.md`：10 月中旬的增长投放、协议合规和宣传素材补提交到远程 `main`（`856a554`）。示例配置里的 DataXAPI Key 已清空，建议作废换新。
- `20261016/koc-channel-and-batch-strategy.md`：KOC 派单渠道调研与首批投放策略。纠正「红兔」为红通告；盘点螃蟹/红通告/通告星球等与蒲公英、数据商、野生兼职；确认首批用方案 2（5 个垂直中号一人一套）测款，再裂变放量；含防查重纪律与五人画像。
- `20261015/training-consent-report-discount-plan.md`：训练授权、举报和授权优惠的方案与上线记录（已上线）。每条数据记录生成时的授权状态，筛选时还要看用户的当前状态；老用户只告知、旧数据单独标记；匿名用户默认授权但可关闭；举报存入数据库并在后台审核；授权用户享有积分优惠，生成结果对用户只显示积分与优惠，实际成本仅后台可见。
- `20261013/agreement-review.md`：协议初版审阅意见。初版不能直接上线：有几处写了系统没做到的事（手机号加密、内容机审、私密开关、自助注销）；对外提供数据把去标识化和匿名化混用；缺运营主体、第三方共享清单、未成年人条款、举报入口和 AI 生成标识；充值协议的积分清零和全额不退风险高。附落地顺序。
- `20261013/user-agreement-and-privacy-policy-v2.md`：协议第二版（当前版本）。运营主体为深圳星泽创旗科技有限责任公司（按营业执照）；训练开关默认开启，付费积分长期有效，先扣赠送积分再扣付费积分；生效日期为 2026年10月15日。
- `20261013/user-agreement-and-privacy-policy.md`：星页法律合规协议初版（包含《用户服务协议》《隐私政策》《积分充值服务协议》）。针对生成式 AI 监管红线、知识产权界定、去标识化/脱敏代码及行为数据用于模型训练与商业化许可、非排他商业衍生授权、严禁钓鱼及电信诈骗、数字化虚拟商品不支持七天无理由退款等进行了条款设计。
- `20261013/koc-distribution-plan-and-sop.md`：星页通告平台 KOC 派单实施规划与执行 SOP（已按 2026-10-16 修订）。含方案 2 测款节奏、多平台渠道、防查重交付、物料包全景、合规/UTM 前置、招募模板、30 秒筛选、评论区防限流与结算。
- `20261012/promo-screenshots-and-explainer-video.md`：小红书配图真实截图与一分钟讲解视频。封面可用，但手机屏幕是假界面，需在封面后跟真实截图。已从线上截图并拼成 3:4 配图，实测生成要几分钟；用真实截图加中文配音做了竖屏视频，按 2 倍速压到 29 秒。
- `20261012/document-compression-timeout-fix.md`：上传长文档生成超时。创建任务不再等待压缩；压缩改到生成过程中，同一批只压一次，并改用更快的模型。
- `20261012/xhs-hot-keywords-fetch.md`：小红书公开热词第一版。免费榜单 20 条已落盘，内容是娱乐热点，简历选题需 DataXAPI Key 后再搜。
- `20261012/weekly-work-plan-growth-compliance-ai-image-agent.md`：星页 StarPage 本周工作计划。合规模块已于 2026-10-15 完成；增长分发已确认方案 2 测款策略，待发单执行；另含 AI 生图与 Agent 沙盒预研。
- `20261012/cursor-remote-gemini-unavailable.md`：连上星页开发机后 Cursor 里没有 Gemini 的原因与处理。远程窗口的 AI 请求改从广州这台机器发出，已改回从开发者自己的电脑发出；需重新加载窗口后生效。不影响网站本身使用的模型。
- `20261010/scene-positioning-implementation.md`：场景定位落地记录。四个场景（网页简历、博主主页、产品落地页、活动邀请函）共用场景配置、强制技能、固定发布链接、分场景漏斗，历经三轮打磨：首屏“别人做的页面”所见即所得真实案例（带微光加载）、点击直达全屏预览、右上角做同款、输入框 Prompt 预填 + 场景化素材拆分、生成页 Shadow DOM 浮标与微信长图海报弹窗、Kimi K3 temperature 参数修复。
- `20261010/china-market-growth-strategy.md`：星页 StarPage 中国大陆地区产品增长策略规划（从通用生成到垂直爆款场景破局、产品内生裂变 PLG、本土小红书/科技圈获客矩阵、商业化权益设计与合规护城河）。
- `20261010/llm-model-catalog-update.md`：模型目录更新与探活。通义 Max 升到 3.8、Plus 槽改为 3.8 Flash、豆包 Pro 升到 Seed 2.1、DeepSeek Flash 升到 V4.1、GLM 升到 5.3、Kimi 升到 K3。
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
- `20260628/ops-backend-design-and-implementation.md`：星页运营后台设计与实施记录（面向管理）——背景与目标、北极星 WAGP（成功生成且被外部访问的页面数）、11 个指标域、明细+聚合两层数据架构、九 Tab 前端交付范围、上线验证与后续迭代。
- `20260629/ops-backend-launch-record.md`：运营后台 systemd 上线记录——迁移、前后端 build+restart、analytics 定时器首次安装、冒烟验证与使用说明。

## 使用约定

- `doc/` 记录本项目阶段性工作过程、实施状态和问题处理记录。
- 可跨项目复用的通用经验沉淀到 `wiki/`，例如 systemd 常驻部署、生成页预览方案、LLM Provider 抽象原则、设计 token 与输入卡片设计模式、PNG logo 透明化处理。

