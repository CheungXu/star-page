# 国内旗舰大模型定价调研与接入方案

> 调研基准日：**2026-06-14**。实施落档：**2026-06-14**。  
> 关联配置：`config/llm.models.json`、`config/billing.json`、`config/llm.env.example`。  
> 技术细节见 `wiki/llm-provider-abstraction.md`。

## 一、背景与目标

星页（StarPage）是服务端按 Token 调用大模型的 B2C 产品，LLM 成本直接影响毛利与用户积分定价。本文档完成：

1. DeepSeek V4、GLM-5.1、Kimi K2.6、MiniMax M3 的**官方与云厂商定价**对比；
2. 各模型在星页场景下的**最优接入路径**（含灾备）；
3. 模型分档、计费倍率与**已落地的配置变更**说明。

**重要边界**：Coding Plan / Token Plan 订阅（火山 Coding Plan、智谱 Coding Plan、Kimi Code、MiniMax Token Plan 等）面向 IDE/CLI 工具，endpoint 与限额体系与 B2C 服务端 API **不通用**，仅适合团队内部开发降本，**不能**替代生产按量 API。

---

## 二、星页项目现状（接入前）

| 维度 | 状态 |
|------|------|
| 部署 | 阿里云 ECS + RDS + OSS + 百炼 DashScope |
| 已接入 | `qwen3.7-max`、`qwen3.7-plus`、`doubao-seed-2-0-pro/code` |
| 调用 | OpenAI 兼容 API，多模型并行，技能路由 + 思考模式 |
| 成本核算 | `llm.models.json` 维护单价 + `cost.py` 自算 |
| 用户计费 | 1 RMB = 100 积分，默认 markup 1.2×（见 `billing-system-plan.md`） |

典型单次 HTML 生成（输入 ~15K + 输出 ~12K，含思考链）成本对比如下：

| 模型 | 单次成本（约） | 定位 |
|------|---------------|------|
| DeepSeek V4 Flash | ¥0.04 | 匿名/轻量 |
| DeepSeek V4 Pro | ¥0.12 | 性价比旗舰 |
| qwen-plus | ¥0.13 | 原匿名档 |
| doubao Seed 2.0 Pro | ¥0.24 | 原默认档之一 |
| MiniMax M3 | ¥0.27 | 代码/HTML 质量优先 |
| GLM-5.1 | ¥0.38 | 高端可选 |
| Kimi K2.6 | ¥0.42 | 高端 Agent |
| qwen-max | ¥0.61 | 成本高，逐步降级 |

---

## 三、官方 API 按量定价（元/百万 Tokens）

| 模型 | 输入（未命中缓存） | 输入（缓存命中） | 输出 | 上下文 |
|------|-------------------|-----------------|------|--------|
| **DeepSeek V4 Flash** | ¥1 | ¥0.02 | ¥2 | 1M |
| **DeepSeek V4 Pro** | ¥3 | ¥0.025 | ¥6 | 1M |
| **GLM-5.1** | ¥6（≤32K）/ ¥8（>32K） | ¥1.3 / ¥2.0 | ¥24 / ¥28 | 200K |
| **Kimi K2.6** | ¥6.5 | ¥1.1 | ¥27 | 262K |
| **MiniMax M3** | ¥4.2（≤512K）/ ¥8.4（>512K） | ¥0.84 / ¥1.68 | ¥16.8 / ¥33.6 | 1M |

补充：

- DeepSeek V4 Pro 自 2026-05 起永久为原刊例价 1/4（[官方定价页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)）。
- Kimi Batch API = 标准价 60%；内置网页搜索 ¥0.03/次（星页当前未用）。
- 思考模式下 reasoning token **计入输出**，HTML 生成场景 output 占比高。

---

## 四、云厂商按量对比

三大云厂商对上述模型**刊例价基本一致**（与官网对齐），差异在折扣、免费额度、SLA 与接入便利：

| 渠道 | DeepSeek V4 | GLM-5.1 | Kimi K2.6 | MiniMax M3 |
|------|-------------|---------|-----------|------------|
| 官网 | Flash ¥1/2，Pro ¥3/6 | ¥6/24 | ¥6.5/27 | ¥4.2/16.8 |
| **阿里云百炼** | 同官网 | 同官网 | `kimi/kimi-k2.6` | `MiniMax/MiniMax-M3` |
| 火山方舟 | 同官网 | 已接入 | 已接入 | 已接入 |
| 腾讯云 TokenHub | 原厂直供同价 | 同官网 | 同官网 | 同官网 |

**百炼叠加优惠**：通用节省计划约 **5 折**（充 ¥250 得 ¥500 抵扣）；新用户各模型通常有 100 万 Token 免费额度。

**火山/腾讯 Coding Plan**（¥40~200/月）：折算约 API 1 折，但为 IDE 专用 prompt 限额，**不用于星页生产**。

---

## 五、各模型最优接入方案

星页生产 API 选型原则：**同一云账单、OpenAI 兼容、流式 usage + reasoning_content、与现有 `llm.models.json` 目录机制一致**。

### 5.1 总览

| 目录 key | 最优主路 | model ID | extra_body | 灾备 |
|----------|----------|----------|------------|------|
| `deepseek-v4-flash` | **阿里云百炼** | `deepseek-v4-flash` | `enable_thinking` + `reasoning_effort: high` | DeepSeek 官网 |
| `deepseek-v4-pro` | **阿里云百炼** | `deepseek-v4-pro` | 同上 | DeepSeek 官网 |
| `glm-5.2` | **阿里云百炼** | `glm-5.2` | `enable_thinking: true` | 智谱开放平台 |
| `kimi-k2.7-code` | **阿里云百炼** | `kimi-k2.7-code` | `enable_thinking: true` | Moonshot 官方 API |

**统一主路参数**（已在 `config/llm.models.json` 落地）：

```text
base_url  = https://dashscope.aliyuncs.com/compatible-mode/v1
api_key   = QWEN_API_KEY（回退 LLM_API_KEY）
protocol  = openai
```

选择百炼的理由：星页已在阿里云；与 Qwen 共用 DashScope Key；百炼 `/api/v1/models` 可拉实时单价；控制台统一开通与对账。

### 5.2 DeepSeek V4 Flash / Pro

**能力**：1M 上下文；思考/非思考可切换；FIM、Tool Calls；Pro 偏复杂推理，Flash 偏高性价比。

**百炼接入**（推荐）：

```text
POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
Authorization: Bearer $QWEN_API_KEY
{
  "model": "deepseek-v4-flash",  // 或 deepseek-v4-pro
  "messages": [...],
  "stream": true,
  "stream_options": { "include_usage": true },
  "enable_thinking": true,
  "reasoning_effort": "high"
}
```

文档：[百炼 DeepSeek API](https://help.aliyun.com/zh/model-studio/deepseek-api)

**官网灾备**（百炼故障时改目录三项即可）：

```text
base_url = https://api.deepseek.com
model    = deepseek-v4-flash / deepseek-v4-pro
api_key  = DEEPSEEK_API_KEY
```

注意：旧名 `deepseek-chat` / `deepseek-reasoner` 将于 **2026-07-24** 弃用。

**星页分档**：匿名允许 `deepseek-v4-flash`；注册用户默认推荐 Pro + Flash 双模型并行（待产品切换 `default_models`）。

### 5.3 智谱 GLM-5.1

**能力**：200K 上下文；SWE-Bench Pro ~58.4%；代码与 Agent 任务强；MIT 开源。

**百炼接入**（推荐）：

```text
model = glm-5.2   // 或 ZHIPU/GLM-5.2
extra_body = { "enable_thinking": true }
```

文档：[百炼 GLM 调用](https://help.aliyun.com/zh/model-studio/glm)

**智谱官网灾备**：

```text
base_url = https://open.bigmodel.cn/api/paas/v4
model    = glm-5.2
api_key  = ZHIPU_API_KEY
```

**定价分档**：输入 ≤32K 与 >32K 两档（已在 `pricing.tiers` 配置）。

**星页分档**：付费高级档，markup **1.5×**。

### 5.4 Kimi K2.6

**能力**：262K 上下文；原生多模态（图/视频）；Agent 与长程代码强；SWE-Bench Pro ~58.6%。

**百炼接入**（推荐）：

```text
model = kimi-k2.7-code
extra_body = { "enable_thinking": true }
```

文档：[百炼 Kimi 月之暗面 API](https://help.aliyun.com/zh/model-studio/kimi-api-by-moonshot-ai)

**Moonshot 官方灾备**：

```text
base_url = https://api.moonshot.cn/v1
model    = kimi-k2.7-code
api_key  = MOONSHOT_API_KEY
```

**注意**：`kimi.com/coding` 为 Kimi Code 会员 endpoint，**不可**用于星页后端。

**星页分档**：付费高级档，markup **1.5×**；星页当前纯文本 HTML 场景暂不需要多模态输入。

### 5.5 MiniMax M3

**能力**：1M 上下文；Coding + Agent；原生多模态；SWE-Bench Pro **59.0%**（开源领先）。

**百炼接入**（推荐）：

```text
model = MiniMax/MiniMax-M3
extra_body = { "thinking": { "type": "adaptive" } }
```

文档：[百炼 MiniMax 直供 API](https://help.aliyun.com/zh/model-studio/minimax-api-by-minimax)

**MiniMax 官网灾备**：

```text
base_url = https://api.minimaxi.com/v1
model    = MiniMax-M3
api_key  = MINIMAX_API_KEY
```

**定价分档**：输入 ≤512K 与 >512K 两档（长上下文倍价）。

**星页分档**：质量优先可选档，markup **1.3×**。

---

## 六、灾备切换操作手册

当百炼单点故障或某模型在百炼不可用时，**只改 `config/llm.models.json` 对应条目**，无需改后端代码：

```text
1. 修改 base_url → 官方 endpoint
2. 修改 model     → 官方 model ID（见上表）
3. 修改 api_key_env → DEEPSEEK_API_KEY / ZHIPU_API_KEY / MOONSHOT_API_KEY / MINIMAX_API_KEY
4. 按官方文档调整 extra_body（字段名可能不同）
5. 重启 backend：systemctl restart star-page-backend
```

`config/llm.env.example` 已预留灾备 Key 变量名注释。

---

## 七、模型能力参考（HTML 生成相关）

| 模型 | SWE-Bench Pro（参考） | 星页匹配度 |
|------|----------------------|-----------|
| MiniMax M3 | ~59.0% | 代码/HTML 质量高，价格适中 |
| Kimi K2.6 | ~58.6% | Agent 强，单价最高 |
| GLM-5.1 | ~58.4% | 代码强，偏贵 |
| DeepSeek V4 Pro | ~55% | **最佳性价比旗舰** |
| DeepSeek V4 Flash | — | 匿名/简单页面 |

Benchmark 偏代码修复，上线前建议对星页做小规模 A/B（页面美观度、CSS/JS 正确性）。

---

## 八、星页推荐模型矩阵

```text
┌─────────────────────────────────────────────────────────────┐
│  匿名试用（控本）                                              │
│  deepseek-v4-flash（+ 可选 qwen-plus / doubao 保留）          │
├─────────────────────────────────────────────────────────────┤
│  注册用户推荐默认（待产品切换 default_models）                   │
│  deepseek-v4-pro + deepseek-v4-flash                          │
├─────────────────────────────────────────────────────────────┤
│  付费高级档（markup 1.3~1.5×）                                 │
│  glm-5.2 / kimi-k2.7-code（MiniMax M3 暂缓）                  │
├─────────────────────────────────────────────────────────────┤
│  逐步降级                                                      │
│  qwen-max（成本高，仅保留兼容）                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 九、已落地配置变更（2026-06-14）

### 9.1 `config/llm.models.json`

新增 5 个模型条目，均走百炼主路 + `QWEN_API_KEY`：

| key | label | model ID |
|-----|-------|----------|
| `deepseek-v4-flash` | DeepSeek V4 Flash | `deepseek-v4-flash` |
| `deepseek-v4-pro` | DeepSeek V4 Pro | `deepseek-v4-pro` |
| `glm-5.2` | 智谱 GLM-5.2 | `glm-5.2` |
| `kimi-k2.7-code` | Kimi K2.7 Code | `kimi-k2.7-code` |

`default_models` **暂保持** `["qwen", "doubao"]`，避免未开通百炼新模型时首访体验突变；产品确认后可改为 `["deepseek-v4-pro", "deepseek-v4-flash"]`。

### 9.2 `config/billing.json`

- `anon_allowed_models` 增加 `deepseek-v4-flash`（置首，作为匿名首选）。
- 新增模型 markup：`deepseek-v4-*` 1.2×，`glm-5.2` / `kimi-k2.7-code` 1.5×。MiniMax M3 暂不上线（百炼产品未开通）。

### 9.3 运维 checklist

1. 百炼控制台开通 DeepSeek V4、GLM-5.1、Kimi K2.6、MiniMax M3 模型服务。
2. 确认 `config/llm.env` 中 `QWEN_API_KEY` 有效。
3. 重启 backend 后访问 `/api/models`，确认新模型出现在可选列表。
4. 各模型试生成 1 次，核对 SSE `usage` + `cost` 与百炼账单。
5. 官方调价时只改 `llm.models.json` 的 `pricing.tiers`，无需改代码。

---

## 十、内部开发降本（与生产分离）

| 场景 | 推荐 | 月费参考 |
|------|------|----------|
| 团队 Vibe Coding | 火山 Coding Plan Lite | ¥40（首月 ~¥9） |
| 单模型深度编程 | MiniMax Token Plan Plus | ¥49 |
| 多模型体验 | 火山 Coding Plan（6 模型混用） | ¥40~200 |

**不得**将上述套餐 Key 接入星页 `generation_service`。

---

## 十一、风险与维护

1. **价格变动频繁**：DeepSeek 2026 年已多次调价；定期核对 [官方定价页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) 与百炼 `/api/v1/models`。
2. **百炼 model ID**：Kimi 在百炼侧使用裸名（如 `kimi-k2.7-code`），勿用 `kimi/` 前缀；GLM 可用 `glm-5.2` 或 `ZHIPU/GLM-5.2`。
3. **思考模式成本**：输出 token 含 reasoning，长思考会显著拉高单次费用。
4. **可选后续**：百炼 pricing API 定时同步脚本；按模型 SLA 做自动 failover。

---

## 十二、参考链接

- [DeepSeek 官方定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
- [Kimi K2.6 定价](https://platform.kimi.com/docs/pricing/chat-k26)
- [MiniMax Token Plan](https://platform.minimaxi.com/docs/guides/pricing-token-plan)
- [百炼 DeepSeek](https://help.aliyun.com/zh/model-studio/deepseek-api)
- [百炼 GLM](https://help.aliyun.com/zh/model-studio/glm)
- [百炼 Kimi](https://help.aliyun.com/zh/model-studio/kimi-api-by-moonshot-ai)
- [百炼 MiniMax](https://help.aliyun.com/zh/model-studio/minimax-api-by-minimax)
- 项目 wiki：`wiki/llm-provider-abstraction.md`
