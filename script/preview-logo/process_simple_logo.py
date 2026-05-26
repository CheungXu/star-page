#!/usr/bin/env python3
"""处理方案一的简化版 logo

输入：用户上传的源图（白底蓝色五角星 + 中间 </> 与两颗小星）
输出：preview-logo/stars-page-logo-simple.png（白底透明 + 自动裁剪 + 边缘羽化）

处理步骤：
1. 转 RGBA
2. 遍历像素：以"接近白色"作为透明度判定
   - 极接近白色 (亮度 >= 245) → 完全透明
   - 半透明区间 (220 ~ 245) → 线性过渡，让边缘更柔和
   - 蓝色主体保留原色，alpha=255
3. 根据 alpha 通道 getbbox() 自动裁剪四周空白
4. 留出少量呼吸感的 padding
5. 保存 PNG
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(
    "/root/.cursor/projects/root-star-page/assets/"
    "c__Users_texzhang_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_"
    "images_stars-page-logo2-67251c36-80cf-4ef8-99b7-124015eff755.png"
)
DST = Path(__file__).resolve().parent / "stars-page-logo-simple.png"

# 透明度阈值：lum >= HIGH 完全透明，lum <= LOW 完全不透明，中间线性过渡
LUM_HIGH = 245.0
LUM_LOW = 220.0
# 裁剪后留出的边距（像素）
PADDING = 16


def main() -> None:
    if not SRC.exists():
        print(f"[错误] 源图不存在：{SRC}")
        sys.exit(1)

    print(f"[读取] {SRC}")
    img = Image.open(SRC).convert("RGBA")
    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]
    print(f"[尺寸] 原始 {w} x {h}")

    rgb = arr[..., :3]
    # 用最大通道值作为亮度估计，对接近白色的像素更敏感
    lum = rgb.max(axis=-1)

    alpha = np.where(
        lum >= LUM_HIGH,
        0.0,
        np.where(
            lum <= LUM_LOW,
            255.0,
            (LUM_HIGH - lum) / (LUM_HIGH - LUM_LOW) * 255.0,
        ),
    )
    arr[..., 3] = np.clip(alpha, 0.0, 255.0)

    out = Image.fromarray(arr.astype(np.uint8), mode="RGBA")

    bbox = out.getbbox()
    if bbox is None:
        print("[警告] getbbox 返回空，跳过裁剪")
    else:
        left, top, right, bottom = bbox
        print(f"[裁剪] bbox = {bbox}, 主体尺寸 {right - left} x {bottom - top}")
        left = max(0, left - PADDING)
        top = max(0, top - PADDING)
        right = min(w, right + PADDING)
        bottom = min(h, bottom + PADDING)
        out = out.crop((left, top, right, bottom))
        print(f"[裁剪] 含 {PADDING}px 边距后尺寸 {out.size[0]} x {out.size[1]}")

    out.save(DST, format="PNG", optimize=True)
    size_kb = DST.stat().st_size / 1024
    print(f"[完成] {DST}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
