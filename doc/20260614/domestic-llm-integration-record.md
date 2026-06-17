# 国内旗舰大模型接入实施记录

> 日期：2026-06-14 ~ 2026-06-17  
> 方案背景见 `domestic-llm-pricing-and-integration.md`。

## 一、交付摘要

在 `config/llm.models.json` 扩展多模型目录，生产主路统一走**阿里云百炼**（复用 `QWEN_API_KEY`），豆包仍走火山方舟 `ARK_API_KEY`。

| 目录 key | 展示名 | model ID | 通道 |
| --- | --- | --- | --- |
| `deepseek-v4-flash` | DeepSeek V4 Flash | `deepseek-v4-flash` | 百炼 |
| `deepseek-v4-pro` | DeepSeek V4 Pro | `deepseek-v4-pro` | 百炼 |
| `glm-5.2` | 智谱 GLM-5.2 | `glm-5.2` | 百炼 |
| `kimi-k2.7-code` | Kimi K2.7 Code | `kimi-k2.7-code` | 百炼 |
| （暂缓）MiniMax M3 | — | — | 百炼产品未开通 |

原有 `qwen` / `qwen-plus` / `doubao` / `doubao-code` 不变。`default_models` 仍为 `["qwen", "doubao"]`，待产品确认后再切 DeepSeek 组合。

## 二、连通性探测（2026-06-17）

脚本：`script/probe-llm-models.py`

```bash
python3 script/probe-llm-models.py
```

**结果：8/8 可用**（上线后实测）

| key | 结果 |
| --- | --- |
| qwen / qwen-plus / doubao / doubao-code | ✅ |
| deepseek-v4-flash / deepseek-v4-pro | ✅ |
| glm-5.2 | ✅ |
| kimi-k2.7-code | ✅ |

### 关键坑：百炼第三方 model ID

| 错误写法 | 现象 | 正确写法 |
| --- | --- | --- |
| `kimi/kimi-k2.6` | HTTP 400 产品未开通 | `kimi-k2.6` 或 `kimi-k2.7-code`（裸名） |
| `MiniMax/MiniMax-M3` | HTTP 400 产品未开通 | 需先在百炼控制台开通 MiniMax 产品 |

GLM、DeepSeek 在百炼侧直接用 `glm-5.2`、`deepseek-v4-pro` 等裸名即可。

## 三、版本迭代

| 阶段 | 变更 |
| --- | --- |
| 初版 | 接入 GLM-5.1、Kimi K2.6（`kimi/` 前缀）、MiniMax M3 |
| 探测后 | Kimi 改为裸名；M3 因未开通暂不上线 |
| 上线前 | GLM-5.1 → **GLM-5.2**；Kimi K2.6 → **Kimi K2.7 Code**；移除 M3 目录项 |

## 四、计费配置

`config/billing.json` 同步：

- `anon_allowed_models` 首位增加 `deepseek-v4-flash`
- markup：`deepseek-v4-*` 1.2×，`glm-5.2` / `kimi-k2.7-code` 1.5×

## 五、上线步骤

配置变更**无需**前端 rebuild，只需重启后端（`get_model_registry` 启动时 `lru_cache`）：

```bash
systemctl restart star-page-backend.service
curl -s http://127.0.0.1:8000/api/models | python3 -m json.tool
```

2026-06-17 已执行重启，`/api/models` 返回 8 个可用模型。

## 六、后续可迭代

- 百炼控制台开通 MiniMax 后再加回 `minimax-m3`
- 产品确认后将 `default_models` 切为 `deepseek-v4-pro` + `deepseek-v4-flash`
- 百炼 `/api/v1/models` 定时同步 `pricing.tiers`（可选脚本）
