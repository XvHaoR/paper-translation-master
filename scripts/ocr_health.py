"""Check actual OCR output, rather than only the model's HTTP liveness."""
from __future__ import annotations

import base64
from io import BytesIO
import json
import re
import urllib.request


def probe_vlm(base_url: str = "http://127.0.0.1:8185/v1", timeout: float = 20) -> str:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (720, 90), "white")
    ImageDraw.Draw(image).text((12, 16), "PaperLoom OCR ready 12345",
                              font=ImageFont.load_default(size=36), fill="black")
    stream = BytesIO()
    image.save(stream, format="PNG")
    with urllib.request.urlopen(base_url + "/models", timeout=timeout) as response:
        model = json.load(response)["data"][0]["id"]
    payload = {
        "model": model, "temperature": 1, "top_p": 0, "max_completion_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")}},
            {"type": "text", "text": "OCR:"},
        ]}],
    }
    request = urllib.request.Request(base_url + "/chat/completions", json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = json.load(response)["choices"][0]["message"]["content"]
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    if "paperloomocrready12345" not in normalized:
        raise RuntimeError("OCR 模型自检失败：接口可访问，但实际识别内容异常")
    return text
