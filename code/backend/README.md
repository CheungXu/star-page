# FastAPI 后端

后端负责页面生成主流程：手机号用户系统、数据库、LLM 流式调用、OSS 存取、HTML 清洗、SSE 事件、上传文件内容抽取和 `/p/{conversation_id}/{page_id}` 页面访问网关。

生成页现已支持展示型 CSS/JS：安全采用"隔离优先"——`/p` 网关统一下发 `Content-Security-Policy: sandbox allow-scripts ...; connect-src 'none'`，把页面关进无主站凭证、无外部网络的不透明 origin；`html_sanitizer` 放行内联脚本/事件/表单控件，移除 iframe/object/embed/base 与 meta refresh，外链 `<script src>` 仅留可信 CDN（`GENERATED_PAGE_CDN_ALLOWLIST`）。当前生成策略要求模型输出普通内联 CSS，不支持 Tailwind Play CDN；若检测到 `cdn.tailwindcss.com` 或 `type="text/tailwindcss"`，页面生成会自动要求模型改写为普通 CSS 并重试一次。原理详见 `wiki/generated-page-js-sandbox-and-security.md`。

## 本地运行

```bash
cd code/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m app.db.migrate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

真实配置从环境变量读取，也会在本地开发时尝试读取仓库 `config/*.env`。密钥文件不要提交。

## Docker 镜像

后端镜像推送到阿里云 ACR 应用仓库：

- 镜像仓库：`crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page`
- tag 约定：`backend-<git 短 sha>` 或带变更说明的版本 tag，另用 `backend-latest` 指向最新后端镜像。

```bash
cd code/backend
ACR=crpi-6w1a91eyh3y1vcd9.cn-guangzhou.personal.cr.aliyuncs.com/stars-page/stars-page
SHA=$(git rev-parse --short HEAD)
docker build -t "$ACR:backend-$SHA" -t "$ACR:backend-latest" .
docker push "$ACR:backend-$SHA"
docker push "$ACR:backend-latest"
```

`Dockerfile` 默认使用阿里云 PyPI 镜像源安装依赖，避免构建时访问默认 PyPI 过慢；如需切换源，可通过 `--build-arg PIP_INDEX_URL=... --build-arg PIP_TRUSTED_HOST=...` 覆盖。

如果迁移时报 `permission denied for schema public`，说明当前 RDS 应用账号只有连接权限、没有建表权限。请先用高权限账号参考 `script/prepare_rds_database.sql` 创建业务库并授权，再重新执行迁移。

服务器上已通过 `star-page-backend.service` 常驻运行：

```bash
systemctl restart star-page-backend.service
journalctl -u star-page-backend.service -f
```

> 备忘（重要）：常驻服务直接从仓库目录跑 uvicorn 且**未开 `--reload`**，只在启动时加载一次代码。**每次修改后端代码后，必须执行 `systemctl restart star-page-backend.service`**，否则线上仍在用旧代码（例如改了 prompt 但生成仍是旧行为）。该服务已设 `TimeoutStopSec=10s`，避免 SSE 长连接把 restart 卡到默认 90s。

## 关键接口

- `POST /api/auth/sms/send`：向手机号发送登录验证码。
- `POST /api/auth/sms/login`：验证码登录；手机号未注册时自动注册，并写入 HttpOnly Session Cookie。
- `GET /api/auth/me`：读取当前登录用户。
- `POST /api/auth/logout`：注销当前 Session。
- `POST /api/auth/password`：登录后设置或更新密码。
- `POST /api/generations`：创建页面生成任务。带 `conversation_id` + `base_page_id` 续写时，基点在最后一轮就留在本会话；基点在更早的轮次则**自动分叉出一条新会话**（见下方「会话分叉」），响应里的 `conversation_id` 是新会话，`forked_from` 给出来源会话与轮次。
- `GET /api/generations/batches/{batch_id}/events`：一轮一条 SSE 连接，带这一轮所有模型的事件（payload 里有 `task_id`），全部收尾后发 `batch_done`。断线时浏览器带 `Last-Event-ID` 续传，也可以用 `?after=` 显式指定。只读，连接断开不影响生成（见下方「后台生成」）。
- `POST /api/generations/batches/{batch_id}/cancel`：停止这一轮还没完成的模型，返回 `stopping` 个数。已完成的照常结算，被停的写成 `cancelled`、不扣积分。
- `GET /api/generations/{task_id}/events`：单个任务的只读事件流，旧前端兼容用。
- `GET /api/conversations`：查询当前登录用户的会话历史列表，支持 `favorite_only=true` 和 `q=关键词`。分叉会话带 `forked_from`（来源会话 id、标题、第几轮、来源是否还在）。
- `GET /api/conversations/{conversation_id}`：获取会话详情，供左侧历史恢复生成树和多轮回看。`batches` 含从来源会话继承来的轮次（`inherited=true`，排在前面）。每个批次带 `kind` / `base_page_id`，每个节点带用量 `usage`、扣费 `billing` 和生成用时 `duration_ms`（任务 `finished_at - started_at`，未结束为空）。
- `PATCH /api/conversations/{conversation_id}`：更新会话收藏状态。
- `DELETE /api/conversations/{conversation_id}`：软删除会话，写入 `deleted_at` 后不再出现在历史列表；其下节点同步软删，但仍被未删除分叉会话继承的那段历史保留。
- `GET /api/models`：返回多模型目录（含可用性）供前端勾选。
- `GET /api/pages`：旧版页面级历史接口，当前左侧历史已切到会话级接口。
- `GET /api/pages/{page_id}`：获取页面元数据。
- `GET /p/{conversation_id}/{page_id}`：页面访问网关，校验节点归属会话后从私有 OSS 读取 HTML 并返回（带展示型沙箱 CSP）。是否失效只看节点自己的 `deleted_at`：删会话会级联软删节点（404），被分叉会话继承的历史节点除外。返回前会补上 Open Graph 标题和描述；`?print=1` 会在加载后调起打印。
- `GET /api/scenes`、`GET /api/scenes/{key}`：场景目录与案例。生成请求可带 `scene_key`，新建会话时按 `config/scenes.json` 强制技能。
- `POST /api/publications`、`GET /u/{slug}`：把某个已完成节点发布成固定链接。重新发布只切换版本。同路径下有 `og.png`、`poster.png`、`qr.png`。返回里除链接外还带分享卡片的生效值 `title` / `subtitle` / `align` 与默认值 `default_title`（生成页 `<title>`）/ `default_subtitle`（场景标语或「由星页 StarPage 生成」）。
- `PATCH /api/publications/{slug}`（仅本人）：改写分享卡片的 `title` / `subtitle` / `align`（`left` / `center` / `right`），只改请求里带上的字段，传 `null` 或空串恢复默认；保存后重画 `poster.png` 与 `og.png`。用户改写的值存在 `page_publications.custom_title` / `custom_subtitle` / `card_align`（迁移 `020_share_card_custom.sql`），重新发布另一个版本时保留；`title` / `og_description` 存生效值。自定义了标题时，`/u/{slug}` 连页面 `<title>` 一起替换（微信等只读 `<title>` 的地方也显示用户写的标题）。
- 朋友圈海报（`app/services/share_card.py`）与前端 `share-poster.ts` 同一版式（前端 720×1080，后端 1080×1620）：标题用 Noto Sans CJK SC 粗体，副标题 / 标签随对齐方式左中右排，二维码卡放在文字下沿与底部落款正中。测试：`tests/test_share_card.py`（纯逻辑）、`tests/test_share_card_db.py`（需 `TEST_DATABASE_URL` 与 `OBJECT_STORAGE_PROVIDER=local`）。
- `GET /assets/{id}`：用户上传的头像、二维码等图片，供生成页引用。
- `GET /api/pages/{page_id}/stats`：作者查看该会话的外部访问次数。
- `GET /api/admin/scenes/funnel`、`POST /api/admin/scenes/showcases`：分场景漏斗，以及把公开页标成案例。

### 会话分叉

库表见迁移 `019_conversation_fork.sql`，逻辑集中在 `app/services/conversation_tree.py`。

- **一条会话 = 生成树上的一条线**。从更早一轮的结果继续时，`create_batch` 新建 `origin='fork'` 的会话，用 `forked_from_conversation_id` / `forked_from_batch_id` / `fork_base_page_id` 接回来源；分叉点及之前的轮次继承、不复制，之后的轮次归新会话。指针总是指向**真正拥有那一轮的会话**（在分叉会话里选继承来的轮次再分叉，指针越过中间会话直接指回去）。
- **会话视图**：`view_batches` 沿分叉指针往上拼出完整轮次。续写的基点必须在这个视图里；判断是否分叉看基点批次是不是视图的最后一轮。
- **发给模型的上下文**不受影响：仍沿 `parent_page_id` 回溯，节点父指针可以跨会话。
- **并行续写**（不选基点，`_parallel_parents`）：都从当前会话视图的最近一轮接着改，不会串到别的分支，也不会跳回更早的轮次。参与了最近一轮且已完成的模型接自己那一版；新加入、上一轮没选或上一轮失败的模型，接最近一轮第一个已完成的结果（按该轮模型顺序）。所以每轮可以自由增减、更换模型。
- **删除**：`release_deleted_pages` 只软删不再被任何未删除分支继承的节点；同时把分叉链上游同样已删的会话重新结算一次，最后一个继承者被删后那段历史才删掉。
- **测试**：`tests/test_conversation_tree.py` 是纯逻辑单测；`tests/test_conversation_fork_db.py` 需要一个跑过迁移的空库，设 `TEST_DATABASE_URL` 后运行，未设置时跳过。可以用临时容器：`docker run -d --rm --name sp-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=stars_page_test -p 127.0.0.1:55432:5432 postgres:16-alpine`，再用 `DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test python -m app.db.migrate` 建表。**不要指向线上库**。

### 计费接口（积分制，1 元 = 100 积分）

用户侧（`routes_billing.py`）：

- `GET /api/billing/account`：返回积分余额（登录）或剩余免费次数（匿名/未分配 cookie 的访客返回合成的满额免费态）。
- `GET /api/billing/transactions`：当前用户积分流水（登录或带匿名 cookie 的访客）。
- `GET /api/billing/packages`：充值套餐（DB `credit_packages`，启动 seed mock）。
- `POST /api/billing/recharge`：建单，**只收 `package_key`（+可选 `provider`）**，金额/积分服务端按套餐计算（价格服务端权威）。需登录。`provider=wechat` 走微信 Native 下单并返回二维码链接 `code_url`；`provider=mock` 仅非生产；不传时微信已配置则默认微信、否则回退 mock。
- `POST /api/billing/recharge/{id}/mock-pay`：mock 支付回调入账，**仅非生产环境（`APP_ENV != production`）开放**，原子流转 `pending→paid` + 幂等入账。
- `POST /api/billing/wechat/notify`：微信支付结果回调（无需登录）。先验签再解密，校验订单归属 + 金额一致 + `trade_state=SUCCESS` 才入账；非成功事件/异常订单回 200 避免重试风暴。
- `GET /api/billing/recharge/{id}`：查询充值订单状态（需登录、校验归属）。微信单仍 `pending` 时主动查微信对账（回调丢失也能到账），前端据此轮询。

微信支付实现见 `app/services/payment/wechat/`（**微信支付公钥模式**，非平台证书模式）：`config.py` 读凭据与私钥/公钥文件（缺失不报错、由 `configured` 判断启用），`client.py` 懒加载单例封装 Native 下单 / 查单 / 回调验签解密 / 资金账单解析，同步 SDK（`wechatpayv3`）调用统一经 `asyncio.to_thread`。`out_trade_no = 订单 UUID 的 hex`，回调可逆向还原。凭据放服务器 `config/wechatpay.env` 与 `config/certs/`（均 gitignore），模板见 `config/wechatpay.env.example`。

管理员侧（`routes_admin.py` + `require_admin`，管理员身份以数据库 `admin_phones` 表为准，按手机号白名单，可预授权未注册手机号；用 `script/set_admin.py` 维护）：

- `GET /api/admin/billing/overview`：财务总览，三段式呈现——①付费业务（不含赠送）：累计充值现金（取已支付充值订单金额之和，provider 无关）/付费确认收入/付费 COGS/付费毛利；②赠送台账：赠送已发放/未用负债/已核销收入/赠送+试用成本；③含赠送合计：综合收入/成本/毛利；期间费用含基础设施成本与**支付手续费（6603）**，营业利润 = 综合毛利 − 基础设施 − 手续费；并含预收账款、**应收第三方支付（1002 净额，微信已收款未结算）**、预付云资源余额与累计充值、科目余额。收入与成本按消费流水的 paid/gift 占比拆分。
- `POST /api/admin/billing/wechat-settlement`：手动记一笔微信结算到账（借 `1001 现金` + `6603 支付手续费` / 贷 `1002 应收第三方支付`），作为自动对账的兜底，每次独立事件号。
- `GET /api/admin/billing/wechat-fundflow?date=YYYY-MM-DD` 与 `POST /api/admin/billing/wechat-fundflow/post`：拉取微信「申请资金账单」(`fundflowbill`，BASIC 账户) 并按业务类型归类结算/手续费/收入，POST 按 `(wechat_settlement, 账单日)` 幂等入账（借 1001 + 6603 / 贷 1002）。当日账单一般次日才可下载；业务类型归类关键词需以首笔真实账单校验。
- `GET /api/admin/billing/transactions`、`/ledger`、`/users`：积分流水、记账凭证、用户对账。
- `GET / PUT /api/admin/billing/model-markups`：读取/保存模型倍率（展示各模型成本基准），PUT 写回 `config/billing.json` 并清缓存即时生效。
- `POST /api/admin/billing/supplier-topup`：记录云/LLM 供应商账户预充值（借 `1102 预付账款-云/LLM供应商`、贷 `1001 现金`）。供应商为预付费模型，每次调用按实际成本贷记 `1102` 冲减预付资产（不再使用应付账款）。
- `GET /api/admin/billing/supplier-balances?refresh=false`：直连各云厂商财务接口拉取**真实账户余额**做对账，默认 5 分钟缓存，`refresh=true` 强制刷新。实现见 `app/services/supplier_balance/`（provider 可插拔）：
  - 阿里云：BSS OpenAPI `QueryAccountBalance`，复用 OSS 的 `OBJECT_STORAGE_ACCESS_KEY_ID/SECRET`；**需给该 RAM 用户授予 `AliyunBSSReadOnlyAccess`**，否则返回 NotAuthorized 提示。
  - 火山引擎（豆包）：需在 `VOLC_ACCESSKEY/VOLC_SECRETKEY` 配置火山账号 IAM AK/SK（ARK 推理 key 不能查余额）；未配置时后台显示「未配置」。其余厂商按需新增 provider 即可。
  - 阿里云费用 key 走独立 `ALIYUN_BILLING_ACCESS_KEY_ID/SECRET`（`config/aliyun.env`），与 OSS/短信分开；火山凭据放 `config/huoshan.env`；两文件均由 systemd 以可选 `EnvironmentFile=-` 加载。
- `GET /api/admin/billing/aliyun-bill?cycle=YYYY-MM` 与 `POST /api/admin/billing/aliyun-bill/post`：按账期拉取阿里云账单总览（BSS `QueryBillOverview`，成本口径取 `pretax_amount`＝折扣后应付，预付费模式下现金已在充值时支付、账单期 `payment_amount`≈0，故用应付而非现金口径），按 `aliyun_llm_keywords`（`config/billing.json`）拆分「百炼 LLM（已按次计入 `6001`，不重复入账）」与「服务器等基础设施」；POST 把基础设施部分按 `(infra_cost, aliyun-账期)` 幂等入账（借 `6002 基础设施成本` / 贷 `1102 预付账款`）。财务总览据此给出「营业利润 = 综合毛利 − 基础设施成本」。
- 该接口还返回**百炼成本偏差对账**：把该账期内走 `dashscope.aliyuncs.com`（百炼平台）的模型按次估算 COGS（`raw_cost_cny` 之和）与账单百炼实际金额对比，给出偏差额与偏差率；并提供付款拆解（原价/应付/代金券补贴/储值卡抵扣/现金支付）。注意 qwen 及在百炼上托管的 deepseek/glm/kimi/minimax 均计入阿里云百炼，doubao 走火山 ARK 不计入。

管理员维护：管理员手机号存于数据库 `admin_phones` 表（迁移 `014_admin_flag.sql` 创建并引导初始管理员）。增删管理员用 `code/backend/.venv/bin/python script/set_admin.py {list|grant <手机号> [--note 备注]|revoke <手机号>}`，可预授权尚未注册的手机号；变更即时生效，无需重启。

计费实现位于 `app/services/billing/`：`pricing.py`（`扣费积分 = max(ceil(原始成本×倍率×100), 1)`）、`account.py`（钱包：赠送/充值入账、生成结算，扣减顺序 gift→paid，全部按 `idempotency_key` 幂等）、`ledger.py`（复式过账，借贷必平且按 `(event_type, event_ref)` 幂等）。倍率与匿名围栏参数读 `config/billing.json`。

### 运营埋点与运营后台接口

运营后台数据内核见 `app/analytics/README.md`（明细+聚合两层架构、聚合 CLI、埋点字典、指标口径）。方案见 `doc/20260628/ops-backend-design-and-implementation.md`，方法论见 `wiki/product-ops-metrics-and-north-star.md`。

- `POST /api/analytics/collect`：公开埋点采集（允许匿名），event 走白名单（`landing_view`/`prompt_input`/`generate_click`/`login_success`）+ IP 内存限频，写 `analytics_events`（IP 仅存 HMAC）。`/p` 网关成功返回时还会 fire-and-forget 写 `page_view_events`（区分作者自览）。
- 管理端（`routes_admin_analytics.py` + `require_admin`，前缀 `/api/admin/analytics`，对应前端 `/ops` 九个 Tab）：`GET /realtime`、`/overview`、`/trends`、`/retention`、`/funnel`、`/engagement`、`/quality`、`/virality`、`/users`、`/users/{id}`、`/cases`、`/cases/{id}`。
- 离线聚合：`python -m app.analytics.aggregate`（按自然日幂等 upsert `metric_daily`/`retention_cohort`/`funnel_daily`，可回填重跑），由 `code/systemd/star-page-analytics.{service,timer}` 定时驱动。

### 训练授权、授权优惠与举报

方案与决策见 `doc/20261015/training-consent-report-discount-plan.md`，库表见迁移 `018_training_consent_and_reports.sql`。

- **授权快照**：`users.allow_training`（默认开）是当前开关；`generation_batches` / `generation_tasks` / `page_versions` / `page_assets` / `analytics_events` 的 `training_consent` 记录**数据产生那一刻**的状态：`granted` / `denied` / `legacy`（协议上线前的数据，或尚未看到更新告知的老用户）。判定在 `consent_service.training_consent_for`。**训练取数口径**：`training_consent='granted'` 且所属用户当前 `allow_training=true`。
- **同意记录**：`user_consents` 只追加，事件 `login_agree`（登录勾选，带 `agreement_version`）/ `notice_ack`（老用户点「知道了」）/ `anon_notice`（匿名首访）/ `preference`（开关变更）/ `merge_opt_out`（匿名关过、登录归并时带到正式账号）。协议版本常量 `POLICY_VERSION` 与前端 `LEGAL_POLICY_VERSION` 必须同步。
- **授权优惠**：倍率读 `config/billing.json.training_discount_rate`（限 0.5–1.0，当前 0.9）。按批次快照判断，`granted` 时 `apply_training_discount` 向下取整（至少 1 积分），优惠额记 `credit_transactions.training_discount_credits`；事后关开关不补扣。前台文案统一写「积分优惠」，不写死折数，方便直接改配置调价。
- **匿名结构样本**：`granted` 的生成结算后，`training_samples.store_training_sample` 抽取去掉文字/链接/联系方式的 HTML 骨架入 `training_samples`（不含 user/page id，按 `content_hash` 去重，`EXTRACTOR_VERSION` 区分抽取规则），关开关不撤回。
- **成本只给管理员看**：生成进度、完成事件、会话详情和用户积分流水接口只向用户返回扣掉的积分和优惠（`credits_charged` / `discount_credits`），不再返回 `cost`、`raw_cost_cny`、`revenue_cny`；回放旧事件时也会去掉 `cost`。实际金额仍记在 `page_versions` 和 `credit_transactions` 里，供 `/admin` 与 `/ops` 读取。
- 接口：`GET / PUT /api/privacy/preferences`（PUT 会给未登录访客懒建匿名身份）、`POST /api/privacy/notice-ack`。`/api/auth/me` 返回 `allow_training`、`needs_policy_notice`。
- **举报**：公开页浮标「举报」跳平台 `/report?page=&slug=`（生成页 CSP 禁止直接请求接口）。`GET /api/reports/target`、`POST /api/reports`（同 IP 同页 24h 一次、每 IP 每日 20 次）；管理端 `GET /api/admin/reports?status=pending|taken_down|rejected|all`、`POST /api/admin/reports/{id}/review`（`takedown` / `reject` / `restore`）。下架即 `pages.moderation_status='blocked'`，`/p` 与 `/u` 返回 451「该页面因违规已下架」，不可再发布，案例墙同步过滤。

匿名体系：未登录访客经 `resolve_actor` 懒签发 HMAC 签名 `sp_anon` cookie 并建 `is_anonymous` 用户，免费 2 次生成；高价模型后端强校验拒绝（匿名只允许 `anon_allowed_models`、单次 ≤ `anon_max_models_per_gen`）；按 IP 每日限制签发匿名 id 数与免费次数。手机验证码登录时把匿名会话/页面归并到正式账号并首次赠送 1000 积分（幂等）。

`POST /api/generations` 同时兼容 JSON 和 `multipart/form-data`。上传文件时表单字段为：

- `prompt`：用户页面需求。
- `models`：勾选的并行模型 key（可重复字段或单个 JSON 数组字符串）。
- `files`：当前最多允许 3 个文件，单文件和单次总大小均不超过 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`pdf`、`txt`、`md`、`html`。后端会抽取为 Markdown/文本并合并到 LLM 上下文；PDF 仅保证可复制文本内容的抽取，扫描版图片 PDF 或加密 PDF 可能解析失败。

如果抽取文本超过 5000 字符，`POST /api/generations` 只保存原文并立即返回任务。压缩发生在后台生成流程里：同一批次只调用一次，优先用 `qwen-plus`（通义 Flash）且关闭思考，多段同时压缩，整次总超时 120 秒。压缩结果写回该批次的全部分支后再进入页面生成；原来 prompt 里的图片地址会保留。短文本仍在创建时直接拼进 prompt。匿名用户若压缩失败，这次免费次数会退回。

Nginx 入口需要同步放开上传体积限制，当前示例配置为 `client_max_body_size 60m`，用于覆盖 50MB 单次上传和 multipart 开销。

为方便后续排查，`generation_tasks` 会记录：

- 用户原始 prompt。
- 上传文件名列表。
- 文件抽取文本。
- 触发压缩时使用的压缩 prompt 说明。
- 最终输入页面生成模型的 prompt。
- 模型原始输出文本，便于排查 HTML 提取或清洗导致的内容丢失。
- 生成 HTML 上传后的 OSS 调试定位信息。

LLM 调用默认带重试机制，配置项为 `LLM_RETRY_ATTEMPTS`、`LLM_RETRY_INITIAL_DELAY_MS`、`LLM_RETRY_MAX_DELAY_MS`。底层客户端会重试可恢复的网络、超时、限流和 5xx 错误；资料压缩会对“调用成功但正文为空”的情况重试；页面生成会在正式 HTML 输出开始前失败或空输出时重试。页面生成后、上传前还会检测 Tailwind 运行时依赖，首次命中时追加修正提示并自动重试一次。

## 后台生成

模型调用由进程内的后台执行器跑完，和浏览器的事件流连接无关。用户新建对话、切到别的历史、刷新或关掉页面，生成都会继续，回来时从数据库回放进度再跟进实时事件。

- **执行器**（`app/services/generation_runner.py`）：`POST /api/generations` 提交批次后立即 `start`，每个任务一个 `asyncio.Task`、各自一个数据库会话。`GENERATION_MAX_CONCURRENCY`（默认 48）限制同时调用模型的任务数，超出的保持 `pending` 按先来后到排队。上限是全局的：一次大批量生成占满名额时，后来的用户要等前面有任务完成，所以上限要明显高于单个用户的突发量（9 模型连发 4 轮 = 36）。认领用原子更新 `pending → running`，同一任务不会跑两次。生成器没把任务写到终态（被硬取消、数据库异常）时，`finally` 用新会话补写。
- **停止**：先协作式中止，在两个模型 chunk 之间检查，写成 `cancelled`；3 秒内模型没吐下一个 chunk 就直接取消协程，再由兜底写库。模型已经完整输出后不再响应停止，页面照常发布。
- **终态与收尾事件同事务**：`completed` / `failed` / `cancelled` 事件和任务终态在同一个事务里提交。事件流据此判断「任务结束」，不会出现状态已结束却漏掉 `completed` 的情况。
- **事件流**（`app/services/generation_events.py`）：只读数据库，每个任务各有一个游标。原因是 `sequence` 全局递增，多个任务并发写入时提交顺序和 `sequence` 顺序不一致，用一个全局游标会永久漏掉晚提交的小号事件。续传 id 是按任务 id 排序后各任务游标用点连接的字符串。回放时把同一任务连续的 `reasoning_delta` 合成一条；执行器每写一条事件就唤醒订阅者，唤醒后等 0.2 秒合批再读库；空闲时每 15 秒发一行 `: ping`，避开 nginx 420 秒读超时。响应头带 `Cache-Control: no-cache, no-transform`，防止中间层压缩把实时事件攒住。
- **启动扫尾**（lifespan，受 `GENERATION_STARTUP_SWEEP` 控制，默认开）：处理上一个进程留下的 `pending` / `running` 任务。最近活动时间（最后一条事件、`started_at`、`created_at` 取最晚）在 `GENERATION_RESUME_WINDOW_MINUTES`（默认 120）内的：没开始过的直接排队；中途被打断且 `retry_count < 1` 的清掉旧事件、写一条 `restarted`、从头重跑一次；其余标 `failed`（「服务重启中断」，不扣积分）。进程退出时不改任务状态，全部留给下次启动处理。
- **单 worker 约束**：任务登记表在内存里，后端必须只跑一个 uvicorn worker。以后要多 worker 或多机，需要给任务加 owner 与心跳字段，再改扫尾逻辑。
- **预览实例注意**：在 worktree 里起后端预览时，如果连的是线上库，必须设 `GENERATION_STARTUP_SWEEP=false`，否则一启动就会去重跑或标记线上的任务。最好连独立测试库。
- **补救卡住的任务**：`script/requeue_stuck_generations.py --batch <id>` 给指定批次没完成的任务刷新最近活动时间，随后在窗口内重启后端，由扫尾重跑（见 `script/README.md`）。
- **重启耗时**：有 SSE 连接时 uvicorn 会等连接关闭，systemd 到 `TimeoutStopSec=10s` 才强杀。强杀不影响正确性，被打断的任务由下次启动扫尾处理。
- **测试**：`tests/test_generation_runner.py` 是纯逻辑单测（扫尾分流、事件合并、续传游标、唤醒器）；`tests/test_generation_runner_db.py` 用假模型客户端跑真实数据库流程（认领只成功一次、后台跑完并续传、实时跟进、协作式停止、硬取消、排队中停止、HTTP 批次流与停止接口、启动扫尾），需要 `TEST_DATABASE_URL`，建库方式同上方「会话分叉」的测试说明。该测试会改测试库里所有未结束任务，**不要指向线上库**。

## 生成过程事件

批次事件流和单任务事件流使用同一组 SSE 事件，批次流的每条 payload 带 `task_id`：

- `status`：普通状态文案。
- `reasoning_delta`：模型流式返回的 `reasoning_content`（回放时同一任务连续的几段会合成一条）。
- `answer_started`：模型进入正式 HTML 输出阶段，前端开始展示“页面创建中”。
- `progress`：创建节点状态，目前包含 `upload_file`、`parse_file`、`compress_document`、`model_thinking`、`model_output`、`deploy`。`model_output` 完成时携带真实 `input_tokens`/`output_tokens`。
- `completed`：页面已上传 OSS、数据库已更新，返回可访问链接；携带 `usage` 与 `billing`（扣掉的积分和授权优惠）。
- `failed`：生成失败。
- `cancelled`：用户停止了这个模型。
- `restarted`：后端重启后这个任务被自动从头重跑，前端应清空它之前的思考和进度。
- `batch_done`：仅批次流，这一轮所有模型都已收尾。

`model_thinking` 节点承载模型 reasoning 内容，前端默认展开且支持收起。`model_output` 生成中优先展示估算 output tokens；完成后展示 API 返回的真实输入/输出 token，并在进度区下方显示消耗的积分与优惠。实际金额成本（按 `llm.models.json` 的 `pricing` 自算）只写入 `page_versions` 与 `credit_transactions` 供后台查看，不再下发给用户。

## 网页制作技能（page-skills）

后端在生成时可自动应用 `skills/page-skills/` 下的网页制作技能（落地页、简历、数据报告等），提升对应场景质量。机制（`app/services/skills/`）：

- `registry.py`：扫描技能目录，解析每个 `SKILL.md` 的 frontmatter(YAML) + 正文，构建进程级缓存的技能目录（改技能文件需重启后端）。
- `selector.py`：`SkillSelector` 接口 + `LlmClassifierSelector`。用户未手动选技能时做一次轻量、非流式的 LLM 分类路由（把技能 name/description 清单交给模型返回 key/NONE）；超时或失败回退到 `triggers` 关键词匹配，再回退到不注入。
- 注入：选中技能的正文经 `build_skill_system_message` 追加为一条 system 消息，叠加在通用提示之上；首轮与续写均注入。
- 路由发生在各 task 执行时（task 级）：用**当前生成 HTML 的 model_key** 做轻量 LLM 分类，不同模型可匹配不同技能；续写沿本节点 parent 链路延用技能。
- 技能路由的 token 用量与页面生成合并计入 `page_versions` 成本。
- 配置：`PAGE_SKILLS_ENABLED`（默认开）、`PAGE_SKILLS_DIR`（默认 `skills/page-skills`）。

> 部署注意：技能目录默认在仓库根的 `skills/page-skills/`，不在后端 Docker 构建上下文（`code/backend`）内。容器化部署时需把技能目录一并提供给后端（COPY 进镜像或挂载卷），并用 `PAGE_SKILLS_DIR` 指向容器内路径，否则技能列表为空、退化为通用生成。技能编写规范见 `skills/page-skills/README.md`。

## 当前默认配置

- 用户系统：手机号验证码登录/自动注册，Session Cookie 名默认 `sp_session`。
- 短信 Provider：`SMS_PROVIDER`，本地可用 `mock`，生产使用 `aliyun`。
- 页面默认权限：`public`，但 OSS Bucket 仍保持私有，访问统一走 `/p/{conversation_id}/{page_id}` 网关。
- 生成页可信 CDN 白名单：`GENERATED_PAGE_CDN_ALLOWLIST`，默认 `https://cdn.jsdelivr.net https://unpkg.com`。
- 业务数据库：`stars_page`。
- 当前 Qwen 配置：`LLM_PROVIDER=qwen`、`LLM_PROTOCOL=openai`、`LLM_MODEL=qwen3.8-max`。
- 多模型目录见 `config/llm.models.json`，当前 9 个可用 key：`qwen`、`qwen-plus`、`doubao`、`doubao-code`、`deepseek-v4.1-flash`、`deepseek-v4-pro`、`glm-5.3`、`glm-5.3-flash`、`kimi-k3.1-pro`（MiniMax 暂缓）。变更目录后需 `systemctl restart star-page-backend.service`；可用 `script/probe-llm-models.py` 探测连通性。
