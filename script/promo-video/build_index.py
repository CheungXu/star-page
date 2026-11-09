"""把 timings.json（配音实际时长）填进 index.template.html，生成 hyperframes 的 index.html。

每段画面从该段第一句配音开始，到下一段第一句开始结束；字幕逐句出现。

用法：
    python script/promo-video/build_index.py --project "data/视频/202610/星页一分钟讲解"
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEAD = 0.6  # 片头留白
TAIL = 4.2  # 最后一句之后的结尾停留


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    project = ROOT / args.project
    timings = json.loads((project / "src/timings.json").read_text(encoding="utf-8"))

    voice_end = timings[-1]["start"] + timings[-1]["duration"]
    total = round(LEAD + voice_end + TAIL, 2)

    order: list[str] = []
    starts: dict[str, float] = {}
    for item in timings:
        if item["scene"] not in starts:
            order.append(item["scene"])
            starts[item["scene"]] = 0.0 if not order[:-1] else round(LEAD + item["start"], 3)
    scenes = {}
    for i, key in enumerate(order):
        end = starts[order[i + 1]] if i + 1 < len(order) else total
        scenes[key] = {"start": starts[key], "dur": round(end - starts[key], 3)}

    caps = []
    for i, item in enumerate(timings):
        start = round(LEAD + item["start"], 3)
        end = round(LEAD + timings[i + 1]["start"], 3) if i + 1 < len(timings) else round(LEAD + voice_end + 0.4, 3)
        caps.append(
            f'      <div id="cap-{i + 1:02d}" class="clip cap" data-start="{start}" data-duration="{round(end - start, 3)}" data-track-index="3">'
            f'<span data-t="{start}">{html.escape(item["text"])}</span></div>'
        )

    out = (project / "src/index.html.tpl").read_text(encoding="utf-8")
    out = out.replace("{{TOTAL}}", str(total)).replace("{{LEAD}}", str(LEAD))
    out = out.replace("{{VOICE}}", str(round(voice_end, 3)))
    out = out.replace("{{CAPTIONS}}", "\n".join(caps))
    out = out.replace("{{SCENES}}", json.dumps(scenes))
    for key, s in scenes.items():
        out = out.replace(f"{{{{{key}.start}}}}", str(s["start"])).replace(f"{{{{{key}.dur}}}}", str(s["dur"]))
    if "{{" in out:
        raise SystemExit("模板里还有未替换的占位符")
    (project / "index.html").write_text(out, encoding="utf-8")
    print(f"已生成 index.html，总长 {total}s")
    for key, s in scenes.items():
        print(f"  {key}: {s['start']}s 起，{s['dur']}s")


if __name__ == "__main__":
    main()
