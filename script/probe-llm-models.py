#!/usr/bin/env python3
"""探测 llm.models.json 中各模型连通性与百炼/方舟模型目录中的最新版本。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "llm.models.json"
ENV_FILE = ROOT / "config" / "llm.env"

# 探测用最小请求，控制成本
PROBE_MAX_TOKENS = 16
PROBE_MESSAGE = "只回复一个字：好"


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


def resolve_key(entry: dict) -> str:
    for env_name in (entry.get("api_key_env"), entry.get("api_key_fallback_env")):
        if not env_name:
            continue
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return ""


def http_json(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 60) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw[:500]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:500]
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def probe_chat(entry: dict, api_key: str) -> tuple[bool, str, str | None]:
    """返回 (成功, 摘要, 实际 model id)"""
    if not api_key:
        return False, "缺少 API Key", None

    url = entry["base_url"].rstrip("/") + "/chat/completions"
    body: dict = {
        "model": entry["model"],
        "messages": [{"role": "user", "content": PROBE_MESSAGE}],
        "max_tokens": PROBE_MAX_TOKENS,
        "stream": False,
    }
    params = {**(entry.get("params") or {})}
    for k, v in params.items():
        if v is not None:
            body[k] = v
    body.update(entry.get("extra_body") or {})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    status, payload = http_json("POST", url, headers, body, timeout=90)

    if status == 200 and isinstance(payload, dict):
        model_id = payload.get("model")
        choices = payload.get("choices") or []
        content = ""
        if choices:
            msg = choices[0].get("message") or {}
            content = (msg.get("content") or "")[:40]
        usage = payload.get("usage") or {}
        tokens = usage.get("total_tokens", "?")
        return True, f"HTTP 200 · 回复={content!r} · tokens={tokens}", model_id

    if isinstance(payload, dict):
        err = payload.get("error") or payload
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or json.dumps(err, ensure_ascii=False)[:200]
        else:
            msg = str(err)[:200]
        return False, f"HTTP {status} · {msg}", None
    return False, f"HTTP {status} · {payload}", None


def fetch_dashscope_models(api_key: str) -> list[dict]:
    url = "https://dashscope.aliyuncs.com/api/v1/models?page_no=1&page_size=200"
    headers = {"Authorization": f"Bearer {api_key}"}
    status, payload = http_json("GET", url, headers, timeout=30)
    if status != 200 or not isinstance(payload, dict):
        return []
    data = payload.get("data") or payload.get("output") or payload
    if isinstance(data, dict):
        return data.get("models") or data.get("data") or []
    if isinstance(data, list):
        return data
    return []


def fetch_ark_models(api_key: str) -> list[dict]:
    url = "https://ark.cn-beijing.volces.com/api/v3/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    status, payload = http_json("GET", url, headers, timeout=30)
    if status != 200 or not isinstance(payload, dict):
        return []
    return payload.get("data") or []


def model_id_from_item(item: dict) -> str:
    for key in ("model", "model_id", "id", "name"):
        val = item.get(key)
        if val:
            return str(val)
    return ""


def find_related_catalog(api_key: str, provider_prefixes: list[str]) -> list[str]:
    models = fetch_dashscope_models(api_key)
    ids: list[str] = []
    for item in models:
        mid = model_id_from_item(item).lower()
        if any(p in mid for p in provider_prefixes):
            ids.append(model_id_from_item(item))
    return sorted(set(ids))


def find_ark_related(api_key: str, prefixes: list[str]) -> list[str]:
    models = fetch_ark_models(api_key)
    ids: list[str] = []
    for item in models:
        mid = model_id_from_item(item).lower()
        if any(p in mid for p in prefixes):
            ids.append(model_id_from_item(item))
    return sorted(set(ids))


LATEST_HINTS: dict[str, dict] = {
    "qwen": {
        "prefixes": ["qwen3.7", "qwen3.6", "qwen3.5", "qwen3-max", "qwen-max"],
        "note": "百炼 Qwen 系列",
    },
    "doubao": {
        "prefixes": ["doubao-seed", "doubao"],
        "note": "方舟豆包 Seed 系列",
    },
    "deepseek": {
        "prefixes": ["deepseek-v4", "deepseek-v3"],
        "note": "DeepSeek 系列",
    },
    "glm": {
        "prefixes": ["glm-5", "glm-4", "zhipu/glm", "zhipu/"],
        "note": "智谱 GLM 系列",
    },
    "kimi": {
        "prefixes": ["kimi-k2", "kimi/"],
        "note": "Kimi 系列",
    },
    "minimax": {
        "prefixes": ["minimax-m", "minimax/"],
        "note": "MiniMax 系列",
    },
}


def pick_latest(candidates: list[str], current: str) -> tuple[str | None, list[str]]:
    if not candidates:
        return None, []
    current_l = current.lower()
    # 若当前已在列表中且无更高版本线索，认为已是最新
    related = [c for c in candidates if current_l in c.lower() or c.lower() in current_l]
    newer = [c for c in candidates if c.lower() != current_l and current_l.split("/")[-1] not in c.lower()]
    # 简单启发：同系列里字典序/名称更长或版本号更大
    best = max(candidates, key=lambda x: x.lower())
    if best.lower() == current_l or best.lower().endswith(current_l.split("/")[-1]):
        return None, candidates
    if current in candidates and len(newer) == 0:
        return None, candidates
    if best != current:
        return best, candidates
    return None, candidates


def main() -> int:
    load_env(ENV_FILE)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    models = catalog.get("models") or []

    qwen_key = resolve_key({"api_key_env": "QWEN_API_KEY", "api_key_fallback_env": "LLM_API_KEY"})
    ark_key = resolve_key({"api_key_env": "ARK_API_KEY"})

    print("=" * 72)
    print("星页 LLM 模型连通性探测")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"QWEN/百炼 Key: {'已配置' if qwen_key else '未配置'} (QWEN_API_KEY 或 LLM_API_KEY 回退)")
    print(f"ARK Key: {'已配置' if ark_key else '未配置'}")
    print("=" * 72)

    results: list[dict] = []
    for entry in models:
        key = entry["key"]
        api_key = resolve_key(entry)
        ok, detail, actual_model = probe_chat(entry, api_key)
        results.append(
            {
                "key": key,
                "label": entry.get("label"),
                "configured_model": entry["model"],
                "actual_model": actual_model,
                "ok": ok,
                "detail": detail,
                "has_key": bool(api_key),
            }
        )
        status = "✅ 通" if ok else ("⚠️ 无Key" if not api_key else "❌ 失败")
        print(f"\n[{status}] {key} ({entry.get('label')})")
        print(f"  配置 model: {entry['model']}")
        if actual_model and actual_model != entry["model"]:
            print(f"  实际返回:   {actual_model}")
        print(f"  {detail}")

    print("\n" + "=" * 72)
    print("模型目录 · 同系列可用 ID（用于判断是否有更新版本）")
    print("=" * 72)

    if qwen_key:
        family_map = {
            "qwen": ["qwen"],
            "qwen-plus": ["qwen"],
            "deepseek-v4-flash": ["deepseek"],
            "deepseek-v4-pro": ["deepseek"],
            "glm-5.2": ["glm"],
            "kimi-k2.7-code": ["kimi"],
        }
        for entry in models:
            fam = family_map.get(entry["key"])
            if not fam:
                continue
            prefixes = []
            for f in fam:
                prefixes.extend(LATEST_HINTS[f]["prefixes"])
            candidates = find_related_catalog(qwen_key, prefixes)
            latest, all_ids = pick_latest(candidates, entry["model"])
            print(f"\n{entry['key']} · 当前 `{entry['model']}`")
            if not all_ids:
                print("  百炼 models API 未返回同系列条目（可能未开通或接口无 listing）")
                continue
            show = all_ids[:15]
            more = len(all_ids) - len(show)
            print(f"  百炼同系列({len(all_ids)}): {', '.join(show)}" + (f" … +{more}" if more > 0 else ""))
            if latest and latest.lower() != entry["model"].lower():
                print(f"  ⚠️ 可能存在更新 ID: `{latest}`")
            else:
                print("  ✓ 当前 ID 在同系列 listing 中，或未检测到更高版本别名")
    else:
        print("\n跳过百炼目录拉取：无 QWEN/LLM API Key")

    if ark_key:
        for entry in models:
            if entry.get("provider") != "doubao":
                continue
            prefixes = LATEST_HINTS["doubao"]["prefixes"]
            candidates = find_ark_related(ark_key, prefixes)
            latest, all_ids = pick_latest(candidates, entry["model"])
            print(f"\n{entry['key']} · 当前 `{entry['model']}`")
            if not all_ids:
                print("  方舟 models API 未返回同系列条目")
                continue
            show = all_ids[:15]
            more = len(all_ids) - len(show)
            print(f"  方舟同系列({len(all_ids)}): {', '.join(show)}" + (f" … +{more}" if more > 0 else ""))
            if latest and latest.lower() != entry["model"].lower():
                print(f"  ⚠️ 可能存在更新 ID: `{latest}`")
            else:
                print("  ✓ 当前 ID 在方舟 listing 中")
    else:
        print("\n跳过方舟目录拉取：无 ARK_API_KEY")

    print("\n" + "=" * 72)
    ok_count = sum(1 for r in results if r["ok"])
    print(f"汇总: {ok_count}/{len(results)} 模型探测成功")
    print("=" * 72)

    # 写入探测结果供 doc 引用
    out = ROOT / "doc" / "20260614" / "llm-model-probe-result.json"
    out.write_text(
        json.dumps(
            {
                "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n结果已写入 {out.relative_to(ROOT)}")

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
