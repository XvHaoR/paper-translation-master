"""全局配置：路径、端口、渲染与 OCR 选项。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WORKSPACE = Path(os.environ.get("PAPERBRIDGE_WORKSPACE", "workspace"))
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass
class Settings:
    """应用配置。可通过环境变量 PAPERBRIDGE_* 覆盖。"""

    workspace: Path = DEFAULT_WORKSPACE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # 渲染
    render_dpi: int = 220
    render_dpi_high: int = 300
    preview_dpi: int = 72
    max_page_pixels: int = 40_000_000  # 超大页面限制
    # 任务
    max_pages_per_job: int = 500
    keep_raw_output: bool = True
    # 缓存
    use_cache: bool = True
    max_cache_documents: int = 100
    # OCR
    ocr_device: str = "auto"  # auto | cpu | gpu
    ocr_mode: str = "official"  # official | auto | native | full | hybrid
    ocr_timeout_seconds: float = 600.0
    # 阅读顺序（READING_ORDER_TECHNICAL_DESIGN.md §13）
    reading_order: str = "model-first"  # model-first | legacy-geometric
    # 解析器缓存版本：布局合并/阅读顺序等算法变更时递增，使旧缓存自动失效重解析
    parser_cache_version: str = "official-direct-rich-v3"
    # 浏览器扩展
    allow_extension_ids: list[str] = field(default_factory=list)
    pair_code_ttl_seconds: int = 600

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        for sub in ("documents", "tmp"):
            (self.workspace / sub).mkdir(parents=True, exist_ok=True)

    @property
    def documents_dir(self) -> Path:
        return self.workspace / "documents"

    @property
    def tmp_dir(self) -> Path:
        return self.workspace / "tmp"

    @classmethod
    def from_env(cls) -> "Settings":
        kwargs: dict = {}
        if v := os.environ.get("PAPERBRIDGE_WORKSPACE"):
            kwargs["workspace"] = Path(v)
        if v := os.environ.get("PAPERBRIDGE_PORT"):
            kwargs["port"] = int(v)
        if v := os.environ.get("PAPERBRIDGE_OCR_MODE"):
            kwargs["ocr_mode"] = v
        if v := os.environ.get("PAPERBRIDGE_OCR_DEVICE"):
            kwargs["ocr_device"] = v
        if v := os.environ.get("PAPERLOOM_READING_ORDER"):
            kwargs["reading_order"] = v
        return cls(**kwargs)


def get_settings() -> Settings:
    """进程级单例，测试中可重置。"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None


_settings: Settings | None = None
