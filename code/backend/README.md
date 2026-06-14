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
- `POST /api/generations`：创建页面生成任务。
- `GET /api/generations/{task_id}/events`：通过 SSE 推送思考过程和完成状态。
- `GET /api/conversations`：查询当前登录用户的会话历史列表，支持 `favorite_only=true` 和 `q=关键词`。
- `GET /api/conversations/{conversation_id}`：获取会话详情，供左侧历史恢复生成树。
- `PATCH /api/conversations/{conversation_id}`：更新会话收藏状态。
- `DELETE /api/conversations/{conversation_id}`：软删除会话，写入 `deleted_at` 后不再出现在历史列表。
- `GET /api/models`：返回多模型目录（含可用性）供前端勾选。
- `GET /api/pages`：旧版页面级历史接口，当前左侧历史已切到会话级接口。
- `GET /api/pages/{page_id}`：获取页面元数据。
- `GET /p/{conversation_id}/{page_id}`：页面访问网关，校验节点归属会话后从私有 OSS 读取 HTML 并返回（带展示型沙箱 CSP）。会话被软删后其下节点链接同步失效（404）。

### 计费接口（积分制，1 元 = 100 积分）

用户侧（`routes_billing.py`）：

- `GET /api/billing/account`：返回积分余额（登录）或剩余免费次数（匿名/未分配 cookie 的访客返回合成的满额免费态）。
- `GET /api/billing/transactions`：当前用户积分流水（登录或带匿名 cookie 的访客）。
- `GET /api/billing/packages`：充值套餐（DB `credit_packages`，启动 seed mock）。
- `POST /api/billing/recharge`：建单，**只收 `package_key`**，金额/积分服务端按套餐计算（价格服务端权威）。需登录。
- `POST /api/billing/recharge/{id}/mock-pay`：mock 支付回调入账，**仅非生产环境（`APP_ENV != production`）开放**，原子流转 `pending→paid` + 幂等入账。

管理员侧（`routes_admin.py` + `require_admin`，管理员身份以数据库 `admin_phones` 表为准，按手机号白名单，可预授权未注册手机号；用 `script/set_admin.py` 维护）：

- `GET /api/admin/billing/overview`：财务总览，三段式呈现——①付费业务（不含赠送）：累计充值现金/付费确认收入/付费 COGS/付费毛利；②赠送台账：赠送已发放/未用负债/已核销收入/赠送+试用成本；③含赠送合计：综合收入/成本/毛利；并含预收账款、预付云资源余额与累计充值、科目余额。收入与成本按消费流水的 paid/gift 占比拆分。
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

匿名体系：未登录访客经 `resolve_actor` 懒签发 HMAC 签名 `sp_anon` cookie 并建 `is_anonymous` 用户，免费 2 次生成；高价模型后端强校验拒绝（匿名只允许 `anon_allowed_models`、单次 ≤ `anon_max_models_per_gen`）；按 IP 每日限制签发匿名 id 数与免费次数。手机验证码登录时把匿名会话/页面归并到正式账号并首次赠送 1000 积分（幂等）。

`POST /api/generations` 同时兼容 JSON 和 `multipart/form-data`。上传文件时表单字段为：

- `prompt`：用户页面需求。
- `models`：勾选的并行模型 key（可重复字段或单个 JSON 数组字符串）。
- `files`：当前最多允许 3 个文件，单文件和单次总大小均不超过 50MB，支持 `docx`、`pptx`、`xlsx`、`xls`、`pdf`、`txt`、`md`、`html`。后端会抽取为 Markdown/文本并合并到 LLM 上下文；PDF 仅保证可复制文本内容的抽取，扫描版图片 PDF 或加密 PDF 可能解析失败。

如果抽取文本超过 5000 字符，后端会先调用 LLM 将资料压缩为面向页面生成的任务简报，再把压缩后的资料放入最终生成 prompt。

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

## 生成过程事件

`GET /api/generations/{task_id}/events` 使用 SSE 推送以下事件：

- `status`：普通状态文案。
- `reasoning_delta`：模型流式返回的 `reasoning_content`。
- `answer_started`：模型进入正式 HTML 输出阶段，前端开始展示“页面创建中”。
- `progress`：创建节点状态，目前包含 `upload_file`、`parse_file`、`compress_document`、`model_thinking`、`model_output`、`deploy`。`model_output` 完成时携带真实 `input_tokens`/`output_tokens` 及自算 `cost`（元）。
- `completed`：页面已上传 OSS、数据库已更新，返回可访问链接；可选携带 `usage` 与 `cost` 摘要。
- `failed`：生成失败。

`model_thinking` 节点承载模型 reasoning 内容，前端默认展开且支持收起。`model_output` 生成中优先展示估算 output tokens；完成后展示 API 返回的真实输入/输出 token，并在进度区下方显示费用摘要（按 `llm.models.json` 的 `pricing` 自算，含分档标签）。用量写入 `page_versions`（迁移 `008_llm_usage_cost.sql`）。

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
- 当前 Qwen 配置：`LLM_PROVIDER=qwen`、`LLM_PROTOCOL=openai`、`LLM_MODEL=qwen3.7-max`。
