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
import http.client
import json
import math
import os
import re
import ssl
import threading
import time
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
    "llm_temperature": None,
    "llm_concurrency": 3,
    "lang_to": "zh",
}

_ENV_SETTINGS = {
    "llm_base_url": "PAPERBRIDGE_LLM_BASE_URL",
    "llm_api_key": "PAPERBRIDGE_LLM_API_KEY",
    "llm_model": "PAPERBRIDGE_LLM_MODEL",
    "llm_temperature": "PAPERBRIDGE_LLM_TEMPERATURE",
    "llm_concurrency": "PAPERBRIDGE_LLM_CONCURRENCY",
    "lang_to": "PAPERBRIDGE_LANG_TO",
}


def parse_temperature(value: Any) -> float | None:
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if not isinstance(value, bool) and isinstance(value, (int, float, str)):
        try:
            number = float(value)
        except (ValueError, OverflowError):
            pass
        else:
            if math.isfinite(number) and number >= 0:
                return number
    raise ValueError("Temperature 必须是非负有限数字，留空使用模型默认值")


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
        settings["llm_concurrency"] = DEFAULT_SETTINGS["llm_concurrency"]
    try:
        settings["llm_temperature"] = parse_temperature(settings["llm_temperature"])
    except ValueError:
        settings["llm_temperature"] = None
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
        cur["llm_concurrency"] = DEFAULT_SETTINGS["llm_concurrency"]
    cur["lang_to"] = str(cur.get("lang_to") or "zh").strip()[:8] or "zh"
    cur["llm_api_key"] = str(cur.get("llm_api_key") or "").strip()
    cur["llm_model"] = str(cur.get("llm_model") or "").strip()
    cur["llm_temperature"] = parse_temperature(cur["llm_temperature"])
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
    if not path and parts.hostname == "api.openai.com":
        path = "/v1"
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


def cache_key(pdf_hash: str, block_text: str, model: str, lang_to: str, provider: str = "", *,
              temperature: float | None = None) -> str:
    raw = f"{pdf_hash}|{model}|{lang_to}|{block_text}"
    if provider:
        raw += "|provider=" + provider.rstrip("/")
    if temperature is not None:
        raw += f"|temperature={temperature}"
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

def http_chat(base_url: str, api_key: str, model: str, messages: list[dict], timeout: int = 90, *,
              temperature: float | None = None, connection_attempts: int = 3) -> str:
    """OpenAI 兼容 /chat/completions 调用(标准库实现)。"""
    url = chat_url(base_url)
    payload = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        delay = 0.4 * 2 ** attempt
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode()).get("error", {})
                detail = str(error.get("message", "") if isinstance(error, dict) else error)
                parameter = str(error.get("param", "") if isinstance(error, dict) else "")
            except (ValueError, AttributeError):
                detail, parameter = "", ""
            if api_key:
                detail = detail.replace(api_key, "[redacted]")
            if exc.code in (400, 422) and "temperature" in payload and (
                    parameter == "temperature" or "temperature" in detail.lower()):
                del payload["temperature"]
                continue
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt + 1 >= connection_attempts:
                raise ValueError(f"LLM HTTP {exc.code}: {detail[:400] or exc.reason}") from exc
            header = (exc.headers or {}).get("Retry-After", "")
            if header.isdigit():
                delay = min(30, max(delay, int(header)))
        except (urllib.error.URLError, ConnectionError, TimeoutError, ssl.SSLError,
                http.client.IncompleteRead) as exc:
            reason = getattr(exc, "reason", exc)
            if (attempt + 1 >= connection_attempts or not isinstance(reason, (
                    ConnectionError, TimeoutError, ssl.SSLEOFError, http.client.IncompleteRead))):
                raise ValueError(f"翻译服务连接失败：{reason}。请检查接口地址、网络和代理。") from exc
        time.sleep(delay)
        attempt += 1
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM 返回格式不正确：缺少 choices[0].message.content") from exc
    if data["choices"][0].get("finish_reason") == "length":
        raise ValueError("模型输出达到长度限制，段落译文不完整")
    if isinstance(content, list):
        content = "".join(part["text"] for part in content if isinstance(part, dict)
                          and part.get("type") in ("text", "output_text") and isinstance(part.get("text"), str))
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
    *, exact: bool = False,
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
    temperature = parse_temperature(settings.get("llm_temperature"))
    concurrency = max(1, min(16, int(settings.get("llm_concurrency") or 3)))

    abstract = ""
    for b in blocks:
        if b.get("type") == "abstract":
            abstract = _block_text(b)
            if abstract:
                break

    wanted = set(block_ids)
    candidates = [b for b in blocks if b.get("id") in wanted] if exact else blocks
    units = [u for u in build_units(candidates) if any(bid in wanted for bid in u["block_ids"])]
    covered = {bid for u in units for bid in u["block_ids"]}

    results: dict[str, dict[str, Any]] = {}
    # 请求了但不在任何单元(不存在/不可翻译)
    for bid in wanted - covered:
        results[bid] = {"error": "块不存在或不可翻译", "blocks": [bid]}

    uncached: list[dict] = []
    for u in units:
        text = _unit_marked_text(u)
        key = cache_key(pdf_hash, text, model, lang_to, base_url, temperature=temperature)
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

    fatal = threading.Event()
    fatal_reason = []
    def call(messages):
        if fatal.is_set():
            raise ValueError(fatal_reason[0])
        try:
            return http_post(base_url, api_key, model, messages, temperature=temperature)
        except ValueError as exc:
            if re.search(r"LLM HTTP (401|403|404)\b", str(exc)):
                fatal_reason.append(str(exc))
                fatal.set()
            raise

    def work(seg):
        batch_units = []
        partial = {}
        for u in seg:
            keys = [cache_key(pdf_hash, source, model, lang_to, base_url, temperature=temperature)
                    for source in u["texts"]]
            u["_individual_keys"] = keys
            for j, key in enumerate(keys):
                hit = cache.get(key)
                if isinstance(hit, str) and hit.strip():
                    partial[u["block_ids"][j]] = {"text":hit, "cached":True}
            if not any(bid in partial for bid in u["block_ids"]):
                batch_units.append(u)
        parsed, batch_error = {}, ""
        if batch_units:
            try:
                raw = call([{"role":"user", "content":build_batch_prompt(batch_units, abstract, lang_to)}])
                parsed = parse_batch_output(raw, len(batch_units))
            except (OSError, ValueError, RuntimeError) as exc:
                batch_error = str(exc)
        for index, u in enumerate(batch_units):
            value = parsed.get(index, "")
            if not value:
                continue
            multi = len(u["texts"]) > 1
            if multi and not _BLOCK_MARK_RE.search(value):
                continue
            pieces = split_unit_output(value, len(u["texts"]))
            inputs = split_unit_output(u["_body"], len(u["texts"]))
            for j, output in pieces.items():
                markers = r"\[\[EQ\d+\]\]"
                if (output and sorted(re.findall(markers, output)) == sorted(re.findall(markers, inputs[j]))
                        and not re.search(r"\[\[(?:B\d+)\]\]|\[ID:\d+\]", output)):
                    partial[u["block_ids"][j]] = {"text":_restore_placeholders(output, u["_ph"]), "cached":False}
        for u in seg:
            for j, bid in enumerate(u["block_ids"]):
                if bid not in partial:
                    if batch_error and not re.search(
                            r"LLM HTTP 413|context[_ ]length|context window|maximum context|too many tokens|长度限制",
                            batch_error, re.I):
                        partial[bid] = {"error":batch_error}
                        continue
                    # A one-block request has no positional ambiguity even if the model ignores numbering.
                    body, equations = _placeholderize(u["texts"][j])
                    try:
                        answer = call([{"role":"system","content":f"Translate this excerpt into {lang_to}. Return only its translation. Keep [[EQn]] placeholders exactly."},
                                       {"role":"user","content":body}]).strip()
                        answer = re.sub(r"^\[ID:0\]\s*", "", answer)
                        if (not answer or re.search(r"\[\[B\d+\]\]|\[ID:\d+\]", answer) or
                                sorted(re.findall(r"\[\[EQ\d+\]\]", answer)) != sorted(equations)):
                            raise ValueError("模型返回的段落标记或公式占位符与原文不符")
                        partial[bid] = {"text":_restore_placeholders(answer, equations), "cached":False}
                    except (OSError, ValueError, RuntimeError) as exc:
                        partial[bid] = {"error":str(exc)}
            values = [partial[bid].get("text") for bid in u["block_ids"]]
            if all(values):
                cache.put(u["_key"], values if len(values) > 1 else values[0])
            else:
                for key, value in zip(u["_individual_keys"], values):
                    if value:
                        cache.put(key, value)
            for bid in u["block_ids"]:
                partial[bid].update(unit=u["first_id"], blocks=[bid])
        return partial

    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(segments) or 1))) as pool:
        for part in pool.map(work, segments):
            results.update(part)
    return {"results": results, "total_units": len(units)}
