# 手机号用户系统预研方案

## 背景

当前项目已经具备用户维度的数据模型，但请求身份仍固定使用默认测试用户 `default_test`。本阶段目标是接入真实用户系统：用户通过手机号验证码登录或自动注册，业务数据按真实用户隔离。

本次方案以当前预研阶段为前提，不背历史数据包袱：不迁移、不认领、不兼容展示 `default_test` 期间产生的历史数据。真实用户系统接入后，业务接口以当前登录用户为准。

## 当前项目现状

- 后端是 FastAPI + SQLAlchemy async + PostgreSQL。
- 前端是 Next.js 单页应用，主要逻辑集中在 `code/frontend/app/page.tsx`。
- 数据库已经有 `users`、`conversations.owner_user_id`、`pages.owner_user_id`、`generation_tasks.requested_by_user_id`、`page_permissions`。
- 当前请求用户来源是 `code/backend/app/core/default_user.py` 的 `ensure_default_user()`。
- 历史、生成、页面访问等业务逻辑已经有用户隔离结构，但运行时所有人共享 `default_test`。
- 前端没有登录 UI，也没有 Cookie/Token 登录态处理。

## 产品目标

- 使用一个登录窗口完成手机号验证码登录/注册。
- 输入手机号并校验验证码后，如果用户不存在则自动注册。
- 登录后按用户隔离历史、生成会话、页面节点和权限。
- 登录后如果未设置密码，弹窗提醒设置密码，并支持稍后再处理。
- 短信发送做成独立模块，当前接入阿里云短信服务，后续可切换其他供应商。

## 方案选择

登录态使用服务端 Session + `HttpOnly` Cookie，例如 `sp_session`。

选择理由：

- 当前前端和后端通过同源 `/api` 代理协作，Cookie Session 接入成本低。
- `EventSource` 不方便携带自定义 `Authorization` header，Cookie 更适合 SSE。
- 前端 JS 不直接持有 token，降低凭证泄露风险。

短信服务使用 Provider 抽象：

```text
AuthService
  -> SmsService
  -> SmsProvider
      -> AliyunSmsProvider
      -> MockSmsProvider
      -> FutureProvider
```

业务层只关心“发送验证码短信”，不直接依赖阿里云 SDK。

## 后端规划

新增模块：

- `code/backend/app/api/routes_auth.py`
  - `POST /api/auth/sms/send`
  - `POST /api/auth/sms/login`
  - `GET /api/auth/me`
  - `POST /api/auth/logout`
  - `POST /api/auth/password`
- `code/backend/app/core/auth.py`
  - `get_current_user()`
  - `get_optional_user()`
- `code/backend/app/services/auth_service.py`
  - 验证码生成、哈希、校验、消费
  - 用户自动注册
  - Session 创建与注销
  - 密码设置
- `code/backend/app/services/sms/base.py`
  - 定义短信 Provider 接口
- `code/backend/app/services/sms/aliyun.py`
  - 阿里云短信实现
- `code/backend/app/services/sms/mock.py`
  - 本地开发和测试实现

数据库迁移：

- `users` 增加：
  - `phone`
  - `phone_verified`
  - `password_set_at`
  - `last_login_at`
- 调整 `password_hash`，允许手机号验证码用户没有初始密码。
- 新增 `sms_verification_codes`：
  - `phone`
  - `scene`
  - `code_hash`
  - `expires_at`
  - `attempt_count`
  - `sent_ip`
  - `consumed_at`
- 新增 `user_sessions`：
  - `session_token_hash`
  - `user_id`
  - `expires_at`
  - `revoked_at`
  - `user_agent`
  - `ip_address`

配置项：

- `SMS_PROVIDER`
- `SMS_CODE_TTL_SECONDS`
- `SMS_SEND_COOLDOWN_SECONDS`
- `SMS_DAILY_LIMIT_PER_PHONE`
- `SMS_DAILY_LIMIT_PER_IP`
- `ALIYUN_SMS_SIGN_NAME`
- `ALIYUN_SMS_TEMPLATE_CODE`
- `AUTH_SESSION_COOKIE_NAME`
- `AUTH_SESSION_TTL_SECONDS`
- `AUTH_COOKIE_SECURE`

阿里云短信 SDK 仅在 `AliyunSmsProvider` 中使用。验证码模板参数建议保持最小，例如 `{"code": "123456"}`。

## 业务接口调整

需要从 `ensure_default_user()` 切换为真实用户的地方：

- `code/backend/app/api/routes_conversations.py`
  - 历史列表、详情、收藏、删除都按当前登录用户过滤。
- `code/backend/app/services/generation_service.py`
  - 创建和续写生成使用当前登录用户。
  - 会话归属校验继续保留。
- `code/backend/app/api/routes_generation.py`
  - 创建生成要求登录。
  - SSE 事件要求当前用户拥有对应 task。
- `code/backend/app/api/routes_pages.py`
  - `/api/pages` 和 `/api/pages/{page_id}` 加用户权限校验。
  - `/p/{conversation_id}/{page_id}` 使用可选用户：public 页面允许匿名访问，private/restricted 页面必须登录且有权限。

预研阶段不保留“未登录使用 default_test 创建和查看历史”的旧行为。上线真实用户系统后，未登录访问业务 API 返回 401，前端引导登录。

## 前端规划

登录入口放在左侧历史栏底部：

- 展开态：显示脱敏手机号、登录按钮、退出按钮。
- 收起态：显示用户图标按钮。
- 点击后打开同一个登录弹窗。

登录弹窗流程：

1. 输入手机号。
2. 点击获取验证码，展示倒计时。
3. 输入验证码并登录。
4. 后端校验成功后，如果手机号未注册则自动注册。
5. 登录成功后刷新 `/api/auth/me` 和历史列表。
6. 如果用户未设置密码，弹窗提醒设置密码，可选择稍后再说。

前端 API 调整：

- 封装统一 `apiFetch()`，默认带 `credentials: "include"`。
- `EventSource` 使用 Cookie 登录态。
- 401 统一触发登录弹窗。
- 未登录时历史区显示“登录后查看历史”。
- 创建页面前如果未登录，先打开登录窗口。

## 安全策略

- 验证码只保存哈希，不保存明文。
- 默认 6 位数字验证码，5 分钟有效。
- 验证码最多尝试 5 次，超过后需重新发送。
- 发送验证码按手机号和 IP 做频控。
- Session token 只保存哈希。
- Cookie 使用 `HttpOnly`、`SameSite=Lax`，HTTPS 后开启 `Secure`。
- 日志不记录验证码、Session token、完整手机号。

## 阶段取舍

本阶段暂不做：

- 历史数据迁移或认领。
- 邮箱登录。
- 第三方登录。
- 找回密码。
- 完整的指定用户可见邀请流。
- 团队、组织、多租户空间。

本阶段保留：

- 密码字段和设置密码接口，作为手机号验证码之外的后续登录能力基础。
- 页面权限表和 `public/private/restricted` 判断基础。
- 短信 Provider 抽象，方便后续切换供应商。

## 验证计划

- 后端迁移可重复执行。
- Mock 短信发送和验证码登录流程可跑通。
- 阿里云短信 provider 在配置齐全时可发送验证码。
- 未登录访问历史、创建生成返回 401。
- 登录后历史只展示当前手机号用户数据。
- 登录后创建页面写入当前用户 ID。
- 退出后无法访问个人历史。
- public 页面匿名可访问。
- private/restricted 页面匿名不可访问。
- 前端登录弹窗、倒计时、自动注册、设置密码提醒、退出登录均可用。

## 后续实施顺序

1. 新增数据库迁移和模型字段。
2. 实现 auth 核心、Session Cookie 和验证码表逻辑。
3. 实现短信 Provider 抽象、Mock provider 和阿里云 provider。
4. 实现 auth API。
5. 将历史、生成、SSE、页面接口切换到真实当前用户。
6. 在前端加入登录窗口、用户区、设置密码提醒和 401 处理。
7. 做本地 mock 验证和阿里云短信联调。
