"""为宣传视频逐句生成中文配音（百炼 qwen3-tts-flash），拼成一条音轨并输出每句时间。

每句单独合成，已存在的分句音频会跳过（断点续跑）；改了台词需删除对应 wav 重做。

用法：
    cd code/backend && . .venv/bin/activate
    python ../../script/promo-video/build_voice.py --project "data/视频/202610/星页一分钟讲解"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[2]
TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
GAP = 0.35  # 句间停顿（秒）


def load_key() -> str:
    for line in (ROOT / "config/llm.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("config/llm.env 中没有 LLM_API_KEY")


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--voice", default="Cherry")
    args = parser.parse_args()

    project = ROOT / args.project
    lines = json.loads((project / "src/script.json").read_text(encoding="utf-8"))
    part_dir = project / "assets/voice-parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("LLM_API_KEY") or load_key()

    for i, item in enumerate(tqdm(lines, desc="配音")):
        wav = part_dir / f"{i:02d}.wav"
        if wav.exists():
            continue
        resp = requests.post(
            TTS_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "qwen3-tts-flash",
                # speak 只给配音用（如网址的读法），字幕仍显示 text
                "input": {"text": item.get("speak", item["text"]), "voice": args.voice, "language_type": "Chinese"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        url = resp.json()["output"]["audio"]["url"]
        tmp = wav.with_suffix(".tmp.wav")
        tmp.write_bytes(requests.get(url, timeout=60).content)
        tmp.rename(wav)

    # 拼接：每句后补 GAP 秒静音，统一 24kHz 单声道
    timings, cursor = [], 0.0
    concat = part_dir / "concat.txt"
    silence = part_dir / "gap.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(GAP), str(silence)],
        check=True,
    )
    entries = []
    for i, item in enumerate(lines):
        wav = part_dir / f"{i:02d}.wav"
        d = duration(wav)
        timings.append({"scene": item["scene"], "text": item["text"], "start": round(cursor, 3), "duration": round(d, 3)})
        cursor += d + GAP
        entries += [f"file '{wav.name}'", f"file '{silence.name}'"]
    concat.write_text("\n".join(entries), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-ar", "24000", "-ac", "1", str(project / "assets/voice.wav")],
        check=True,
    )
    (project / "src/timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"配音总长 {cursor:.2f}s，共 {len(lines)} 句")


if __name__ == "__main__":
    main()
