# 星页计费系统设计方案

> 本文为计费系统的设计方案落档，便于后续回溯。实施记录另见同目录 `billing-system-implementation-record.md`。

## 一、目标与确认前提

- 计费单位：**积分（credit），整数存储，1 RMB = 100 积分**（1 积分 = 0.01 元），全程整数避免浮点误差。
- 扣费公式：`扣费积分 = ceil(LLM原始成本(元) × 模型倍率 × 100)`，**非整数一律向上取整**；并设底线 `max(结果, 1)`，避免极小成本取整为 0 被白嫖。倍率**按模型可配**、默认 `1.2`、允许 `<1`（打折）。原始成本沿用现有 `page_versions.total_cost_cny` 的算法。
- 匿名策略（主流做法）：分配匿名设备标识 → 赠送 **2 次**免费生成 → 用尽/复制分享链接/打开更多时提示登录 → 注册时把匿名历史与身份**归并**到正式账号。
- 匿名模型限制（控成本）：匿名只能选次优模型（如允许 `deepseek-v4-flash`、`qwen-plus`、`doubao`），高价旗舰（`qwen`(max)、`deepseek-v4-pro`、`glm-5.2`、`kimi-k2.7-code` 等）**仍然可见但置灰**——点击时轻量提示"注册后可用"，用以吸引注册而非隐藏；**单次最多生效 2 个模型**，勾第 3 个时轻量提示需注册；`max_tokens` 不变。允许集合走配置，可维护。
- 注册用户：首次注册一次性赠送 **1000 积分**；消费时**先扣赠送、后扣充值**。
- 管理员：`config` 手机号白名单识别（复用现有手机登录），`/admin` 路由后台。
- 支付：微信/支付宝**先预留 mock**，购买页用 mock 套餐与 mock 支付回调，后续替换。

## 二、用户体系 × 计费的结合（匿名→注册）

复用现有 `users + owner_user_id` 外键体系，把匿名访客建模为 `users` 中 `is_anonymous=true` 的真实行，从而所有 `conversations/pages/page_versions` 无需改外键即可挂载。

- `resolve_actor(session, request, response)`：有登录态→返回登录用户；否则校验**签名 `sp_anon` cookie**，合法则取对应匿名用户、否则在通过 IP 天花板后懒签发新匿名 id（HMAC 签名）并回写 cookie。
- 改造点：`routes_generation.py` 的 `create_generation` / SSE、`routes_conversations.py`、`/api/pages` 历史，由 `get_current_user` 改为 `resolve_actor`（允许匿名）。`/api/billing/recharge`、`set_password`、`/api/admin/*` 仍强制登录/管理员。
- 归并在 `auth_service.login_with_code` 内完成：读取请求中的匿名用户，将其 `conversations/pages` 改挂到手机用户并标记匿名用户 merged；调用 `BillingService.grant_signup_bonus`（幂等，仅首次）。

## 三、财务模型（复式记账）

采用**双层账本**：业务层「积分钱包流水」+ 财务层「复式记账总账」。积分本质是**预收账款（递延收入）**，收入在**消费时**确认，而非充值时。区分**充值积分**与**赠送积分**两个桶，二者会计处理不同。

会计科目（Chart of Accounts）：
- 资产：`1001 现金/在途`、`1002 应收第三方支付`
- 负债：`2001 预收账款-充值积分`、`2002 赠送积分负债`、`2101 应付账款-LLM供应商`
- 收入：`5001 服务收入`
- 成本/费用：`6001 LLM算力成本(COGS)`、`6601 推广赠送费用`

业务事件 → 复式分录：
- 充值支付成功（金额 X 元）：借 `1001 现金` X / 贷 `2001 预收-充值` X
- 赠送发放（面值 F 元）：借 `6601 推广费` F / 贷 `2002 赠送负债` F（未用赠送 = 2002 余额）
- 消费 N 积分（拆分赠送部分 g、充值部分 p；原始成本 C 元）：
  - 充值部分：借 `2001 预收-充值` p×0.01 / 贷 `5001 服务收入` p×0.01
  - 赠送部分：借 `2002 赠送负债` g×0.01 / 贷 `5001 服务收入(赠送核销)` g×0.01
  - 算力成本：借 `6001 COGS` C / 贷 `2101 应付-LLM` C（毛利 = 确认收入 − C）
- 匿名试用消费：仅记 `6001 COGS` / `2101 应付`，无收入（计入试用获客成本）。

审计要点：每条消费流水**快照** `raw_cost_cny / markup / revenue_cny`，配置变更不污染历史；所有写入用 `idempotency_key`（充值用 order_id、消费用 version_id）保证可重入。

## 四、数据库（迁移 `011_billing.sql`，raw SQL + IF NOT EXISTS 幂等）

- `users`：新增 `is_anonymous`、`anon_device_id`、`merged_into_user_id`。
- `anon_visitors`：`anon_device_id unique`、`user_id`、`sign_ip`、`user_agent`、`free_generations_used`、`created_at`（支撑 IP 天花板与风控审计）。
- `credit_accounts`：`user_id` 唯一、`paid_balance`、`gift_balance`、`free_generations_used`、`total_recharged_credits`、`total_spent_credits`、`signup_bonus_granted`、`updated_at`。
- `credit_transactions`（追加写流水）：`type`、`credits_delta`、`paid_delta`、`gift_delta`、`balance_after`、`ref_type/ref_id`、`model_key`、`raw_cost_cny`、`markup`、`revenue_cny`、`idempotency_key unique`、`created_at`。
- `recharge_orders`：`package_key`、`amount_cny`、`base_credits`、`bonus_credits`、`status`、`payment_provider`、`provider_txn_id`、`paid_at`。
- `credit_packages`：套餐配置（DB 表，启动 seed mock）。
- 复式账本：`ledger_accounts`、`ledger_entries`、`ledger_entry_lines`。

## 五、后端服务与接口

- `app/services/billing/`：`account.py`（钱包扣减 gift→paid/赠送/充值入账）、`ledger.py`（复式过账）、`pricing.py`（读 `config/billing.json` 算 credits）。
- `/api/models` 的 `ModelInfo` 增加 `anon_allowed` 字段。
- 计费接入 `generation_service.py`：`create_batch` 前置校验；任务完成处 `BillingService.settle(version_id, ...)` 幂等结算。
- 用户接口 `routes_billing.py`（均需登录）：`account/transactions/packages/recharge/mock-pay`。
- 管理员接口 `routes_admin.py` + `require_admin`：`overview/transactions/ledger/users`。
- `config/billing.json`：`default_markup`、`free_trial_generations`、`signup_bonus_credits`、`model_markups`、`anon_allowed_models`、`anon_max_models_per_gen`、`admin_phones`、`packages`。

## 六、前端（Next.js）

- 顶部状态：登录显示积分余额；匿名显示「剩余免费 N 次」。
- 匿名模型选择：高价模型可见但置灰，点击轻提示"注册后可用"；多选最多生效 2 个，勾第 3 个轻提示注册。
- 登录拦截：第 3 次生成、复制分享链接时弹登录框。
- 新路由 `app/pricing`：套餐卡 + mock 支付闭环。
- 新路由 `app/admin`：财务仪表盘（管理员可见）。
- 抽出 `app/lib/api.ts` 与 `app/lib/billing.ts`。

## 七、安全加固

### 7.1 匿名防滥用（分层围栏）
- 签名 cookie（HMAC）：服务端只认自己签发的匿名 id，伪造/篡改失效。
- IP 天花板：`anon_visitors` 记录 `sign_ip`，限制每 IP 每日签发匿名 id 数与免费生成次数。
- 成本兜底：匿名仅次优模型 + 最多 2 个模型；后端强校验。
- 后续增强：设备指纹 / 验证码 / 风控评分。

### 7.2 充值接口加固
- 价格服务端权威：客户端只传 `package_key`。
- 幂等 + 状态机：`UPDATE ... WHERE status='pending'` 原子流转 + 唯一约束。
- mock 生产禁用：`mock-pay` 仅在 `APP_ENV != production` 开放。
- 真实回调预留：验签 + 金额比对 + 订单归属校验。
- 限流与边界：建单频率、单笔上下限校验。

## 八、实施阶段

1. 阶段零：落档本方案。
2. 阶段一：迁移 + ORM + billing 服务 + 单测。
3. 阶段二：resolve_actor 匿名体系 + 生成计费接入 + 注册归并赠送。
4. 阶段三：用户侧展示与拦截 + pricing 购买页。
5. 阶段四：财务后台。
6. 阶段五：沉淀 readme/doc/wiki。

## 九、待后续提供 / 可迭代

- 真实微信/支付宝商户配置。
- 套餐定价与折扣最终数值。
- 模型成本/倍率长期维护入口（先配置文件，后续可做 admin 编辑页）。
