"""上下文感知 LLM 翻译服务(阅读页双语模式)。

设计要点(ZOTERO_INTEGRATION_DESIGN.md §6.4):
- 输入为结构化 IR 块(段落/标题/图注/脚注/参考文献…),公式与表格块不翻译
- 上下文窗口:前后各 2 块 + Abstract 术语锚点
- 行内 $...$ 公式提取为占位符保护,译文返回后还原
- 缓存:sha1(pdf_hash + 块文本 + 模型 + 语对),命中秒回
- 兼容任意 OpenAI 兼容接口(DeepSeek/Kimi/Qwen/GLM/Ollama)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

# 可翻译块类型(公式/表格/图片/代码不翻译)
TRANSLATABLE_TYPES = {
    "document_title", "authors", "abstract", "heading", "paragraph",
    "caption", "reference", "footnote", "list",
}

_INLINE_MATH_RE = re.compile(r"\$([^$\n]+)\$")

# ---- 设置 ----

DEFAULT_SETTINGS: dict[str, Any] = {
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    "llm_concurrency": 8,
    "lang_to": "zh",
}

_ENV_SETTINGS = {
    "llm_base_url": "PAPERBRIDGE_LLM_BASE_URL",
    "llm_api_key": "PAPERBRIDGE_LLM_API_KEY",
    "llm_model": "PAPERBRIDGE_LLM_MODEL",
    "llm_concurrency": "PAPERBRIDGE_LLM_CONCURRENCY",
    "lang_to": "PAPERBRIDGE_LANG_TO",
}


def load_settings(workspace: Path) -> dict[str, Any]:
    """运行时设置(workspace/settings.json)+ env 默认值。"""
    settings = dict(DEFAULT_SETTINGS)
    for key, env_name in _ENV_SETTINGS.items():
        val = os.environ.get(env_name)
        if val:
            settings[key] = val
    f = Path(workspace) / "settings.json"
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            settings.update({k: data[k] for k in settings if k in data})
        except Exception:
            pass
    try:
        settings["llm_concurrency"] = int(settings["llm_concurrency"])
    except (TypeError, ValueError):
        settings["llm_concurrency"] = 4
    return settings


def save_settings(workspace: Path, data: dict[str, Any]) -> dict[str, Any]:
    """校验并保存运行时设置;base_url 为空视为关闭翻译。"""
    cur = load_settings(workspace)
    for key in DEFAULT_SETTINGS:
        if key in data:
            cur[key] = data[key]
    cur["llm_base_url"] = str(cur.get("llm_base_url") or "").strip()
    if cur["llm_base_url"] and not cur["llm_base_url"].startswith(("http://", "https://")):
        raise ValueError("llm_base_url 必须是 http(s) 地址或留空")
    try:
        cur["llm_concurrency"] = max(1, min(16, int(cur["llm_concurrency"])))
    except (TypeError, ValueError):
        cur["llm_concurrency"] = 4
    cur["lang_to"] = str(cur.get("lang_to") or "zh").strip()[:8] or "zh"
    cur["llm_api_key"] = str(cur.get("llm_api_key") or "").strip()
    cur["llm_model"] = str(cur.get("llm_model") or "").strip()
    f = Path(workspace) / "settings.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    return cur


def translate_enabled(settings: dict[str, Any]) -> bool:
    return bool(settings.get("llm_base_url") and settings.get("llm_model"))


def chat_url(base_url: str) -> str:
    """Accept a base URL or full completion URL; repair DeepSeek's other protocol URL."""
    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip("/")
    if parts.hostname == "api.deepseek.com" and path in ("/anthropic", "/anthropic/v1"):
        path = "/v1"
    elif "/anthropic" in path or path.endswith("/messages"):
        raise ValueError("此地址使用 Anthropic 协议，请填写服务商的 OpenAI 兼容地址")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


# ---- 占位符保护 ----

def _placeholderize(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}

    def _repl(m: re.Match) -> str:
        key = f"[[EQ{len(placeholders)}]]"
        placeholders[key] = m.group(0)
        return key

    return _INLINE_MATH_RE.sub(_repl, text), placeholders


def _restore_placeholders(text: str, placeholders: dict[str, str]) -> str:
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


def cache_key(pdf_hash: str, block_text: str, model: str, lang_to: str, provider: str = "") -> str:
    raw = f"{pdf_hash}|{model}|{lang_to}|{block_text}"
    if provider:
        raw += "|provider=" + provider.rstrip("/")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---- 翻译缓存 ----

class TranslateCache:
    def __init__(self, cache_file: Path) -> None:
        self.cache_file = Path(cache_file)
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.cache_file.is_file():
                self._cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception:
            self._cache = {}

    def _save(self) -> None:
        try:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_file)
        except Exception:
            pass

    def get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def put(self, key: str, text: Any) -> None:
        with self._lock:
            self._cache[key] = text
            self._save()


# ---- LLM 调用 ----

def http_chat(base_url: str, api_key: str, model: str, messages: list[dict], timeout: int = 90) -> str:
    """OpenAI 兼容 /chat/completions 调用(标准库实现)。"""
    url = chat_url(base_url)
    payload = json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error", {})
            detail = error.get("message", "") if isinstance(error, dict) else str(error)
        except (ValueError, UnicodeError):
            pass
        # Never echo a provider's copy of the credential into the reader.
        if api_key:
            detail = detail.replace(api_key, "[redacted]")
        raise ValueError(f"LLM HTTP {exc.code}: {detail[:400] or exc.reason}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM 返回格式不正确：缺少 choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM 返回空译文，请检查模型是否支持文本对话")
    return content.strip()


# ---- 批量翻译(语义单元合并 + 分段批量请求) ----

# 可合并类型:同一页、顺序相邻的段落合并为一个翻译单元(公式/表格/标题打断)
MERGEABLE_TYPES = {"paragraph"}
MAX_UNIT_CHARS = 1200
_SEGMENT_MAX_CHARS = 1800  # 每段(一次请求)累计字符上限


def _block_text(b: dict) -> str:
    c = b.get("content") or {}
    if isinstance(c, dict):
        return (c.get("text") or "\n".join(c.get("items") or [])).strip()
    return str(c or "").strip()


def build_units(blocks: list[dict]) -> list[dict]:
    """把全量有序块合并为翻译单元。

    规则:
    - 同一页、顺序相邻的 paragraph 块合并(中间出现任何其他块即打断,
      公式/表格/标题天然成为分隔);
    - 其余可翻译类型各自成单元;
    - 单元累计字符超过 MAX_UNIT_CHARS 时截断开新单元。
    返回 [{"first_id", "block_ids", "texts", "page"}]
    """
    units: list[dict] = []
    cur: dict | None = None
    for b in blocks:
        t = b.get("type")
        text = _block_text(b)
        if t not in TRANSLATABLE_TYPES:
            cur = None  # 不可翻译块打断合并
            continue
        if not text:
            continue
        if (
            cur is not None
            and cur.get("mergeable")
            and t in MERGEABLE_TYPES
            and b.get("page") == cur["page"]
            and sum(len(x) for x in cur["texts"]) + len(text) <= MAX_UNIT_CHARS
        ):
            cur["block_ids"].append(b.get("id"))
            cur["texts"].append(text)
            continue
        cur = {
            "first_id": b.get("id"),
            "block_ids": [b.get("id")],
            "texts": [text],
            "page": b.get("page"),
            "mergeable": t in MERGEABLE_TYPES,
        }
        units.append(cur)
    return units


def _unit_marked_text(u: dict) -> str:
    """单元文本;多块单元在块间插入 [[Bx]] 标记,供模型分段返回译文。"""
    texts = u["texts"]
    if len(texts) == 1:
        return texts[0]
    return "".join(f"[[B{i}]]{t}" for i, t in enumerate(texts))


def build_batch_prompt(units: list[dict], abstract: str, lang_to: str) -> str:
    """一次请求翻译多条:编号列表 + 共享 Abstract 术语锚点。"""
    lines: list[str] = []
    if abstract:
        lines.append(f"【论文背景】{abstract[:800]}")
    lines.append(f"下面有 {len(units)} 条待翻译文本,每条以 [ID:n] 开头:")
    for i, u in enumerate(units):
        lines.append(f"[ID:{i}] {u['_body']}")
    lines.append(
        f"请把每条翻译成{lang_to}。只输出译文,一行一条,保持 [ID:n] 前缀,格式:\n"
        "[ID:0] 译文0\n[ID:1] 译文1\n"
        "含 [[Bx]] 标记的多段文本按段翻译:译文之间同样用 [[Bx]] 分隔,"
        "顺序与段数保持一致;\n"
        "不要输出任何其他内容;公式与专有名词保留原文;"
        "术语与【论文背景】保持一致;保留 [[EQn]] 占位符原样不动。"
    )
    return "\n".join(lines)


def parse_batch_output(text: str, n: int) -> dict[int, str]:
    """解析模型返回的 [ID:n] 译文列表(按前缀切分,容忍行内换行)。

    模型回显 prompt 中的示例行(如 "[ID:0] 译文0")会产生重复编号:
    保留首次出现的译文,后续重复编号忽略。
    """
    out: dict[int, str] = {}
    matches = list(re.finditer(r"\[ID:(\d+)\]\s*", text or ""))
    for i, m in enumerate(matches):
        idx = int(m.group(1))
        if 0 <= idx < n and idx not in out:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            out[idx] = text[m.end():end].strip()
    return out


_BLOCK_MARK_RE = re.compile(r"\[\[B(\d+)\]\]")


def split_unit_output(text: str, n_blocks: int) -> dict[int, str]:
    """把多段单元译文按 [[Bx]] 标记切分回各块(按出现顺序对应)。

    - 无标记:整段归块 0(模型忽略标记的兜底);
    - 首个标记之前的前导文字丢弃(模型可能加说明);
    - 段数不足时缺失的块不产出。
    """
    if n_blocks <= 1 or not text:
        return {0: (text or "").strip()}
    matches = list(_BLOCK_MARK_RE.finditer(text))
    if not matches:
        return {0: text.strip()}
    out: dict[int, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg = text[m.end():end].strip()
        idx = int(m.group(1))
        if seg and 0 <= idx < n_blocks and idx not in out:
            out[idx] = seg
    return out


def translate_batch(
    doc_dir: Path,
    block_ids: list[str],
    settings: dict[str, Any],
    cache: TranslateCache,
    http_post: Callable[..., str] = http_chat,
) -> dict[str, Any]:
    """翻译指定块:先合并为语义单元,再分段批量请求 LLM。

    返回 {"results": {block_id: {"text", "cached", "unit", "blocks"} | {"error", "unit", "blocks"}},
          "total_units": N}
    - 合并单元的译文按 [[Bx]] 切分,逐块挂回对应 block_id(unit 指向单元首块);
    - http_post 可注入用于测试。
    """
    doc_file = Path(doc_dir) / "document.json"
    if not doc_file.is_file():
        return {"results": {bid: {"error": "文档数据不存在", "blocks": [bid]} for bid in block_ids}, "total_units": 0}
    data = json.loads(doc_file.read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    doc_meta = data.get("document", {})
    pdf_hash = doc_meta.get("id") or Path(doc_dir).name

    model = settings.get("llm_model") or ""
    lang_to = settings.get("lang_to") or "zh"
    base_url = settings.get("llm_base_url") or ""
    api_key = settings.get("llm_api_key") or ""
    concurrency = max(1, min(16, int(settings.get("llm_concurrency") or 8)))

    abstract = ""
    for b in blocks:
        if b.get("type") == "abstract":
            abstract = _block_text(b)
            if abstract:
                break

    wanted = set(block_ids)
    units = [u for u in build_units(blocks) if any(bid in wanted for bid in u["block_ids"])]
    covered = {bid for u in units for bid in u["block_ids"]}

    results: dict[str, dict[str, Any]] = {}
    # 请求了但不在任何单元(不存在/不可翻译)
    for bid in wanted - covered:
        results[bid] = {"error": "块不存在或不可翻译", "blocks": [bid]}

    uncached: list[dict] = []
    for u in units:
        text = _unit_marked_text(u)
        key = cache_key(pdf_hash, text, model, lang_to, base_url)
        hit = cache.get(key)
        first = u["first_id"]
        if hit is not None and (isinstance(hit, str) and len(u["block_ids"]) == 1 and hit.strip()
                                or isinstance(hit, list) and len(hit) == len(u["block_ids"])
                                and all(isinstance(p, str) and p.strip() for p in hit)):
            parts = hit if isinstance(hit, list) else [hit]
            for j, bid in enumerate(u["block_ids"]):
                part = parts[j] if j < len(parts) else ""
                if part:
                    results[bid] = {"text": part, "cached": True, "unit": first, "blocks": [bid]}
                else:
                    results[bid] = {"error": "缺少该段译文(缓存)", "unit": first, "blocks": [bid]}
            continue
        u["_key"] = key
        u["_text"] = text
        u["_body"], u["_ph"] = _placeholderize(text)
        uncached.append(u)

    # 按字符数分组为段
    segments: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0
    for u in uncached:
        if cur and cur_chars + len(u["_text"]) > _SEGMENT_MAX_CHARS:
            segments.append(cur)
            cur, cur_chars = [], 0
        cur.append(u)
        cur_chars += len(u["_text"])
    if cur:
        segments.append(cur)

    def work(seg: list[dict]) -> tuple[list[dict], dict[int, str] | None, str]:
        prompt = build_batch_prompt(seg, abstract, lang_to)
        messages = [{"role": "user", "content": prompt}]
        last_err = ""
        for attempt in range(3):
            try:
                out = http_post(base_url, api_key, model, messages)
                parsed = parse_batch_output(out, len(seg))
                missing = [i for i in range(len(seg)) if i not in parsed or not parsed[i]]
                if missing:
                    raise ValueError(f"缺少 [ID:{missing}] 的译文")
                return seg, parsed, ""
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                if attempt < 2:
                    import time

                    time.sleep(1 * (2 ** attempt))
        return seg, None, last_err

    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(segments) or 1))) as pool:
        for seg, parsed, err in pool.map(work, segments):
            if parsed is None:
                for u in seg:
                    for bid in u["block_ids"]:
                        results[bid] = {"error": err or "翻译失败", "unit": u["first_id"], "blocks": [bid]}
                continue
            for i, u in enumerate(seg):
                out = parsed.get(i)
                if out is None:
                    for bid in u["block_ids"]:
                        results[bid] = {"error": "缺少译文", "unit": u["first_id"], "blocks": [bid]}
                    continue
                parts = split_unit_output(out, len(u["block_ids"]))
                translated = {j: _restore_placeholders(p, u["_ph"]) for j, p in parts.items()}
                # 多块单元缓存整组译文(list),单块保持字符串(兼容旧缓存)
                if len(translated) != len(u["block_ids"]):
                    pass  # Partial output must be retried instead of poisoning the cache.
                elif len(u["block_ids"]) == 1:
                    cache.put(u["_key"], translated.get(0, ""))
                else:
                    cache.put(u["_key"], [translated.get(j, "") for j in range(len(u["block_ids"]))])
                for j, bid in enumerate(u["block_ids"]):
                    t = translated.get(j)
                    if t:
                        results[bid] = {"text": t, "cached": False, "unit": u["first_id"], "blocks": [bid]}
                    else:
                        results[bid] = {"error": "缺少该段译文", "unit": u["first_id"], "blocks": [bid]}

    return {"results": results, "total_units": len(units)}
