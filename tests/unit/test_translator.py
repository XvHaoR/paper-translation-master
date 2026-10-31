"""LLM 翻译服务测试(app/services/translator.py)。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.translator import (
    TRANSLATABLE_TYPES,
    TranslateCache,
    build_batch_prompt,
    build_units,
    cache_key,
    chat_url,
    http_chat,
    load_settings,
    parse_batch_output,
    save_settings,
    split_unit_output,
    translate_batch,
    translate_enabled,
    _placeholderize,
    _restore_placeholders,
)


def _doc_dir(tmp_path: Path, split_paragraphs: bool = False) -> Path:
    """构造测试文档。split_paragraphs=True 时把一段话拆成两个相邻段落(测合并)。"""
    if split_paragraphs:
        blocks = [
            {"id": "b1", "type": "paragraph", "content": {"text": "The first half of a sentence that continues"}},
            {"id": "b2", "type": "paragraph", "content": {"text": "into the second half with $E=mc^2$ math."}},
            {"id": "b3", "type": "equation_block", "content": {"latex": r"\mathbf{X}=1", "text": None}},
            {"id": "b4", "type": "abstract", "content": {"text": "We propose a method."}},
            {"id": "b5", "type": "table", "content": {"html": "<table></table>", "text": None}},
            {"id": "b6", "type": "reference", "content": {"text": "[1] Author, Title."}},
        ]
    else:
        blocks = [
            {"id": "b1", "type": "paragraph", "content": {"text": "First sentence with $E=mc^2$ inline."}},
            {"id": "b2", "type": "paragraph", "content": {"text": "Second paragraph here."}},
            {"id": "b3", "type": "equation_block", "content": {"latex": r"\mathbf{X}=1", "text": None}},
            {"id": "b4", "type": "abstract", "content": {"text": "We propose a method."}},
            {"id": "b5", "type": "table", "content": {"html": "<table></table>", "text": None}},
            {"id": "b6", "type": "reference", "content": {"text": "[1] Author, Title."}},
        ]
    d = tmp_path / "doc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "document.json").write_text(
        json.dumps({"document": {"id": "abcd1234"}, "blocks": blocks}, ensure_ascii=False),
        encoding="utf-8",
    )
    return d


class _FakeLLM:
    """注入的假 LLM:解析 [ID:n] 列表并逐条返回译文。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_first = 0  # 前 N 次抛错(测重试)

    def __call__(self, base_url, api_key, model, messages, timeout=90):
        self.calls.append({"url": base_url, "key": api_key, "model": model, "messages": messages})
        if len(self.calls) <= self.fail_first:
            raise RuntimeError("mock down")
        content = messages[0]["content"]
        lines = [
            f"[ID:{m.group(1)}] 译:{m.group(2)}"
            for m in re.finditer(r"^\[ID:(\d+)\]\s*(.+)$", content, re.M)
        ]
        return "\n".join(lines)


def _settings(**kw) -> dict:
    base = {
        "llm_base_url": "https://mock/v1",
        "llm_api_key": "k",
        "llm_model": "m",
        "llm_concurrency": 2,
        "lang_to": "zh",
    }
    base.update(kw)
    return base


def _cache(tmp_path) -> TranslateCache:
    return TranslateCache(tmp_path / "cache.json")


# ---- 占位符 ----

def test_placeholderize_roundtrip():
    text = "energy $E=mc^2$ and $\\alpha$ end"
    body, ph = _placeholderize(text)
    assert "$" not in body
    assert body == "energy [[EQ0]] and [[EQ1]] end"
    assert _restore_placeholders(body, ph) == text


# ---- 缓存 ----

def test_cache_key_stable():
    k1 = cache_key("abc", "text", "model", "zh")
    k2 = cache_key("abc", "text", "model", "zh")
    k3 = cache_key("abc", "text2", "model", "zh")
    assert k1 == k2 and k1 != k3 and len(k1) == 40


def test_translate_cache_persist(tmp_path):
    cf = tmp_path / "cache.json"
    c = TranslateCache(cf)
    c.put("k1", "hello")
    c2 = TranslateCache(cf)  # 重新加载
    assert c2.get("k1") == "hello"


# ---- 语义单元合并 ----

def test_build_units_merges_split_paragraphs():
    data = json.loads((_doc_dir(Path("."), split_paragraphs=True) / "document.json").read_text(encoding="utf-8"))
    units = build_units(data["blocks"])
    # b1+b2 合并为一个单元;b4 abstract、b6 reference 各自成单元
    merged = [u for u in units if "b1" in u["block_ids"]][0]
    assert merged["block_ids"] == ["b1", "b2"]
    assert merged["first_id"] == "b1"
    ids = [u["first_id"] for u in units]
    assert ids == ["b1", "b4", "b6"]  # b3/b5 不可翻译


def test_build_units_page_and_type_break_merge():
    blocks = [
        {"id": "a", "type": "paragraph", "page": 1, "content": {"text": "p1 text"}},
        {"id": "b", "type": "paragraph", "page": 2, "content": {"text": "p2 text"}},  # 不同页 → 不合并
        {"id": "c", "type": "paragraph", "page": 2, "content": {"text": "p2 more"}},  # 同页相邻 → 合并
        {"id": "d", "type": "heading", "page": 2, "content": {"text": "H"}},  # 打断
        {"id": "e", "type": "paragraph", "page": 2, "content": {"text": "after heading"}},
    ]
    units = build_units(blocks)
    assert [u["first_id"] for u in units] == ["a", "b", "d", "e"]
    assert units[1]["block_ids"] == ["b", "c"]


def test_build_units_char_cap():
    blocks = [
        {"id": "x", "type": "paragraph", "page": 1, "content": {"text": "A" * 900}},
        {"id": "y", "type": "paragraph", "page": 1, "content": {"text": "B" * 900}},
    ]
    units = build_units(blocks)
    assert len(units) == 2  # 900+900 > 1200 → 断开


# ---- 批量 prompt 与解析 ----

def test_build_batch_prompt_numbered():
    units = [
        {"_body": "hello world"},
        {"_body": "second item"},
    ]
    p = build_batch_prompt(units, "ABSTRACT", "zh")
    assert "[ID:0] hello world" in p
    assert "[ID:1] second item" in p
    assert "【论文背景】ABSTRACT" in p
    assert "只输出译文" in p


def test_parse_batch_output_multiline():
    text = "开头垃圾行\n[ID:0] 第一行译文\n继续\n[ID:1] 第二条\n"
    out = parse_batch_output(text, 2)
    assert out[0] == "第一行译文\n继续"
    assert out[1] == "第二条"


def test_parse_batch_output_missing():
    out = parse_batch_output("[ID:0] only", 3)
    assert out == {0: "only"}


def test_split_unit_output():
    # 单块:原样
    assert split_unit_output("单段", 1) == {0: "单段"}
    # 模型忽略标记:整段归块 0
    assert split_unit_output("no markers", 3) == {0: "no markers"}
    # 正常分段,前导说明丢弃
    out = split_unit_output("前导说明[[B0]]译文A[[B1]]译文B", 2)
    assert out == {0: "译文A", 1: "译文B"}
    # 段数不足:只产出出现的段
    assert split_unit_output("[[B0]]译文A", 2) == {0: "译文A"}
    # 空文本
    assert split_unit_output("", 2) == {0: ""}


# ---- 批量翻译 ----

def test_translate_batch_ok_and_cached(tmp_path):
    fake = _FakeLLM()
    cache = _cache(tmp_path)
    res = translate_batch(_doc_dir(tmp_path), ["b1"], _settings(), cache, fake)
    r = res["results"]
    assert res["total_units"] >= 1
    assert r["b1"]["cached"] is False
    # b1+b2 合并成多块单元:译文切分后挂回各自块,公式占位符还原
    assert "First sentence with" in r["b1"]["text"]
    assert "$E=mc^2$" in r["b1"]["text"]
    assert "Second paragraph" in r["b2"]["text"]
    assert fake.calls

    # 第二次(缓存命中)
    fake2 = _FakeLLM()
    res2 = translate_batch(_doc_dir(tmp_path), ["b1"], _settings(), cache, fake2)
    assert res2["results"]["b1"]["cached"] is True
    assert fake2.calls == []  # 未再调 LLM


def test_translate_batch_merged_unit_per_block_results(tmp_path):
    """拆成两块的段落合并为一个单元 → 一次 LLM 调用,译文按块切分逐块返回。"""
    fake = _FakeLLM()
    cache = _cache(tmp_path)
    res = translate_batch(_doc_dir(tmp_path, split_paragraphs=True), ["b1", "b2"], _settings(), cache, fake)
    r = res["results"]
    assert len(fake.calls) == 1  # 合并后只调一次
    assert "b1" in r and "b2" in r  # 每块都有译文
    assert r["b1"]["text"] == "The first half of a sentence that continues"
    assert "into the second half" in r["b2"]["text"]
    assert "$E=mc^2$" in r["b2"]["text"]  # 公式还原
    assert r["b1"]["unit"] == "b1" and r["b2"]["unit"] == "b1"  # 同属一个单元

    # 缓存命中:不再调 LLM,仍逐块返回
    fake2 = _FakeLLM()
    res2 = translate_batch(_doc_dir(tmp_path, split_paragraphs=True), ["b1", "b2"], _settings(), cache, fake2)
    assert fake2.calls == []
    assert res2["results"]["b1"]["cached"] is True
    assert res2["results"]["b2"]["cached"] is True
    assert "into the second half" in res2["results"]["b2"]["text"]


def test_translate_batch_merged_unit_ignores_markers(tmp_path):
    """模型忽略 [[Bx]] 标记时:整段译文兜底挂首块,其余块报缺段。"""
    class _NoMarkers(_FakeLLM):
        def __call__(self, base_url, api_key, model, messages, timeout=90):
            return "[ID:0] 整段合并译文"

    res = translate_batch(_doc_dir(tmp_path, split_paragraphs=True), ["b1", "b2"], _settings(), _cache(tmp_path), _NoMarkers())
    r = res["results"]
    assert r["b1"]["text"] == "整段合并译文"
    assert "error" in r["b2"]


def test_translate_batch_placeholder_restored(tmp_path):
    class _Fixed(_FakeLLM):
        def __call__(self, base_url, api_key, model, messages, timeout=90):
            return "[ID:0] 译文保留 [[EQ0]] 原样"

    res = translate_batch(_doc_dir(tmp_path), ["b1"], _settings(), _cache(tmp_path), _Fixed())
    assert "$E=mc^2$" in res["results"]["b1"]["text"]
    assert "[[EQ0]]" not in res["results"]["b1"]["text"]


def test_translate_batch_retry_then_error(tmp_path):
    fake = _FakeLLM()
    fake.fail_first = 99  # 永远失败
    res = translate_batch(_doc_dir(tmp_path), ["b1"], _settings(), _cache(tmp_path), fake)
    assert "error" in res["results"]["b1"]
    assert fake.calls  # 至少重试过


def test_translate_batch_unknown_block(tmp_path):
    res = translate_batch(_doc_dir(tmp_path), ["nope"], _settings(), _cache(tmp_path), _FakeLLM())
    assert "error" in res["results"]["nope"]


# ---- 设置 ----

def test_load_settings_defaults_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBRIDGE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("PAPERBRIDGE_LLM_MODEL", "qwen")
    s = load_settings(tmp_path)
    assert s["llm_base_url"] == "http://localhost:11434/v1"
    assert s["llm_model"] == "qwen"
    assert s["llm_concurrency"] == 8


def test_save_settings_roundtrip_and_validate(tmp_path):
    saved = save_settings(tmp_path, {"llm_base_url": "https://api.deepseek.com/v1", "llm_model": "deepseek-chat"})
    assert saved["llm_base_url"] == "https://api.deepseek.com/v1"
    assert load_settings(tmp_path)["llm_model"] == "deepseek-chat"
    with pytest.raises(ValueError):
        save_settings(tmp_path, {"llm_base_url": "not-a-url"})


def test_translate_enabled():
    assert not translate_enabled(_settings(llm_base_url="", llm_model=""))
    assert translate_enabled(_settings())


@pytest.mark.parametrize("base, expected", [
    ("https://api.deepseek.com/anthropic", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/anthropic/v1/", "https://api.deepseek.com/v1/chat/completions"),
    ("http://localhost:11434/v1", "http://localhost:11434/v1/chat/completions"),
    ("https://example.com/v1/chat/completions", "https://example.com/v1/chat/completions"),
])
def test_chat_endpoint_protocol_and_full_url(base, expected):
    assert chat_url(base) == expected


def test_other_anthropic_endpoint_is_rejected():
    with pytest.raises(ValueError, match="Anthropic"):
        chat_url("https://example.com/anthropic")


def test_http_provider_error_is_useful_and_redacted(monkeypatch):
    import io
    import urllib.error

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.com", 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":{"message":"Invalid key secret"}}'))

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(ValueError, match="HTTP 401: Invalid key") as exc:
        http_chat("https://example.com", "secret", "model", [])
    assert "secret" not in str(exc.value)


def test_split_unit_uses_actual_marker_ids():
    assert split_unit_output("[[B1]]second[[B0]]first", 2) == {1: "second", 0: "first"}
    assert split_unit_output("[[B0]]first[[B8]]extra", 2) == {0: "first"}


def test_incomplete_output_is_not_cached(tmp_path):
    cache = _cache(tmp_path)
    doc = _doc_dir(tmp_path)
    translate_batch(doc, ["b1"], _settings(), cache, lambda *args: "[ID:0] partial")
    fake = _FakeLLM()
    results = translate_batch(doc, ["b1"], _settings(), cache, fake)["results"]
    assert fake.calls
    assert results["b2"]["text"]
