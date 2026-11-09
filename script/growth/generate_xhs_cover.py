#!/usr/bin/env python3
"""生成小红书封面图并保存。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "config" / "llm.env"
OUTPUT_DIR = ROOT / "data" / "文案" / "202610" / "01-春招两分钟"
TRIAL_DIR = OUTPUT_DIR / "试稿"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def generate_cover_ark(prompt: str, save_path: Path) -> bool:
    load_env(ENV_FILE)
    api_key = os.environ.get("ARK_API_KEY", "").strip()
    if not api_key:
        print("错误: 未配置 ARK_API_KEY")
        return False

    url = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": "doubao-seedream-5-0-pro-260628",
        "prompt": prompt,
        "size": "1024x1365",
        "watermark": False,
    }

    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        print(f"正在向火山方舟提交生图请求...")
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "data" in data and len(data["data"]) > 0:
                image_url = data["data"][0].get("url")
                if image_url:
                    print(f"生图成功，正在下载图片到 {save_path.name}...")
                    img_req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(img_req, timeout=60) as img_resp:
                        save_path.write_bytes(img_resp.read())
                    print(f"保存完毕: {save_path}")
                    return True
            print(f"返回数据异常: {data}")
            return False
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"火山方舟 HTTP 错误 {e.code}: {err_msg}")
        return False
    except Exception as e:
        print(f"生成异常: {e}")
        return False


if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    target_name = sys.argv[2] if len(sys.argv) > 2 else "封面_test.png"
    target_file = TRIAL_DIR / target_name if "试稿" in target_name else OUTPUT_DIR / target_name
    generate_cover_ark(prompt, target_file)
