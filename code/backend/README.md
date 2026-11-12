# FastAPI 后端

后端负责页面生成主流程：手机号用户系统、数据库、LLM 流式调用、OSS 存取、HTML 清洗、SSE 事件、上传资料解析与信息提取和 `/p/{conversation_id}/{page_id}` 页面访问网关。

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

`python -m app.db.migrate` 每次都按文件名重跑全部 SQL，语句必须能重复执行。要删掉某张表上的索引时，除了新增 `DROP INDEX IF EXISTS`，还得删掉最初那份迁移里的 `CREATE INDEX`，否则下次迁移会先把索引建回来。`generation_events` 的 `(task_id, sequence)` 已有唯一约束，不要再加一条同样列的普通索引。

线上后端按角色拆成两类常驻进程，每类都可以开多个（`APP_ROLE`，不设时为 `all`，即单进程既接请求又跑生成，本地开发、测试和预览都用它）：

| 服务 | 角色 | 做什么 |
| --- | --- | --- |
| `star-page-web@8000`、`@8001`、`@8002` | `web` | 接 HTTP 请求、推事件流、返回生成页与分享页。不跑生成、不做启动扫尾，可以逐个重启 |
| `star-page-worker@1`、`@2` | `worker` | `python -m app.worker`，认领并跑生成、给手上的任务续期、接手失联 worker 的任务。不对外提供 HTTP。重启时先排空，生成不中断 |

```bash
script/restart_web.sh       # 逐个重启所有在跑的 web，不中断对外服务
script/restart_worker.sh    # 逐个重启所有 worker：每个先跑完手上的再退出，其他 worker 照常领新任务
journalctl -u 'star-page-worker@*' -u 'star-page-web@*' -f
journalctl -u 'star-page-*' --since -10min -o cat | grep '^\[metrics\]'   # 每个进程每分钟一行运行指标
```

每个进程每 `METRICS_INTERVAL_SECONDS`（默认 60，0 关闭）打印一行 `[metrics]`：事件循环延迟（平均 / 最大毫秒）、默认线程池的线程数和排队数、连接池借出数和突发数、内存（`rss_mb`）、文件描述符数；web 另有事件流订阅数和分享页缓存（条数、MB、命中、未命中），worker 另有 `worker_id`、在跑任务数、是否在排空、在做的批次解析数，以及解析子进程指标（`parse_waiting`、`parse_procs`、`parse_last_rss_mb`、`parse_last_ms`、`parse_timeouts`、`parse_ooms`）。代码在 `app/core/metrics.py`。

> 备忘（重要）：常驻服务直接从仓库目录运行且**未开 `--reload`**，只在启动时加载一次代码，改完后端代码必须重启。按改动范围选：
> - 只改接口、分享页、计费展示、登录等请求路径上的代码：`script/restart_web.sh`。
> - 改了生成相关代码（prompt、模型调用、解析、结算、`generation_service` / `generation_runner`）：还要 `script/restart_worker.sh`。在跑的生成不会被打断，但一个 worker 手上有长任务时要等它跑完（最多 6 分钟）。
> - 两边都改了，或改了模型目录、`config/*.env`：两者都重启。
> 计费倍率（`config/billing.json`）不用重启：各进程每 5 秒按文件修改时间检查一次。
>
> 滚动重启一定要等前一个实例恢复、再等过 Nginx 的 `fail_timeout`（5 秒）才动下一个，否则 Nginx 会把两个实例同时算作不可用。`script/restart_web.sh` 已经按这个顺序做。

同步代码不能在事件循环里跑，按轻重分层隔离：

- 默认线程池在启动时放大到 `THREAD_POOL_SIZE`（默认 32，`app/core/executors.py`），`asyncio.to_thread`（OSS 读写、流式预检、微信支付、分享图等）都用它。
- 文档完整解析不跑在 worker 进程线程里，而是由 `app/services/document_parser/child.py` 按单份文件拉起**受限子进程**（`nice(10)` + `RLIMIT_AS`，默认 `DOCUMENT_PARSE_MAX_MEMORY_MB=1536`），由信号量限制单 worker 并发 `DOCUMENT_PARSE_CONCURRENCY`（默认 1，兼容旧环境变量名 `DOCUMENT_PARSE_THREADS`）。原件从 OSS 直接流式落盘到临时文件再交给子进程，超时或取消时 `SIGKILL` 进程组并用 `os.wait4` 回收及采集 `ru_maxrss`，既不抢父进程 GIL 也不留内存碎片。
- OSS 客户端按配置在进程内复用（`storage/factory.py`），连接池 64。上传原件走 `put_stream` 直接读 Starlette 的 `SpooledTemporaryFile`，下载原件走 `download_file` 按块写临时文件；读小对象正文时 `get_object` 和 `.read()` 在同一个线程里做完。
- 纯 Python 的 HTML 解析放进线程只是不卡事件循环，同一进程内仍只用一个核，要多核靠多开 web 实例。

访问者 IP（匿名额度、短信防刷）只读 Nginx 用 `$remote_addr` 写入的 `X-Real-IP`，不读客户端能伪造的 `X-Forwarded-For`；本地没有 Nginx 时用连接对端地址。新建匿名身份时同一网络当天超限抛的 `AnonLimitError`，由 `main.py` 的全局处理统一回 402 + `need_login`。

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
  - **怎么补**（`app/services/share_meta.py`）：默认不解析整页，只扫 `<head>`（跳过注释、`script`、`style`、`title`、`textarea`、`noscript` 里的文字），删掉同名的 `og:*` / `description` 标签，在 `</head>` 前插入新标签，浮标或打印脚本插在最后一个 `</body>` 前。50KB 页面约 0.4 毫秒，直接在事件循环里做；找不到完整 `<head>` 的不规整页面才退回 BeautifulSoup（约 9 毫秒，放到线程里）。上线前对线上 210 个页面比对过，没有一个需要退回。
  - **原文缓存**（`app/services/page_cache.py`）：`/p/`、`/u/` 和发布时取标题都先读进程内缓存，按存储键缓存页面原文。页面版本的存储键带版本 id、写入后不再改，所以不会读到旧内容，也不用跨进程失效；删除、下架、权限、改分享标题都在读缓存前查库判断。按字节淘汰最久没用的，总量 `PAGE_HTML_CACHE_MAX_BYTES`（默认 64MB，0 关闭），单页超过总量的 1/8 不缓存。同一个键同时很多请求时只下载一次。
- `GET /api/scenes`、`GET /api/scenes/{key}`：场景目录与案例。生成请求可带 `scene_key`，新建会话时按 `config/scenes.json` 强制技能。
- `POST /api/publications`、`GET /u/{slug}`：把某个已完成节点发布成固定链接。重新发布只切换版本。同路径下有 `og.png`、`poster.png`、`qr.png`。返回里除链接外还带分享卡片的生效值 `title` / `subtitle` / `align` 与默认值 `default_title`（生成页 `<title>`）/ `default_subtitle`（场景标语或「由星页 StarPage 生成」）。
- `PATCH /api/publications/{slug}`（仅本人）：改写分享卡片的 `title` / `subtitle` / `align`（`left` / `center` / `right`），只改请求里带上的字段，传 `null` 或空串恢复默认；保存后重画 `poster.png` 与 `og.png`。用户改写的值存在 `page_publications.custom_title` / `custom_subtitle` / `card_align`（迁移 `020_share_card_custom.sql`），重新发布另一个版本时保留；`title` / `og_description` 存生效值。自定义了标题时，`/u/{slug}` 连页面 `<title>` 一起替换（微信等只读 `<title>` 的地方也显示用户写的标题）。
- 朋友圈海报（`app/services/share_card.py`）与前端 `share-poster.ts` 同一版式（前端 720×1080，后端 1080×1620）：标题用 Noto Sans CJK SC 粗体，副标题 / 标签随对齐方式左中右排，二维码卡放在文字下沿与底部落款正中。测试：`tests/test_share_card.py`（纯逻辑）、`tests/test_share_card_db.py`（需 `TEST_DATABASE_URL` 与 `OBJECT_STORAGE_PROVIDER=local`）。
- `GET /assets/{id}`：用户上传的图片和从文档里取出的配图，供生成页引用。整页快照不在这里，外部读不到。
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

- `GET /api/billing/account`：返回当前可用积分（登录）或剩余免费次数（匿名/未分配 cookie 的访客返回合成的满额免费态）。登录用户另有冻结中积分、欠费、是否已充值和剩余旗舰首秀次数；`total_balance` 只计可用，不含冻结。
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

计费实现位于 `app/services/billing/`：`pricing.py`（标准积分向上取整，授权折扣向下取整且不低于 `1.25x` 原始成本）、`account.py`（提交时 `FOR UPDATE` 冻结预留，完成时多退少补并封顶记欠费，充值入账时冲抵欠费；扣减顺序 gift→paid，全部按 `idempotency_key` 幂等）、`ledger.py`（复式过账，借贷必平且按 `(event_type, event_ref)` 幂等）。预授权和单纯解冻不写消费流水。倍率、预留额和匿名围栏读 `config/billing.json`。管理端用户余额和预收负债按「可用 + 冻结」统计。技能路由和长文档压缩按各自模型单价计入成本；格式重试只向用户收成功那一轮，账上记两轮真实消耗。详见 `app/services/billing/README.md`。

### 运营埋点与运营后台接口

运营后台数据内核见 `app/analytics/README.md`（明细+聚合两层架构、聚合 CLI、埋点字典、指标口径）。方案见 `doc/20260628/ops-backend-design-and-implementation.md`，方法论见 `wiki/product-ops-metrics-and-north-star.md`。

- `POST /api/analytics/collect`：公开埋点采集（允许匿名），event 走白名单（`landing_view`/`prompt_input`/`generate_click`/`login_success`）+ IP 内存限频，写 `analytics_events`（IP 仅存 HMAC）。`/p` 网关成功返回时还会 fire-and-forget 写 `page_view_events`（区分作者自览）。
- 管理端（`routes_admin_analytics.py` + `require_admin`，前缀 `/api/admin/analytics`，对应前端 `/ops` 九个 Tab）：`GET /realtime`、`/overview`、`/trends`、`/retention`、`/funnel`、`/engagement`、`/quality`、`/virality`、`/users`、`/users/{id}`、`/cases`、`/cases/{id}`。
- 离线聚合：`python -m app.analytics.aggregate`（按自然日幂等 upsert `metric_daily`/`retention_cohort`/`funnel_daily`，可回填重跑），由 `code/systemd/star-page-analytics.{service,timer}` 定时驱动。

### 训练授权、授权优惠与举报

方案与决策见 `doc/20261015/training-consent-report-discount-plan.md`，库表见迁移 `018_training_consent_and_reports.sql`。

- **授权快照**：`users.allow_training`（默认开）是当前开关；`generation_batches` / `generation_tasks` / `page_versions` / `page_assets` / `analytics_events` 的 `training_consent` 记录**数据产生那一刻**的状态：`granted` / `denied` / `legacy`（协议上线前的数据，或尚未看到更新告知的老用户）。判定在 `consent_service.training_consent_for`。**训练取数口径**：`training_consent='granted'` 且所属用户当前 `allow_training=true`。
- **同意记录**：`user_consents` 只追加，事件 `login_agree`（登录勾选，带 `agreement_version`）/ `notice_ack`（老用户点「知道了」）/ `anon_notice`（匿名首访）/ `preference`（开关变更）/ `merge_opt_out`（匿名关过、登录归并时带到正式账号）。协议版本常量 `POLICY_VERSION` 与前端 `LEGAL_POLICY_VERSION` 必须同步。
- **授权优惠**：倍率读 `config/billing.json.training_discount_rate`（限 0.5–1.0，当前 0.92）。按批次快照判断，`granted` 时 `apply_training_discount` 向下取整，同时不低于 `cost_floor_multiplier`（当前 1.25）倍原始成本，优惠额记 `credit_transactions.training_discount_credits`；事后关开关不补扣。前台文案统一写「积分优惠」，不写死折数，方便直接改配置调价。
- **匿名结构样本**：`granted` 的生成结算后，`training_samples.store_training_sample` 抽取去掉文字/链接/联系方式的 HTML 骨架入 `training_samples`（不含 user/page id，按 `content_hash` 去重，`EXTRACTOR_VERSION` 区分抽取规则），关开关不撤回。
- **成本只给管理员看**：生成进度、完成事件、会话详情和用户积分流水接口只向用户返回扣掉的积分和优惠（`credits_charged` / `discount_credits`），不再返回 `cost`、`raw_cost_cny`、`revenue_cny`；回放旧事件时也会去掉 `cost`。实际金额仍记在 `page_versions` 和 `credit_transactions` 里，供 `/admin` 与 `/ops` 读取。
- 接口：`GET / PUT /api/privacy/preferences`（PUT 会给未登录访客懒建匿名身份）、`POST /api/privacy/notice-ack`。`/api/auth/me` 返回 `allow_training`、`needs_policy_notice`。
- **举报**：公开页浮标「举报」跳平台 `/report?page=&slug=`（生成页 CSP 禁止直接请求接口）。`GET /api/reports/target`、`POST /api/reports`（同 IP 同页 24h 一次、每 IP 每日 20 次）；管理端 `GET /api/admin/reports?status=pending|taken_down|rejected|all`、`POST /api/admin/reports/{id}/review`（`takedown` / `reject` / `restore`）。下架即 `pages.moderation_status='blocked'`，`/p` 与 `/u` 返回 451「该页面因违规已下架」，不可再发布，案例墙同步过滤。

匿名体系：未登录访客经 `resolve_actor` 懒签发 HMAC 签名 `sp_anon` cookie 并建 `is_anonymous` 用户，免费 1 次生成（读访客行时加行锁，避免并发把免费次数用穿）；高价模型后端强校验拒绝（匿名只允许 `anon_allowed_models`、单次 ≤ `anon_max_models_per_gen`）；按 IP 每日限制签发匿名 id 数与免费次数。手机验证码登录时把匿名会话/页面归并到正式账号，并首次赠送 450 积分和 1 次旗舰双模首秀（幂等）。未充值用户在首秀用完后只能选轻量模型。

`POST /api/generations` 同时兼容 JSON 和 `multipart/form-data`。上传文件时表单字段为：

- `prompt`：用户页面需求。
- `models`：勾选的并行模型 key（可重复字段或单个 JSON 数组字符串）。
- `files`：文档和图片一起，最多 3 个文件，单文件和单次总大小不超过 50MB，图片单张不超过 10MB。文档支持 `docx`、`pptx`、`xlsx`、`xls`、`pdf`、`txt`、`md`、`html`；图片支持 `png`、`jpg`、`webp`、`gif`。
- `images` / `image_roles`：场景槽位图（头像、二维码等），单张不超过 2MB。

上传资料的处理分三段，细节见 `app/services/document_parser/README.md`：

1. **创建请求只做流式轻预检**：先用 `UploadFile.size` 拦截空文件与超限文件，再直接基于 Starlette 落盘的临时文件流检查文件头、能否打开、是否加密、PDF 不超过 30 页、PPTX 不超过 40 页、DOCX 流式 `iterparse` 不超过 2000 段、Office 文件解压后不超过 150MB。不合格直接 4xx，不建批次、不扣免费次数。图片直接入 `page_assets`，文档原件经 `put_stream` 流式暂存私有存储，web 进程内存不驻留整份文档，接口立即返回。
2. **后台受限子进程解析一次，全批共用**：第一个开跑的模型拉起解析任务，同批其他模型等同一个结果；停掉某个模型不会打断解析，只有同批已经没有排队或在跑的任务时才取消（取消或排空退出时立即 `SIGKILL` 解析子进程）。PDF 用 pdfplumber（逐页 `flush_cache`）+ 单次打开的 pypdfium2，PPTX / DOCX（收满 20 张图即停）/ Excel（`iter_rows` 流式统计画像）各有专门解析器。文档配图（每个文档最多 20 张）上传后立即释放字节并进 `page_assets`；少字大图页、扫描页渲染成整页快照，存私有键，`/assets` 读不到。原件解析完即删。
3. **每轮同模型提取**：会话线上只要有文档，每一轮、每个模型都用自己（关闭思考、纯文本、不附图）结合「全部资料 + 历次需求 + 本轮需求」提取本轮用得上的内容；与资料无关时输出 `<EMPTY/>`，这一轮不塞资料。提取用量并入该模型这次生成的用量计费。

写码阶段的图片走两个通道：文档配图在消息里列出 `/assets/{id}` 供页面使用（快照从不列地址）；能读图的模型在本轮新上传了图片、或提取结果点名了某些资料图片时，会收到缩小后的原图（最多 6 张），续写轮次不重复附历史图片。不能读图的模型只收到文字和地址，进度里提示一句。

整批都失败在资料阶段（解析或提取）时，退回一次匿名免费次数（`generation_batches.free_trial_refunded` 保证只退一次）。

测试：`tests/test_document_parsers.py`（各格式解析与预检上限，现场生成样例文件，见 `tests/doc_fixtures.py`）、`tests/test_document_extraction.py`（提取协议、适配层附图与降级、配图决策、共享解析任务）都是纯逻辑；`tests/test_document_flow_db.py` 用真实数据库、本地存储和真实解析器跑完整流程（两个模型只解析一次、两个图片通道、第二轮重新提取且无关时不塞资料、解析失败只退一次免费次数、停一个模型不打断解析、回访匿名访客创建生成），需要 `TEST_DATABASE_URL`。它和 `tests/test_generation_runner_db.py` 的假存储、假模型在被调用时会用 `tests/idle_txn.py` 断言调用方会话不在事务中，覆盖解析上传、资料提取、附图下载、创建请求暂存、写码模型调用，以及上一版只剩对象存储里的 HTML 时的读取。

Nginx 入口需要同步放开上传体积限制，当前示例配置为 `client_max_body_size 60m`，用于覆盖 50MB 单次上传和 multipart 开销。

为方便后续排查，`generation_tasks` 会记录：

- 用户原始 prompt。
- 上传文件名列表。
- 文件抽取文本（带图片锚点，批次和任务上都有）。
- 本轮从资料里提取出的内容（`extracted_context`，空串表示本轮与资料无关）和提取 prompt 说明（`compression_prompt`）。
- 最终输入页面生成模型的 prompt（附图只记说明，不存图片数据）。
- 模型原始输出文本，便于排查 HTML 提取或清洗导致的内容丢失。
- 生成 HTML 上传后的 OSS 调试定位信息。

LLM 调用默认带重试机制，配置项为 `LLM_RETRY_ATTEMPTS`、`LLM_RETRY_INITIAL_DELAY_MS`、`LLM_RETRY_MAX_DELAY_MS`。底层客户端会重试可恢复的网络、超时、限流和 5xx 错误；资料提取会对“调用成功但正文为空”的情况重试（「与资料无关」用 `<EMPTY/>` 表示，不会被当成空正文）；页面生成会在正式 HTML 输出开始前失败或空输出时重试；带图请求在输出前返回 400 时，去掉图片再试一次。页面生成后、上传前还会检测 Tailwind 运行时依赖，首次命中时追加修正提示并自动重试一次。

## 后台生成

模型调用由后台执行器跑完，和浏览器的事件流连接无关。用户新建对话、切到别的历史、刷新或关掉页面，生成都会继续，回来时从数据库回放进度再跟进实时事件。线上执行器只在 worker 进程里；`all` 角色的单进程里，执行器和接口在同一个进程。

- **web 与 worker 怎么通信**（`app/services/generation_bus.py`）：用 PostgreSQL `LISTEN/NOTIFY`，不依赖 Redis。三个频道：`sp_gen_dispatch`（web 提交批次后发，内容是批次 id）、`sp_gen_cancel`（web 收到停止后发，内容是任务 id）、`sp_gen_batch`（批次有新事件时发，web 据此唤醒挂着的事件流；worker 同一批次 200 毫秒内只发一次）。收：每个进程一条 asyncpg 专用连接，不占连接池，每 `GENERATION_BUS_PING_SECONDS`（默认 20 秒）`SELECT 1` 探活，`GENERATION_BUS_PING_TIMEOUT_SECONDS`（默认 3 秒）内没回就断开重连，重连后补读一遍。发：走连接池的普通会话，提交时送达，所以收到通知时数据已经可见；也就不依赖发送方自己的监听连接是否健康。通知只是提速，丢了由下面两条兜底：worker 每 `GENERATION_DISPATCH_SCAN_SECONDS`（默认 5 秒）扫一次没人领的 `pending` 任务和带停止标记的任务；web 的监听连接断开期间，事件流改为每 2 秒读一次库。
- **连接池**：`create_async_engine` 默认 `DB_POOL_SIZE=30`、`DB_MAX_OVERFLOW=40`（本进程最多同时借出 70 条，等 30 秒），`DB_POOL_RECYCLE_SECONDS=1800`（超过 30 分钟的连接在下次借出前换新）。线上按角色在 systemd 里覆盖：每个 web 10 + 10，worker 20 + 30。SQLAlchemy 在事务 `commit` 后把连接还回池。流式等待模型吐字、下载原件、解析、上传配图、续写提取和附图下载期间都不占连接。预览实例如果和线上连同一个 RDS，把 `DB_POOL_SIZE` 和 `DB_MAX_OVERFLOW` 都调成 5。
- **执行器与认领**（`app/services/generation_runner.py`）：`POST /api/generations` 提交批次后，`all` 角色立即认领一轮（`kick`）；`web` 角色发派发通知，收到的 worker 各认领一轮。名额存在库里：**全站上限** `GENERATION_MAX_CONCURRENCY`（默认 48）比的是 `status = 'running'` 的任务数，**个人上限**（默认 `GENERATION_USER_CONCURRENCY=10`，`users.generation_concurrency` 有值时以它为准，会员放宽限制时写这一列或改 `user_concurrency_limit`）比的是该用户 `running` 的任务数。认领在一个事务里做，先拿咨询锁 `pg_advisory_xact_lock`，所有 worker 排着来：按 `created_at, id` 取最早的 `pending` 任务（最多 500 条、每人最多 64 条），个人名额满的跳过（不占全站名额，一个人提交再多也挤不到别人），全站名额满的不再选，选中的用 `UPDATE … WHERE status = 'pending' AND cancel_requested_at IS NULL` 改成 `running` 并记下 `worker_id`。同一批的任务按选模型的顺序错开 1 微秒写 `created_at`，所以按提交顺序开始。没轮到的写一条 `queued` 事件（`reason` 为 `user_limit` 或 `global_limit`，每种原因只写一次），轮到后写 `started`。任务一结束就补领一轮，并通知其他 worker 也补领（`sp_gen_dispatch`，内容 `slot`）。上限调大（改库或改配置）后下一轮认领立即生效。各 worker 的 `GENERATION_*` 上限要一致；`GENERATION_MAX_CONCURRENCY=0` 等于暂停接新任务。每个任务一个 `asyncio.Task`、各自一个数据库会话。生成器没把任务写到终态（被硬取消、数据库异常）时，`finally` 用新会话补写。
- **续期与接手**（迁移 `026`）：worker 每 `GENERATION_HEARTBEAT_SECONDS`（默认 10 秒）把手上任务的 `heartbeat_at` 刷成现在。超过 `GENERATION_WORKER_LEASE_SECONDS`（默认 45 秒）没续期的 `running` 任务，说明那个 worker 已经不在了：其他 worker 每次定时扫描（默认 5 秒）都会接手，规则同下面的启动扫尾（重跑一次或标失败）。扫尾拿 `pg_try_advisory_xact_lock`，同一时刻只有一个 worker 做；锁放在单独一个会话的事务里，因为扫尾过程中要多次提交。worker 续期时发现任务已经不归自己（被接手后退回 `pending` 或换了 `worker_id`），就停掉本地协程，不写库。续期最多 10 秒才发现一次，所以还有一道写入前的确认：`GenerationService(owner=worker_id)` 写成功、失败、停止这三种终态之前，先 `SELECT … FOR UPDATE` 锁住任务行，确认仍是 `running` 且 `worker_id` 是自己，不是就回滚整个事务（版本、结算、终态都不写）并抛 `TaskLost`；执行器兜底收尾也带上 `owner`。扫尾这边同样在行锁下重新确认任务确实过期才重置或标失败（`_reset_for_rerun`、`finish_unfinished_task(stale_before=…)`），防止查出来到动手之间执行者又续上了期。
- **排空**（`app/worker.py`）：worker 收到 SIGTERM 后不再认领，停止请求和续期照常处理，等手上的任务和批次解析都结束再退出，最多 `WORKER_DRAIN_TIMEOUT_SECONDS`（默认 360 秒）。超时或排空中再收到一次信号时，取消剩下的协程，不改状态，把它们的 `heartbeat_at` 改成 1970 年，其他 worker 下一次扫描（5 秒内）就接手重跑；被 SIGKILL 时等 45 秒续期过期。systemd 的 `TimeoutStopSec` 设成 390 秒。
- **批次解析跨 worker 与防毒文件**（`app/services/document_parser/pipeline.py`，迁移 `027`）：同一批的几个模型可能分在不同 worker 上，资料只解析一次。用原子更新抢解析（`parse_attempts < 2` 且 `parse_status` 是 `pending` 或续期已过期），抢到时 `parse_attempts + 1` 并每 10 秒续期，其余的每秒查一次结果。进程正常退出交接（`_hand_over_parse`）时清空续期并退回本次尝试计数、不删原件，留给别的 worker 马上接手；若某份异常文件连续两次让 worker 崩溃失联（`parse_attempts >= 2` 且续期再次过期），下一个检查的 worker 直接把批次标 `failed`（「该文件多次解析失败，请检查后重新上传」）、删原件并退回一次免费次数，不再反复接手导致连环崩溃。写成功或失败结果前先锁住批次行，确认 `parse_worker_id` 仍是本进程；已被接手就只删本进程上传的配图（快照键按批次固定，接手方会写同一个键，不能删），不碰原件，回去等接手方的结果。
- **停止**：先协作式中止，在两个模型 chunk 之间检查，写成 `cancelled`；3 秒内模型没吐下一个 chunk 就直接取消协程，再由兜底写库。模型已经完整输出后不再响应停止，页面照常发布。
- **停止接口**（`GenerationService.request_cancel`，所有角色都走它）：对每个未结束的任务加行锁，按锁内看到的状态处理。还是 `pending` 的直接写成已停止并解开冻结（认领条件是 `status = 'pending'`，之后不会再被领走），并发一条批次通知，让挂在其他 web 实例上的事件流马上看到「已停止」；已是 `running` 的只写 `generation_tasks.cancel_requested_at`（迁移 `025`），`all` 角色直接停本进程的协程，`web` 角色通知所有 worker，跑它的那个停下并写终态。不能对 `running` 直接调用 `finish_unfinished_task`：它也会写终态，和 worker 抢着写会把状态改回、重复结算。worker 收到的停止通知不是自己手上的任务时记下 60 秒：可能是本进程刚在库里认领、还没登记，登记时补上停止标志。
- **终态与收尾事件同事务**：`completed` / `failed` / `cancelled` 事件和任务终态在同一个事务里提交。事件流据此判断「任务结束」，不会出现状态已结束却漏掉 `completed` 的情况。
- **事件流**（`app/services/generation_events.py`）：只读数据库，每个任务各有一个游标。原因是 `sequence` 全局递增，多个任务并发写入时提交顺序和 `sequence` 顺序不一致，用一个全局游标会永久漏掉晚提交的小号事件。续传 id 是按任务 id 排序后各任务游标用点连接的字符串。一次提交可以包含多行，同一任务内的行序就是序号顺序。思考碎片先在进程里合并再落库：该任务第一段立即写入，之后满 `GENERATION_REASONING_FLUSH_SECONDS`（默认 5 秒）或缓冲满 `GENERATION_REASONING_FLUSH_CHARS`（默认 8000 字）再写一行；其它事件写入前会把缓冲放进同一个事务。硬取消不刷缓冲。输出中每 50 token 的进度另受 `GENERATION_PROGRESS_FLUSH_SECONDS`（默认 2 秒）限制，开始时的 0 token 和完成时的真实用量不限制。回放时仍把同一任务连续的 `reasoning_delta` 合成一条。执行器每次提交后唤醒订阅者，唤醒后等 0.2 秒合批再读库；空闲时每 15 秒发一行 `: ping`，避开 nginx 420 秒读超时。响应头带 `Cache-Control: no-cache, no-transform`，防止中间层压缩把实时事件攒住。
- **启动扫尾**（`all` 角色在 lifespan 里做，`worker` 角色在 `app/worker.py` 启动时做，都受 `GENERATION_STARTUP_SWEEP` 控制，默认开；`web` 角色永远不做）：和定时接手是同一段代码，只是多处理 `pending`。`running` 的只看续期已过期的，在线 worker 手上的不碰。带停止标记的直接写成已停止，不再重跑。其余按最近活动时间（最后一条事件、`started_at`、`created_at` 取最晚）判断，在 `GENERATION_RESUME_WINDOW_MINUTES`（默认 120）内的：没开始过的直接排队；中途被打断且 `retry_count < 1` 的清掉旧事件、写一条 `restarted`、从头重跑一次；其余标 `failed`（「服务重启中断」，不扣积分）。最后把任务都已结束、批次却还停在进行中的批次重算一次状态。
- **单进程部署**：`all` 角色只能开一个 uvicorn 进程（不要加 `--workers`），它没有通知总线，停止请求只在本进程处理。要多开就按线上拆成 web 和 worker。
- **预览实例注意**：在 worktree 里起后端预览时，如果连的是线上库，必须设 `GENERATION_STARTUP_SWEEP=false`，否则一启动就会去重跑或标记线上的任务；也绝不能用 `APP_ROLE=worker`，它的定时扫描会把线上的 `pending` 任务领走。最好连独立测试库。连同一个 RDS 时，把 `DB_POOL_SIZE` 和 `DB_MAX_OVERFLOW` 调成 5，避免预览进程和线上一起把连接池打满。
- **补救卡住的任务**：`script/requeue_stuck_generations.py --batch <id>` 给指定批次没完成的任务刷新最近活动时间，worker 的定时接手几秒内就把续期过期的任务重跑（见 `script/README.md`）。
- **重启耗时**：有 SSE 连接时 uvicorn 会等连接关闭，systemd 到 `TimeoutStopSec=10s` 才强杀；浏览器的事件流会自动重连到另一个 web 实例。worker 先排空，见上。强杀不影响正确性，被打断的任务由其他 worker 接手。
- **改某个用户的上限**：暂无管理界面，直接改库，例如 `UPDATE users SET generation_concurrency = 30 WHERE phone = '...'`；改回默认设为 `NULL`。下次提交生效（迁移 `021_user_generation_concurrency.sql`）。
- **历史事件**：`script/compact_generation_events.py` 把已结束超过 10 分钟的任务收短。连续思考行合并成一行，文本保留；同一阶段、同一状态的连续进度（输出中每 50 token 一条）收成最后一行。序号都留在这段的最后一行（续传只会多发、不会漏发）。默认只统计，`--apply` 才写入，写完执行 `VACUUM (ANALYZE)`。不要在有人还在看这条任务的事件时合并，所以留了 10 分钟。
- **测试**：`tests/test_generation_runner.py` 是纯逻辑单测（扫尾分流、事件合并、续传游标、唤醒器、个人上限取值）；`tests/test_generation_runner_db.py` 用假模型客户端跑真实数据库流程（认领只成功一次、后台跑完并续传、实时跟进、协作式停止、硬取消、排队中停止、HTTP 批次流与停止接口、启动扫尾、个人上限排队与补位、调大上限立即补上、全站上限排队原因、思考碎片合并后序号仍在正式输出之前、历史思考行合并且不碰还在跑的任务），需要 `TEST_DATABASE_URL`，建库方式同上方「会话分叉」的测试说明。该测试会改测试库里所有未结束任务，**不要指向线上库**。`tests/test_web_worker_split_db.py` 在同一个测试里分别构造 web 和 worker 两侧的执行器，用真实的 `LISTEN/NOTIFY` 验证：派发通知丢了由扫描补上；停止排队中和运行中的任务；停止与认领同时发生时只有一方写终态；收到停止时还没登记认领也能停下；扫尾按停止标记收尾并重算停在进行中的批次；跨进程唤醒事件流；监听断开时退回轮询、断线后重连；以及多个 worker：共用全站名额并按顺序补位、退出时交给另一个 worker 马上重跑、在线 worker 的任务不被扫尾、续期发现被接手后停手不写库、被接手后旧执行者跑完也写不回去（不出第二个版本、不多结一次账）、扫尾查出后执行者又续上期就不动、排空时跑完手上的且不领新的、排空超时。DB 测试会捡起库里所有 `pending` 任务，每个用例前后都把遗留的未结束任务收掉。`tests/test_document_flow_db.py` 另测了批次解析的跨 worker 抢占与交接，以及被接手后成功、失败都不写回。`tests/test_page_delivery_fast.py` 测分享信息快速插入（各种不规整写法、退回兜底）、取标题和分享页原文缓存。`tests/test_billing_config_reload.py` 验证计费配置按文件修改时间重载。

## 生成过程事件

批次事件流和单任务事件流使用同一组 SSE 事件，批次流的每条 payload 带 `task_id`：

- `status`：普通状态文案。
- `reasoning_delta`：模型流式返回的 `reasoning_content`。写入前已按上面的间隔合并；回放时同一任务连续的几段还会再合成一条。
- `answer_started`：模型进入正式 HTML 输出阶段，前端开始展示“页面创建中”。
- `progress`：创建节点状态，目前包含 `upload_file`、`parse_file`、`compress_document`、`model_thinking`、`model_output`、`deploy`。`model_output` 完成时携带真实 `input_tokens`/`output_tokens`。
- `completed`：页面已上传 OSS、数据库已更新，返回可访问链接；携带 `usage` 与 `billing`（扣掉的积分和授权优惠）。
- `failed`：生成失败。
- `queued`：名额已满在排队，带 `reason`（`user_limit` / `global_limit`）和 `message`。
- `started`：排队的任务拿到名额、开始调用模型（没排过队的任务不发）。
- `cancelled`：用户停止了这个模型（排队中的也可以停，不扣积分）。
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
- 多模型目录见 `config/llm.models.json`，当前 9 个可用 key：`qwen`、`qwen-plus`、`doubao`、`doubao-code`、`deepseek-v4.1-flash`、`deepseek-v4-pro`、`glm-5.3`、`glm-5.3-flash`、`kimi-k3.1-pro`（MiniMax 暂缓）。变更目录后 worker 和 web 都要重启（`systemctl restart star-page-worker.service` 后执行 `script/restart_web.sh`）；可用 `script/probe-llm-models.py` 探测连通性。
