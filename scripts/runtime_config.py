"""Read only the supported installer environment; keep process control private."""
from __future__ import annotations

import json
from pathlib import Path

ENVIRONMENT_KEYS = frozenset({
    "PAPERBRIDGE_WORKSPACE", "PADDLE_PDX_CACHE_HOME", "HF_HOME", "MODELSCOPE_CACHE",
    "PAPERBRIDGE_OCR_PAGE_BATCH_SIZE", "PAPERBRIDGE_OCR_USE_QUEUES",
    "PAPERBRIDGE_OCR_DEVICE",
    "PAPERBRIDGE_OCR_MODE", "PAPERBRIDGE_RAPIDOCR_MODELS",
})


def runtime_environment(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema") != 1 or not isinstance(data.get("environment", {}), dict):
        raise ValueError("OCR 安装信息格式无效，请重新运行安装器修复")
    return {key: value for key, value in data.get("environment", {}).items()
            if key in ENVIRONMENT_KEYS and isinstance(value, str) and "\0" not in value}
