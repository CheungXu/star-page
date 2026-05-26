"""把 Star Page Logo 处理为透明背景 + 自动裁剪白边的版本。

处理步骤：
1. 读取原始 PNG 图片。
2. 用 GIMP 的"颜色到 alpha"算法（参考白色），把白色背景转为透明，
   同时保留蓝色星形的抗锯齿边缘（避免锯齿感）。
3. 根据 alpha 通道自动裁剪四周空白区域。
4. 输出透明背景的 PNG，并同步到前端 public 目录。

用法：
    python3 script/process_logo.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "image" / "stars-page-logo.png"
DST_PATH = REPO_ROOT / "image" / "stars-page-logo-transparent.png"
PUBLIC_PATH = REPO_ROOT / "code" / "frontend" / "public" / "stars-page-logo.png"


def clean_near_white(img: Image.Image, threshold: int = 235) -> Image.Image:
    """把"接近白色"的像素直接置为纯白，去掉扫描/压缩伪影。

    阈值 235 表示：当像素 R/G/B 三通道最小值都 ≥ 235 时，视为白色背景。
    这样可以消除原图边缘那条很浅的灰色伪影线。
    """
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    near_white_mask = arr.min(axis=-1) >= threshold
    arr[near_white_mask] = [255, 255, 255]
    return Image.fromarray(arr, mode="RGB")


def color_to_alpha(img: Image.Image, reference=(255, 255, 255)) -> Image.Image:
    """GIMP 风格的颜色到 alpha 算法。

    将与 reference 颜色越接近的像素 alpha 越低，并对前景色做反向还原，
    保证抗锯齿边缘是带 alpha 的纯色（视觉上无锯齿白边）。
    """
    rgba = np.array(img.convert("RGBA"), dtype=np.float32)
    ref = np.array(reference, dtype=np.float32)

    r = rgba[..., 0]
    g = rgba[..., 1]
    b = rgba[..., 2]
    a = rgba[..., 3] / 255.0

    # 每个通道与参考色的"距离比例"，取最大值作为像素整体的不透明度
    def channel_alpha(channel: np.ndarray, ref_value: float) -> np.ndarray:
        if ref_value < 1e-6:
            return channel / 255.0
        if ref_value > 255 - 1e-6:
            return (255.0 - channel) / 255.0
        lower = (ref_value - channel) / ref_value
        upper_denominator = 255.0 - ref_value
        upper = (channel - ref_value) / upper_denominator if upper_denominator > 0 else 0.0
        return np.maximum(np.maximum(lower, upper), 0.0)

    alpha_new = np.maximum.reduce([
        channel_alpha(r, ref[0]),
        channel_alpha(g, ref[1]),
        channel_alpha(b, ref[2]),
    ])
    alpha_new = np.clip(alpha_new, 0.0, 1.0)

    # 还原前景颜色：避免边缘出现"白雾"
    alpha_safe = np.where(alpha_new > 1e-6, alpha_new, 1.0)
    r_new = (r - ref[0] * (1 - alpha_new)) / alpha_safe
    g_new = (g - ref[1] * (1 - alpha_new)) / alpha_safe
    b_new = (b - ref[2] * (1 - alpha_new)) / alpha_safe

    final_alpha = (alpha_new * a * 255.0).clip(0, 255)
    final = np.stack([
        np.clip(r_new, 0, 255),
        np.clip(g_new, 0, 255),
        np.clip(b_new, 0, 255),
        final_alpha,
    ], axis=-1).astype(np.uint8)
    return Image.fromarray(final, mode="RGBA")


def trim_transparent(img: Image.Image, padding: int = 16, alpha_threshold: int = 32) -> Image.Image:
    """根据 alpha 通道自动裁剪四周空白，并保留少量内边距。

    alpha_threshold 阈值偏高，避免被极浅的"半透明伪影"撑大边界。
    """
    alpha = np.array(img.split()[-1])
    nonzero = np.argwhere(alpha > alpha_threshold)
    if nonzero.size == 0:
        return img
    top, left = nonzero.min(axis=0)
    bottom, right = nonzero.max(axis=0) + 1

    top = max(0, int(top) - padding)
    left = max(0, int(left) - padding)
    bottom = min(img.height, int(bottom) + padding)
    right = min(img.width, int(right) + padding)
    return img.crop((left, top, right, bottom))


def main() -> None:
    if not SRC_PATH.exists():
        raise FileNotFoundError(f"找不到源图片：{SRC_PATH}")

    print(f"读取源图：{SRC_PATH}")
    img = Image.open(SRC_PATH)
    print(f"  原始尺寸：{img.size}，模式：{img.mode}")

    print("步骤 1/3：清理近白色伪影（阈值 235）")
    cleaned = clean_near_white(img, threshold=235)

    print("步骤 2/3：白色背景转透明（GIMP 风格 color-to-alpha）")
    transparent = color_to_alpha(cleaned, reference=(255, 255, 255))

    print("步骤 3/3：自动裁剪四周空白（保留 16px 内边距，alpha 阈值 32）")
    trimmed = trim_transparent(transparent, padding=16, alpha_threshold=32)
    print(f"  裁剪后尺寸：{trimmed.size}")

    DST_PATH.parent.mkdir(parents=True, exist_ok=True)
    trimmed.save(DST_PATH, format="PNG", optimize=True)
    print(f"已保存透明版 logo：{DST_PATH}")

    PUBLIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    trimmed.save(PUBLIC_PATH, format="PNG", optimize=True)
    print(f"已同步到前端 public：{PUBLIC_PATH}")


if __name__ == "__main__":
    main()
