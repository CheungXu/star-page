# PNG Logo 透明化 + 自动裁剪流程

需要在 Web/移动端把"白底蓝标"或类似设计稿的 PNG logo 直接用上时，最常见三个痛点：

1. 白色背景跟页面背景不融合（在浅色/深色/光晕背景上都会出现可见的白色矩形）。
2. 设计稿四周留了大量白边，自己量 padding 麻烦且不准。
3. 简单"白→透明"会让抗锯齿边缘留下一圈白雾或锯齿。

本条沉淀一套**一次性预处理脚本**的实现方案，5 行命令搞定，并能正确处理抗锯齿边缘。

## 核心思路

| 步骤 | 作用 | 关键参数 |
|---|---|---|
| 1. 清理近白色伪影 | 把 R/G/B 三通道最小值都 ≥ 阈值的像素一律置为纯白，去掉扫描线 / 压缩伪影 | `threshold = 235` |
| 2. GIMP 风格 color-to-alpha | 按比例反推前景纯色 + alpha，保证抗锯齿边缘是带 alpha 的纯色 | 参考色 `(255, 255, 255)` |
| 3. 按 alpha bounding box 自动裁剪 | 找非透明像素的外接矩形，保留少量内边距 | `alpha_threshold = 32`, `padding = 16px` |

**步骤 1 是必须的**：否则原图边缘的扫描伪影线（如 RGB≈228）会被算法识别成"半透明像素"，撑大裁剪边界。

## color-to-alpha 算法（关键代码）

```python
def channel_alpha(channel: np.ndarray, ref_value: float) -> np.ndarray:
    if ref_value < 1e-6:
        return channel / 255.0
    if ref_value > 255 - 1e-6:
        return (255.0 - channel) / 255.0
    lower = (ref_value - channel) / ref_value
    upper_denominator = 255.0 - ref_value
    upper = (channel - ref_value) / upper_denominator if upper_denominator > 0 else 0.0
    return np.maximum(np.maximum(lower, upper), 0.0)

# 三个通道分别算 alpha，取最大值作为像素整体不透明度
alpha_new = np.maximum.reduce([
    channel_alpha(r, ref[0]),
    channel_alpha(g, ref[1]),
    channel_alpha(b, ref[2]),
])

# 还原前景颜色：避免边缘"白雾"
alpha_safe = np.where(alpha_new > 1e-6, alpha_new, 1.0)
r_new = (r - ref[0] * (1 - alpha_new)) / alpha_safe
g_new = (g - ref[1] * (1 - alpha_new)) / alpha_safe
b_new = (b - ref[2] * (1 - alpha_new)) / alpha_safe
```

这是 GIMP "颜色到 Alpha" 工具的算法，关键在于**反推前景色**：`new_color = (color - ref * (1 - alpha)) / alpha`，让原本被白底"稀释"的浅蓝边缘还原为纯蓝 + 部分透明，从而没有白雾。

## 完整脚本

参见 `script/process_logo.py`（约 100 行 Python，仅依赖 `Pillow` + `numpy`）：

```bash
pip install Pillow numpy --break-system-packages   # 服务器 PEP 668 环境
python3 script/process_logo.py
```

输出：

- `image/{name}-transparent.png`：仓库归档版本
- `code/frontend/public/{name}.png`：前端实际加载的版本（直接覆盖原文件）

## 实测对比

原图：`1400 × 752`，纯白背景 + 蓝色星形 + 右侧 1px 灰色伪影线。

| 步骤 | 输出尺寸 | 备注 |
|---|---|---|
| 仅 color-to-alpha + 裁剪（阈值 8） | 1003 × 752 | **裁错了**，右侧伪影线被识别为内容 |
| 加上"近白色清理"（阈值 235）+ 裁剪阈值 32 | 614 × 577 | 正确，紧贴星形外接矩形 + 16px padding |

文件大小：原始 PNG ~212KB，处理后 ~? KB（通常比原图小，因为透明区域 PNG 压缩极致）。

## 配合 CSS 的最佳实践

透明 logo 不要再加任何 `background-color`、`border` 或 `padding`，否则白色卡片感会回来。改用 `filter: drop-shadow(...)` 建立空间感：

```css
.brand-mark .brand-logo {
  display: block;
  width: 116px;
  height: 116px;
  object-fit: contain;
  /* 双层投影：近距离阴影定位 + 远距离散光定空间 */
  filter:
    drop-shadow(0 6px 12px rgba(53, 99, 233, 0.22))
    drop-shadow(0 20px 44px rgba(53, 99, 233, 0.2));
}
```

`drop-shadow` 比 `box-shadow` 强的地方：它跟着 alpha 形状走，对透明 PNG / SVG 都生效，五角星会有星形阴影而不是矩形阴影。

## 适用范围

- 任何"白底单色"或"白底彩色"的 PNG logo / icon / 插图。
- 设计稿来不及给 SVG、只能拿到 PNG 时的过渡方案。
- ⚠️ 如果 logo 主体里**故意包含白色细节**（比如黑底白字 logo、或彩色 logo 里有白色高光），不能用 white 作为参考色，否则白色细节会一起消失。这种场景应该取 logo **背景中心区域**的颜色作为参考色，比如 `reference=(248, 248, 248)`。

## 与 SVG 的关系

最佳方案永远是直接拿到 SVG 矢量原图：体积更小、任意缩放清晰、可以直接用 `currentColor` 跟随主题色变换。

本流程是"拿不到 SVG 时的退化方案"，处理后的 PNG 在 96-256px 范围内显示效果与原图无差异，但放大到 512px+ 会暴露 PNG 的栅格化锯齿。**一旦拿到 SVG，应该立即替换并删除处理脚本**。

## 相关条目

- `frontend-design-tokens-and-prompt-card.md`：Header Logo 设计要点（透明 PNG + drop-shadow 优于白底卡片）。
