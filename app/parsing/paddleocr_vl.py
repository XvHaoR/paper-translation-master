"""PaddleOCR-VL-1.6 完整流水线后端封装。

- 只封装“版面分析 + VLM 识别”完整流水线，不调用裸 0.9B 模型（方案 §5.2）。
- 惰性加载：未安装 paddleocr 时给出可理解的错误，不阻塞原生路径。
- 结果统一为 :class:`PageOcrResult`（版面框 + 识别文本），原始 JSON 供调试保存。

识别层保持窄接口 :class:`DocumentRecognizer`，MVP 只实现 PaddleOCR-VL
一个后端，但未来可平级替换（方案 §5.2）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from app.parsing.page_strategy import LayoutBox, map_label_to_block_type, normalize_bbox

logger = logging.getLogger(__name__)


def _env_bool(name: str, *, default: bool) -> bool:
    """读取布尔环境变量；未设置时保持调用方给出的默认值。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_merge_mode(raw: str) -> str | dict[int, str] | None:
    """解析 PAPERBRIDGE_OCR_LAYOUT_MERGE_MODE。

    支持 "large"/"small"/"union" 或 JSON 按类别配置,如 '{"14": "large"}'。
    PP-DocLayoutV3 中 image 类别序号为 14;返回 None 表示不启用。
    """
    s = (raw or "").strip()
    if not s:
        return None
    if s in ("large", "small", "union"):
        return s
    if s.startswith("{"):
        try:
            return {int(k): v for k, v in json.loads(s).items()}
        except (ValueError, TypeError):
            return None
    return None


@dataclass
class PageOcrResult:
    """一页的 OCR 结果。"""

    page: int
    boxes: list[LayoutBox] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # 后端原始输出（调试）
    duration_ms: int = 0
    error: str = ""


@dataclass
class OfficialPageResult:
    """PaddleOCR 官方 ``save_to_*`` 产物的逐页索引。

    这里不再把官方 Markdown 拆回自定义中间格式；Markdown、JSON 和 ``imgs``
    目录均由 PaddleOCR 原样写入，由上层仅负责将其放入文档资产目录。
    """

    page: int
    raw: dict[str, Any]
    markdown: str
    output_dir: Path
    duration_ms: int = 0


class DocumentRecognizer(Protocol):
    """识别层窄接口（方案 §11.2）。"""

    def recognize_page(self, image: bytes | str | Path, page_no: int) -> PageOcrResult:
        ...

    def is_available(self) -> bool:
        ...


# ---- 模型层缓存（缓存分离，L1）------------------------------------------

# 序列化时剔除的调试大字段（回放不需要图像数据，只保留版面框/识别文本）。
# PaddleOCR-VL 1.6 的 ``doc_preprocessor_res.output_img`` 是完整 RGB 数组，
# 220 DPI 页面展开为 JSON 后可达 70 MB；必须在任何 ``tolist`` 前跳过。
_JSON_SKIP_KEYS = {"input_img", "output_img", "imgs_in_doc"}


def json_safe(obj: Any) -> Any:
    """把模型输出递归转为可 JSON 序列化的纯 Python 结构。

    - 剔除 ``input_img``/``output_img``/``imgs_in_doc`` 等图像调试字段；
    - numpy 标量/数组转为 float/int/str（paddle 结果常含 numpy 类型）。
    """
    if isinstance(obj, dict):
        return {
            str(k): json_safe(v)
            for k, v in obj.items()
            if str(k) not in _JSON_SKIP_KEYS
        }
    if isinstance(obj, (list, tuple)):
        return [json_safe(x) for x in obj]
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return json_safe(obj.tolist())
    except ImportError:  # pragma: no cover - numpy 与 paddle 同装
        pass
    return str(obj)


def _result_value(obj: Any, key: str, default: Any = None) -> Any:
    """从 PaddleX Result、dict 或普通对象安全读取字段。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError, AttributeError):
        return getattr(obj, key, default)


def _compact_model_result(item: Any) -> dict[str, Any]:
    """只提取后处理和缓存回放必需的 PaddleOCR-VL 字段。

    不访问 ``item.json``，因为该属性会把页面图像转换成巨大的嵌套列表，
    即使随后再由 :func:`json_safe` 删除，也已经付出了转换和内存成本。
    """
    width = _result_value(item, "width")
    height = _result_value(item, "height")
    layout = _result_value(item, "layout_det_res", {}) or {}
    raw_boxes = _result_value(layout, "boxes", []) or []
    compact_boxes: list[dict[str, Any]] = []
    for box in raw_boxes:
        coordinate = _result_value(box, "coordinate", []) or []
        raw_order = _result_value(box, "order")
        compact_boxes.append(
            {
                "label": str(_result_value(box, "label", "paragraph") or "paragraph"),
                "coordinate": json_safe(coordinate),
                "score": float(_result_value(box, "score", 0.5) or 0.5),
                "order": int(raw_order) if raw_order is not None else None,
            }
        )

    parsing_items = _result_value(item, "parsing_res_list", []) or []
    compact_parsing: list[Any] = []
    for block in parsing_items:
        if isinstance(block, str):
            compact_parsing.append(block)
            continue
        label = _result_value(block, "label")
        bbox = _result_value(block, "bbox")
        content = _result_value(block, "content")
        if label is not None or bbox is not None or content is not None:
            compact_parsing.append(
                {
                    "label": str(label or ""),
                    "bbox": json_safe(bbox or []),
                    "content": str(content or ""),
                }
            )

    result: dict[str, Any] = {
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "layout_det_res": {"boxes": compact_boxes},
        "parsing_res_list": compact_parsing,
    }
    return result


def _parse_markdown_text(raw_text: str) -> tuple[str, str]:
    """从元素识别文本中分离 LaTeX（``$$...$$`` / ``$...$``）。

    返回 (文本, latex)。PaddleOCR-VL 对公式元素返回 LaTeX，其余返回 Markdown。
    """
    t = (raw_text or "").strip()
    if not t:
        return "", ""
    if t.startswith("$$") and t.endswith("$$"):
        return "", t[2:-2].strip()
    if t.startswith("$") and t.endswith("$") and len(t) > 2:
        return "", t[1:-1].strip()
    return t, ""


def _parse_parsing_block(raw: str) -> dict | None:
    """解析 PaddleOCR-VL 的 parsing_res_list 文本块。

    格式：::

        #################
        label:\tdoc_title
        bbox:\t[386, 130, 1818, 271]
        content:\t...
        #################
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    result: dict = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("label:"):
            result["label"] = ln.split(":", 1)[1].strip()
        elif ln.startswith("bbox:"):
            try:
                part = ln.split(":", 1)[1].strip().strip("[]")
                result["bbox"] = [float(x) for x in part.split(",") if x.strip()]
            except ValueError:
                result["bbox"] = []
        elif ln.startswith("content:"):
            content = ln.split(":", 1)[1].strip()
            i += 1
            # 多行内容直到下一个 "#####" 或文件结束
            while i < len(lines) and not lines[i].startswith("#####"):
                content += "\n" + lines[i]
                i += 1
            result["content"] = content
            continue
        i += 1
    return result if result else None


def _extract_boxes(json_res: dict) -> list[dict]:
    """从 PaddleX JSON 中提取版面框列表。

    PaddleOCR-VL-1.6 结构：
    - ``layout_det_res.boxes``：label/coordinate/score/order
    - ``parsing_res_list``：label/bbox/content（识别文本）
    """
    boxes: list[dict] = []
    ld = json_res.get("layout_det_res") or {}
    if isinstance(ld, dict):
        for b in ld.get("boxes") or []:
            coord = b.get("coordinate") or []
            raw_order = b.get("order")
            boxes.append(
                {
                    "label": str(b.get("label") or "paragraph"),
                    "bbox": list(coord) if coord else [],
                    "score": float(b.get("score") or 0.5),
                    "order": int(raw_order) if raw_order is not None else None,
                }
            )

    # 从 parsing_res_list 对齐识别文本（按 label + bbox 中心最近邻）
    # 注意：inline_formula 等区域模型不输出独立识别文本（行内公式以内嵌
    # $...$ 形式出现在 text 块中），label 不匹配时保持 text 为空。
    # 元素可能是 PaddleOCRVLBlock 对象（.label/.bbox/.content）或字符串文本块
    parsing_blocks: list[dict] = []
    for item in json_res.get("parsing_res_list") or []:
        if isinstance(item, str):
            parsed = _parse_parsing_block(item)
            if parsed:
                parsing_blocks.append(parsed)
        elif isinstance(item, dict):
            parsing_blocks.append(item)
        elif hasattr(item, "label"):
            parsing_blocks.append(
                {
                    "label": str(item.label or ""),
                    "bbox": list(getattr(item, "bbox", None) or []),
                    "content": str(getattr(item, "content", None) or ""),
                }
            )

    for box in boxes:
        if not box.get("bbox"):
            continue
        # 只与 label 相同的识别块对齐，避免公式框错配正文文本
        label = box.get("label") or ""
        candidates = [
            pb for pb in parsing_blocks if (pb.get("label") or "").strip() == label.strip()
        ]
        if not candidates:
            continue
        bx = box["bbox"]
        bcx, bcy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        best, best_d = None, float("inf")
        for pb in candidates:
            pbbox = pb.get("bbox") or []
            if len(pbbox) < 4:
                continue
            d = abs((pbbox[0] + pbbox[2]) / 2 - bcx) + abs((pbbox[1] + pbbox[3]) / 2 - bcy)
            if d < best_d:
                best, best_d = pb, d
        if best is not None:
            box["text"] = str(best.get("content") or "").strip()
    return boxes


def _image_dims(json_res: dict) -> tuple[float, float] | None:
    """从后端输出读取页面像素尺寸，用于归一化。"""
    w, h = json_res.get("width"), json_res.get("height")
    if w and h:
        return float(w), float(h)
    info = json_res.get("doc_preprocessor_res")
    if isinstance(info, dict):
        img = info.get("input_img") or {}
        if isinstance(img, dict) and img.get("width") and img.get("height"):
            return float(img["width"]), float(img["height"])
    return None


class PaddleOcrVlParser:
    """PaddleOCR-VL-1.6 完整流水线后端（惰性加载）。"""

    PIPELINE_VERSION = "v1.6"
    supports_document_progress = True

    def __init__(
        self,
        device: str = "auto",
        model_dir: str | None = None,
        *,
        vl_backend: str | None = None,
        vl_server_url: str | None = None,
        vl_max_concurrency: int | None = None,
        use_queues: bool | None = None,
        enable_hpi: bool | None = None,
        use_tensorrt: bool | None = None,
        precision: str | None = None,
        layout_merge_bboxes_mode: str | dict[int, str] | None = None,
    ) -> None:
        """创建官方 PaddleOCR-VL 客户端。

        默认保持本地 PaddlePaddle 推理。设置 ``vl_backend`` 为 ``vllm``、
        ``sglang`` 或官方 FastDeploy 的 ``fastdeploy-server``，并提供服务
        地址后，PaddleX 会按官方方式将 VLM 请求转发给对应服务；布局、结果
        格式和本项目后处理不变。

        未显式设置任何参数时严格等价于官方示例 ``PaddleOCRVL()``。可选
        ``layout_merge_bboxes_mode`` 仅保留给兼容旧流程，不再改变默认模型
        配置。

        同名 ``PAPERBRIDGE_OCR_*`` 环境变量可用于服务进程配置，避免把
        部署地址硬编码进文档缓存或源码。
        """
        self.device = device
        self.page_batch_size = int(os.environ.get("PAPERBRIDGE_OCR_PAGE_BATCH_SIZE", "0"))
        if self.page_batch_size < 0:
            raise ValueError("OCR page batch size must not be negative")
        self.model_dir = model_dir
        self.vl_backend = (
            vl_backend
            if vl_backend is not None
            else os.environ.get("PAPERBRIDGE_OCR_VL_BACKEND", "")
        ).strip().lower()
        self.vl_server_url = (
            vl_server_url
            if vl_server_url is not None
            else os.environ.get("PAPERBRIDGE_OCR_VL_SERVER_URL", "")
        ).strip()
        raw_concurrency = (
            str(vl_max_concurrency)
            if vl_max_concurrency is not None
            else os.environ.get("PAPERBRIDGE_OCR_VL_MAX_CONCURRENCY", "")
        )
        self.vl_max_concurrency = int(raw_concurrency) if raw_concurrency else None
        self.use_queues = (
            use_queues
            if use_queues is not None
            else _env_bool(
                "PAPERBRIDGE_OCR_USE_QUEUES",
                default=bool(self.vl_backend),
            )
        )
        self.enable_hpi = (
            enable_hpi
            if enable_hpi is not None
            else _env_bool("PAPERBRIDGE_OCR_ENABLE_HPI", default=False)
        )
        self.use_tensorrt = (
            use_tensorrt
            if use_tensorrt is not None
            else _env_bool("PAPERBRIDGE_OCR_USE_TENSORRT", default=False)
        )
        self.precision = (
            precision
            if precision is not None
            else os.environ.get("PAPERBRIDGE_OCR_PRECISION", "")
        ).strip().lower()
        # 官方默认不传此参数；仅在用户明确设置环境变量时才覆盖。
        self.layout_merge_bboxes_mode = (
            layout_merge_bboxes_mode
            if layout_merge_bboxes_mode is not None
            else _parse_merge_mode(os.environ.get("PAPERBRIDGE_OCR_LAYOUT_MERGE_MODE", ""))
        )
        if self.vl_backend not in ("", "vllm", "sglang", "fastdeploy-server"):
            raise ValueError(
                "vl_backend 仅支持 vllm、sglang、fastdeploy-server 或空值（本地推理）"
            )
        if self.vl_backend and not self.vl_server_url:
            raise ValueError("使用远程 VLM 服务时必须设置 vl_server_url")
        if self.vl_max_concurrency is not None and self.vl_max_concurrency < 1:
            raise ValueError("vl_max_concurrency 必须大于 0")
        if self.precision and self.precision not in ("fp32", "fp16"):
            raise ValueError("precision 仅支持 fp32 或 fp16")
        self._ocr: Any = None
        self._load_error: str = ""
        self._load_attempted = False

    # ---- 加载 ----------------------------------------------------------

    @staticmethod
    def _add_cuda_dll_dirs() -> None:
        """把 paddle/nvidia 自带的 CUDA 运行时目录加入 DLL 搜索路径。

        Windows 下 paddlepaddle-gpu 会把 cublasLt64_13.dll 等放在
        site-packages/nvidia/<pkg>/bin/x86_64，但不会自动加入搜索路径。
        ``ctypes.WinDLL``（paddle/transformers 内部使用）只按 PATH 与系统
        目录搜索，因此除 os.add_dll_directory 外还必须把目录加到 PATH。
        """
        if os.name != "nt":
            return
        import glob as _glob
        import site as _site

        candidates: list[str] = []
        for base in _site.getsitepackages() + [_site.getusersitepackages()]:
            candidates.extend(
                _glob.glob(str(Path(base) / "nvidia" / "*" / "bin" / "x86_64"))
            )
            candidates.extend(_glob.glob(str(Path(base) / "nvidia" / "*" / "bin")))
        for c in candidates:
            if not os.path.isdir(c):
                continue
            try:
                os.add_dll_directory(c)
            except OSError:
                pass
            if c not in os.environ.get("PATH", ""):
                os.environ["PATH"] = c + os.pathsep + os.environ.get("PATH", "")

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def supports_batch(self) -> bool:
        """是否走官方多输入队列路径。

        本地默认关闭，避免改变既有的逐页内存特征；vLLM/SGLang 服务端或
        显式 ``use_queues=True`` 时开启。
        """
        return bool(self.use_queues)

    def cache_signature(self) -> str:
        """模型层缓存签名：模型输出确定性由权重/依赖版本决定。

        只读包版本号，不加载模型。模型或推理引擎升级后签名变化，
        旧 raw 缓存自动失效（见 orchestrator._raw_cache_valid）。
        """
        try:
            import paddleocr  # noqa: F401
            import paddlex  # noqa: F401

            package_sig = (
                f"paddleocr-{paddleocr.__version__}|"
                f"paddlex-{paddlex.__version__}|{PaddleOcrVlParser.PIPELINE_VERSION}"
            )
        except ImportError:
            package_sig = f"unknown|{PaddleOcrVlParser.PIPELINE_VERSION}"
        profile = (
            f"backend={self.vl_backend or 'local'}|"
            f"queues={int(bool(self.use_queues))}|"
            f"hpi={int(bool(self.enable_hpi))}|"
            f"trt={int(bool(self.use_tensorrt))}|"
            f"precision={self.precision or 'default'}"
        )
        return f"{package_sig}|{profile}"

    def restore_result(self, raw: dict, page_no: int) -> PageOcrResult:
        """从模型层缓存（raw JSON）回放一页结果，与模型推理路径共用解析。"""
        result = PageOcrResult(page=page_no)
        result.raw = raw
        result.duration_ms = 0
        self._to_boxes(result)
        if not result.boxes:
            result.error = "缓存数据未返回任何版面框"
        return result

    def _load(self) -> None:
        """惰性加载流水线；首次加载会下载模型权重。"""
        if self._ocr is not None:
            return
        if self._load_attempted:
            raise RuntimeError(f"PaddleOCR-VL 加载失败：{self._load_error}")
        self._load_attempted = True
        self._add_cuda_dll_dirs()
        try:
            from paddleocr import PaddleOCRVL
        except ImportError as e:
            self._load_error = (
                "未安装 paddleocr/paddlepaddle。请先安装 OCR 依赖（pip install "
                f"\"paperbridge[ocr]\" 或直接安装 paddleocr）: {e}"
            )
            raise RuntimeError(self._load_error)

        # 空 kwargs 是官方默认配置（PaddleOCRVL()）。不要在这里关闭预处理、
        # 修改布局合并策略或指定流水线版本，否则本地结果不再能与官方体验页对齐。
        kwargs: dict[str, Any] = {}
        if self.page_batch_size:
            from copy import deepcopy
            from paddlex.inference import load_pipeline_config

            config = deepcopy(load_pipeline_config("PaddleOCR-VL-1.6"))
            # The default PDF batch is 64 pages. Bound it so page results and
            # unhealthy-output checks become available early, keeping the
            # official layout/content settings and VLM request concurrency.
            config["batch_size"] = self.page_batch_size
            # Official "large" keeps an outer figure instead of nested panels.
            # Preserve all other official class policies (not a global override).
            config["SubModules"]["LayoutDetection"]["layout_merge_bboxes_mode"][14] = "large"
            kwargs["paddlex_config"] = config
        if self.layout_merge_bboxes_mode:
            kwargs["layout_merge_bboxes_mode"] = self.layout_merge_bboxes_mode
        if self.device == "cpu":
            kwargs["device"] = "cpu"
        if self.model_dir:
            kwargs["model_dir"] = self.model_dir
        if self.vl_backend:
            kwargs.update(
                vl_rec_backend=self.vl_backend,
                vl_rec_server_url=self.vl_server_url,
                use_queues=bool(self.use_queues),
            )
            if self.vl_max_concurrency is not None:
                kwargs["vl_rec_max_concurrency"] = self.vl_max_concurrency
        elif self.use_queues:
            kwargs["use_queues"] = True
        if self.enable_hpi:
            kwargs["enable_hpi"] = True
        if self.use_tensorrt:
            kwargs["use_tensorrt"] = True
        if self.precision:
            kwargs["precision"] = self.precision
        logger.info(
            "正在加载 PaddleOCR-VL-%s 流水线（后端=%s，首次使用会下载模型）",
            self.PIPELINE_VERSION,
            self.vl_backend or "local",
        )
        t0 = time.time()
        try:
            self._ocr = PaddleOCRVL(**kwargs)
        except Exception as e:  # noqa: BLE001
            self._load_error = f"{type(e).__name__}: {e}"
            raise RuntimeError(self._load_error) from e
        logger.info("PaddleOCR-VL 流水线加载完成，耗时 %.1fs", time.time() - t0)

    def recognize_document(
        self, pdf_path: str | Path, output_dir: str | Path,
        *, progress: Callable[[int], None] | None = None,
    ) -> list[OfficialPageResult]:
        """按 PaddleOCR 官方方式直接解析 PDF 并保留官方导出物。

        每个页面单独存入 ``output_dir/page-XXX``，避免官方 ``save_to_*`` 在
        PDF 多页输入时使用同名文件而互相覆盖。除目录隔离外，不改写模型 JSON、
        Markdown 或图片资产。
        """
        self._load()
        base = Path(output_dir)
        base.mkdir(parents=True, exist_ok=True)
        started = time.time()
        pages: list[OfficialPageResult] = []
        try:
            predict_kwargs: dict[str, Any] = {}
            if self.use_queues:
                predict_kwargs["use_queues"] = True
            # PaddleOCR 3.7's predict() eagerly collects the whole document.
            # Its iterator is necessary for early page progress and fail-fast
            # output validation; both methods use the same official pipeline.
            predict = getattr(self._ocr, "predict_iter", None) or self._ocr.predict
            output = predict(str(pdf_path), **predict_kwargs)
            for page_no, item in enumerate(output, start=1):
                page_dir = base / f"page-{page_no:03d}"
                page_dir.mkdir(parents=True, exist_ok=True)
                item.save_to_json(save_path=str(page_dir))
                item.save_to_markdown(save_path=str(page_dir))

                json_file = next(page_dir.glob("*.json"), None)
                markdown_file = next(page_dir.glob("*.md"), None)
                if json_file is None or markdown_file is None:
                    raise RuntimeError("PaddleOCR 官方导出缺少 JSON 或 Markdown 文件")
                raw = json.loads(json_file.read_text(encoding="utf-8"))
                markdown = markdown_file.read_text(encoding="utf-8")
                self.validate_output(raw)
                pages.append(
                    OfficialPageResult(
                        page=page_no,
                        raw=raw,
                        markdown=markdown,
                        output_dir=page_dir,
                    )
                )
                if progress:
                    progress(page_no)
        except Exception as e:  # noqa: BLE001
            logger.exception("官方 PDF 直通解析失败")
            raise RuntimeError(f"官方 PaddleOCR PDF 解析失败：{type(e).__name__}: {e}") from e
        elapsed = int((time.time() - started) * 1000)
        for page in pages:
            page.duration_ms = elapsed // max(1, len(pages))
        return pages

    @staticmethod
    def validate_output(raw: dict) -> None:
        """Fail before saving a successful result if inference degenerates."""
        for block in raw.get("res", raw).get("parsing_res_list", []):
            text = str(block.get("block_content", block.get("content", "")) or "")
            if len(text) < 160:
                continue
            if text.count("\ufffd") >= max(12, len(text) // 10) or re.search(r"([^\W\d_])\1{79,}", text):
                raise RuntimeError("OCR 模型输出异常重复内容，已停止识别以避免继续空耗；请重启 OCR 服务后重试")

    # ---- 识别 ----------------------------------------------------------

    def recognize_region(self, image_path: str | Path, kind: str) -> str:
        """Official element prompt for a bounded quality retry; reuses warm models."""
        if kind not in {"formula", "table"}:
            raise ValueError("Only formula/table quality retries are supported")
        if getattr(self,"_prefer_native_regions",False):
            return self.recognize_region_native(image_path,kind)
        if self.vl_backend == "fastdeploy-server" and not getattr(self,"_region_health_checked",False):
            from scripts.ocr_health import probe_vlm
            try:
                probe_vlm(self.vl_server_url,timeout=20)
                self._region_health_checked = True
            except Exception:
                return self.recognize_region_native(image_path,kind)
        self._load()
        output = self._ocr.predict(str(image_path), use_layout_detection=False,
                                   prompt_label=kind, use_queues=False,
                                   max_new_tokens=2048 if kind == "formula" else 4096)
        item = next(iter(output), None)
        if item is None:
            return ""
        entries = _result_value(item, "parsing_res_list", []) or []
        return "\n".join(str(_result_value(block,"content","") or "") for block in entries).strip()

    def recognize_region_native(self, image_path: str | Path, kind: str) -> str:
        """Reuse native weights for bad remote regions without restarting servers.

        Refuse to load a second GPU model under memory pressure. The caller then
        retains the original result and its review warning, rather than using a
        slow CPU fallback or risking another active OCR job.
        """
        if not self.vl_backend:
            raise RuntimeError("原生复核结果仍异常，保留原结果供人工核对")
        parser = getattr(self,"_native_regions",None)
        if parser is None:
            import subprocess
            executable = shutil.which("nvidia-smi")
            if self.device == "cpu" or not executable:
                raise RuntimeError("需要可用的原生 GPU 进行公式复核，原结果已保留")
            response = subprocess.run([executable,"--query-gpu=memory.free","--format=csv,noheader,nounits","--id=0"],
                capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            try:
                free_mb = int(response.stdout.strip().splitlines()[0])
            except (ValueError,IndexError):
                free_mb = 0
            if response.returncode or free_mb < 4096:
                raise RuntimeError("空闲显存不足 4 GB，已跳过原生公式复核，原结果已保留")
            parser = PaddleOcrVlParser(device=self.device,vl_backend="",vl_server_url="",use_queues=False)
            self._native_regions = parser
        text = parser.recognize_region(image_path,kind)
        self._prefer_native_regions = True
        return text

    def _result_from_item(
        self, item: Any, page_no: int, duration_ms: int
    ) -> PageOcrResult:
        result = PageOcrResult(page=page_no, duration_ms=duration_ms)
        result.raw = self._first_json([item])
        self._to_boxes(result)
        if not result.boxes:
            result.error = "流水线未返回任何版面框"
        return result

    def recognize_page(self, image: bytes | str | Path, page_no: int) -> PageOcrResult:
        """识别一页图像，返回版面框 + 识别文本。"""
        self._load()
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                img_path = Path(tmp) / "page.png"
                if isinstance(image, bytes):
                    img_path.write_bytes(image)
                else:
                    shutil.copy(image, img_path)
                raw_list = self._ocr.predict(str(img_path))
                item = next(iter(raw_list), None)
            return self._result_from_item(item, page_no, int((time.time() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            logger.exception("页面 %d OCR 失败", page_no)
            return PageOcrResult(
                page=page_no,
                error=f"{type(e).__name__}: {e}",
                duration_ms=int((time.time() - t0) * 1000),
            )

    def recognize_pages(
        self,
        images: list[bytes | str | Path],
        page_nos: list[int],
    ) -> list[PageOcrResult]:
        """使用 PaddleOCR-VL 官方多输入队列识别多个页面。

        当 ``use_queues=True`` 时，PaddleX 会让数据加载、布局检测和 VLM
        推理通过内部队列异步衔接。这里传入一组页面，而不是像旧实现一样
        为每页创建一次临时目录并单独调用 ``predict``。
        """
        if len(images) != len(page_nos):
            raise ValueError("images 与 page_nos 的数量必须一致")
        if not images:
            return []
        self._load()
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths: list[str] = []
                for idx, image in enumerate(images):
                    img_path = Path(tmp) / f"page-{idx:04d}.png"
                    if isinstance(image, bytes):
                        img_path.write_bytes(image)
                    else:
                        shutil.copy(image, img_path)
                    paths.append(str(img_path))
                if self.use_queues:
                    raw_items = list(self._ocr.predict(paths, use_queues=True))
                else:
                    raw_items = list(self._ocr.predict(paths))
            if len(raw_items) != len(page_nos):
                raise RuntimeError(
                    f"流水线返回 {len(raw_items)} 页结果，期望 {len(page_nos)} 页"
                )
            average_ms = int((time.time() - t0) * 1000 / len(page_nos))
            return [
                self._result_from_item(item, page_no, average_ms)
                for item, page_no in zip(raw_items, page_nos)
            ]
        except Exception as e:  # noqa: BLE001
            logger.exception("批量 OCR 失败（%d 页）", len(page_nos))
            duration_ms = int((time.time() - t0) * 1000)
            return [
                PageOcrResult(
                    page=page_no,
                    error=f"{type(e).__name__}: {e}",
                    duration_ms=duration_ms,
                )
                for page_no in page_nos
            ]

    def _first_json(self, raw_list: list) -> dict:
        if not raw_list:
            return {}
        item = raw_list[0]
        # 首选直接提取紧凑字段，避免 .json 物化完整页面像素数组。
        try:
            compact = _compact_model_result(item)
            if compact["layout_det_res"]["boxes"] or compact["parsing_res_list"]:
                return compact
        except Exception:  # noqa: BLE001 - 兼容未来 PaddleX Result 结构
            logger.debug("紧凑提取 PaddleOCR-VL 结果失败，回退通用 JSON", exc_info=True)

        # 兼容性回退：旧/未知 PaddleX 结果对象。
        if hasattr(item, "json") and callable(item.json):
            try:
                return json_safe(dict(item.json))
            except Exception:  # noqa: BLE001
                pass
        if isinstance(item, dict):
            return json_safe(item)
        try:
            return json_safe(dict(item))
        except Exception:  # noqa: BLE001
            return {}

    def _to_boxes(self, result: PageOcrResult) -> None:
        """把后端 JSON 转为 LayoutBox 列表（带阅读顺序）。"""
        j = result.raw
        raw_boxes = _extract_boxes(j)
        if not raw_boxes:
            return
        page_w, page_h = _image_dims(j) or (1.0, 1.0)

        boxes: list[LayoutBox] = []
        for i, rb in enumerate(raw_boxes):
            label = str(rb.get("label") or "paragraph")
            bbox_px = list(rb.get("bbox") or [0, 0, 1, 1])
            # 保留模型 order 原值：None 表示模型未提供顺序，与 0 严格区分
            raw_order = rb.get("order")
            order: int | None = int(raw_order) if raw_order is not None else None
            score = float(rb.get("score") or 0.5)
            text = str(rb.get("text") or "")
            norm = normalize_bbox(bbox_px, page_w, page_h)
            if norm and norm[2] - norm[0] <= 0 and norm[3] - norm[1] <= 0:
                continue
            text_out, latex = _parse_markdown_text(text)
            boxes.append(
                LayoutBox(
                    label=label,
                    bbox=norm,
                    score=score,
                    order=order,
                    text=latex or text_out,
                    extra={
                        "model_order": order,
                        "bbox_px": bbox_px,
                        "latex": latex,
                        "markdown": text_out,
                    },
                )
            )
        # 不在此处重新编号：模型顺序由 resolve_reading_order() 决定
        result.boxes = boxes


class MockOcrParser:
    """测试用假后端：返回确定性版面框，不依赖模型。"""

    def __init__(self, boxes: list[LayoutBox] | None = None) -> None:
        self._boxes = boxes or []

    def is_available(self) -> bool:
        return True

    def recognize_page(self, image: bytes | str | Path, page_no: int) -> PageOcrResult:
        return PageOcrResult(page=page_no, boxes=[b for b in self._boxes])

    @staticmethod
    def cache_signature() -> str:
        return "mock-0"

    def restore_result(self, raw: dict, page_no: int) -> PageOcrResult:
        return PageOcrResult(page=page_no, boxes=[b for b in self._boxes])


def make_recognizer(device: str = "auto", model_dir: str | None = None) -> DocumentRecognizer:
    """工厂：按配置创建识别器。"""
    return PaddleOcrVlParser(device=device, model_dir=model_dir)
