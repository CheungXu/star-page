# 手机号用户系统实施记录

## 本次实现范围

已按预研方案接入手机号验证码用户系统：

- 新增手机号用户字段、短信验证码表、用户 Session 表。
- 新增 Auth API：发送验证码、验证码登录/自动注册、读取当前用户、退出登录、设置密码。
- 新增短信 Provider 抽象：`mock` 与 `aliyun` 两种实现。
- 阿里云短信接入签名 `深圳星泽创旗科技`、模板 `SMS_507185134`，模板参数为 `{"code": "验证码"}`。
- 历史、生成、SSE、页面元数据接口已从 `default_test` 切换为当前登录用户。
- `/p/{conversation_id}/{page_id}` 分享网关保留 public 匿名访问；private/restricted 使用可选登录用户鉴权。
- 前端左侧历史栏底部新增用户区，支持手机号验证码登录/注册、退出登录、未设置密码提醒。
- 前端 API 请求统一携带 Cookie；SSE 使用 Cookie 登录态。

## 关键文件

- `code/backend/migrations/007_phone_auth.sql`
- `code/backend/app/models/entities.py`
- `code/backend/app/core/auth.py`
- `code/backend/app/api/routes_auth.py`
- `code/backend/app/services/auth_service.py`
- `code/backend/app/services/sms/`
- `code/frontend/app/page.tsx`
- `code/frontend/app/globals.css`
- `config/README.md`
- `code/backend/README.md`

## 配置说明

短信真实配置放在 `config/sms.env`，后端配置加载器已包含该文件。

推荐配置：

```text
SMS_PROVIDER=aliyun
ALIYUN_SMS_SIGN_NAME=深圳星泽创旗科技
ALIYUN_SMS_TEMPLATE_CODE=SMS_507185134
ALIYUN_SMS_ENDPOINT=dysmsapi.aliyuncs.com
```

轻量应用服务器当前使用最小权限 RAM 用户 AK/SK：

```text
ALIBABA_CLOUD_ACCESS_KEY_ID=...
ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
```

这两个值不写入文档、不提交 Git。后续迁移到 ECS/RAM Role 时，可以去掉 AK/SK，让阿里云默认凭据链接管。

## 数据库迁移

已执行：

```bash
cd code/backend
.venv/bin/python -m app.db.migrate
```

输出确认已执行到：

```text
已执行迁移：007_phone_auth.sql
```

## 验证结果

已完成：

- 后端 Python 编译检查通过。
- FastAPI 应用导入检查通过。
- 数据库迁移执行成功。
- 前端 `npm run lint` 通过，仅保留原有 `<img>` 优化警告。
- 前端 `npm run build` 通过。
- 使用测试手机号 `15827488805` 调用真实短信发送接口成功，返回：

```text
status= 200
{'ok': True, 'cooldown_seconds': 60}
```

## 注意事项

- 当前阶段不迁移、不认领、不兼容 `default_test` 历史数据。
- 未登录访问业务 API 会返回 401，前端会引导登录。
- 若 60 秒内重复向同一手机号发验证码，会触发冷却限制。
- 设置密码只是登录后的补充能力，本阶段仍以手机号验证码为主。
- 修改后端代码后，线上 systemd 服务需要重启才会生效。
