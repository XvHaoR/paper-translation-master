"""截断标题重识别修复。

PaddleOCR-VL 的 PP-DocLayoutV3 对超大字号粗体章节标题（如 "IV.
EXPERIMENTS"）的检测框只覆盖标题左侧一小部分，VLM 只能识别出截断文本
（"IV. E"）。本模块检测这种截断模式后，把标题框按所在栏扩展为整行，
裁剪页面预览图重新送 VLM OCR，用完整标题替换截断文本。

失败不影响主流程：任何异常都保留原文并记录 warning。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# 截断标题模式:罗马数字+点+单字母("II. R"/"IV. E"),或孤立短大写词("R")
TRUNCATED_PATTERNS = [
    re.compile(r"^[IVXL]+\.\s*[A-Z]\.?$"),
    re.compile(r"^[A-Z]{1,2}\.?$"),
]
MAX_TRUNCATED_LEN = 6

_OCR_PROMPT = "OCR:"
_DEFAULT_MODEL = "PaddlePaddle/PaddleOCR-VL"


def find_truncated_headings(blocks: list[dict]) -> list[dict]:
    """返回疑似被截断的 heading 块（含 bbox）。"""
    out: list[dict] = []
    for b in blocks:
        if b.get("type") != "heading":
            continue
        text = ((b.get("content") or {}).get("text") or "").strip()
        if not text or len(text) > MAX_TRUNCATED_LEN:
            continue
        if not any(p.match(text) for p in TRUNCATED_PATTERNS):
            continue
        if not b.get("bbox"):
            continue
        out.append(b)
    return out


def _column_ranges(blocks: list[dict]) -> tuple[tuple[float, float], tuple[float, float]]:
    """按 bbox 中心 x 划分左右栏,返回各栏 [x1, x2] 归一化范围。"""
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for b in blocks:
        box = b.get("bbox")
        if not box or len(box) < 4:
            continue
        x1, _y1, x2, _y2 = box[:4]
        if x2 <= x1:
            continue
        cx = (x1 + x2) / 2
        (left if cx < 0.5 else right).append((x1, x2))
    fallback = (0.05, 0.49), (0.51, 0.95)
    result: list[tuple[float, float]] = []
    for col in (left, right):
        if col:
            result.append((min(x1 for x1, _ in col), max(x2 for _, x2 in col)))
        else:
            result.append(fallback[len(result)])
    return tuple(result)  # type: ignore[return-value]


def _ocr_crop(
    base_url: str,
    model: str,
    api_key: str,
    png_bytes: bytes,
    timeout: int = 60,
) -> str:
    """把裁剪区域送 VLM OCR("OCR:" prompt),返回识别文本。"""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": _OCR_PROMPT},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 256,
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"] or "").strip()


def _clean_ocr_text(raw: str) -> str:
    """清理 VLM 返回的标题文本。"""
    text = re.sub(r"\s+", " ", raw or "").strip()
    # 去掉常见的 OCR 前后缀噪音
    text = re.sub(r"^(ocr|OCR)[:\s]*", "", text).strip()
    return text.strip(" .,:;-_")


def repair_headings(
    blocks: list[dict],
    preview_dir: Path,
    *,
    server_url: str,
    model: str = _DEFAULT_MODEL,
    api_key: str = "",
    progress=None,
) -> int:
    """修复截断标题,原地更新 blocks 中 heading 的 content.text。

    返回修复数量;失败块保持原文。
    """
    truncated = find_truncated_headings(blocks)
    if not truncated:
        return 0
    if not server_url:
        logger.info("未配置 VLM 服务地址,跳过标题修复(%d 个截断标题)", len(truncated))
        return 0

    # 每页:栏范围 + 页面图尺寸
    pages_blocks: dict[int, list[dict]] = {}
    for b in blocks:
        if b.get("page") is not None:
            pages_blocks.setdefault(b.get("page"), []).append(b)

    fixed = 0
    for b in truncated:
        page = b.get("page")
        box = b.get("bbox")
        try:
            col_ranges = _column_ranges(pages_blocks.get(page, []))
            x1, y1, x2, y2 = (float(v) for v in box[:4])
            cx = (x1 + x2) / 2
            col_x1, col_x2 = col_ranges[0 if cx < 0.5 else 1]
            # 标题行通常独占一行:扩展为整栏宽度,保持行高
            pad = 0.004
            ex_box = (
                max(0.0, col_x1 - pad),
                max(0.0, y1 - pad),
                min(1.0, col_x2 + pad),
                min(1.0, y2 + pad),
            )
            preview = preview_dir / f"preview-{page:03d}.png"
            if not preview.is_file():
                continue
            with Image.open(preview) as img:
                w, h = img.size
                crop = img.crop(
                    (
                        int(ex_box[0] * w),
                        int(ex_box[1] * h),
                        int(ex_box[2] * w),
                        int(ex_box[3] * h),
                    )
                )
            buf = __import__("io").BytesIO()
            crop.save(buf, format="PNG")
            raw = _ocr_crop(server_url, model, api_key, buf.getvalue())
            new_text = _clean_ocr_text(raw)
            old_text = ((b.get("content") or {}).get("text") or "").strip()
            # 只接受明显更完整的识别结果
            if len(new_text) > len(old_text) and not re.fullmatch(r"[IVXL]+\.", new_text):
                b.setdefault("content", {})["text"] = new_text
                fixed += 1
                logger.info("标题修复: %r -> %r (p.%s)", old_text, new_text, page)
                if progress:
                    progress(f"标题修复: {old_text} -> {new_text}")
        except Exception as e:  # noqa: BLE001
            logger.warning("标题修复失败(%s p.%s): %s", b.get("id"), page, e)
    return fixed
