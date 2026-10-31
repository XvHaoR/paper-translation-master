"""任务编排器：预检 → 渲染 → 版面/路由 → 识别 → 规范化 → 质量 → HTML。

对应方案 §6 架构图。支持四种 OCR 模式：

- ``native``：纯原生文本，不调用任何模型（快速路径）
- ``auto``（默认）：按预检策略，原生页走原生文本；扫描/混合页走完整流水线
- ``hybrid``：所有页面运行完整流水线，正文区域优先使用原生文本，
  公式/表格/图片/代码区域使用模型结果（方案 §5.3 混合提取）
- ``full``：所有页面完全使用模型结果
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.models.ir import (
    Block,
    BlockContent,
    BlockType,
    DocumentIR,
    DocumentMeta,
    PageInfo,
    QualityInfo,
    SourceInfo,
    SourceMethod,
    block_id,
)
from app.parsing.native_pdf import NativePdfExtractor
from app.parsing.paddleocr_vl import (
    DocumentRecognizer,
    MockOcrParser,
    OfficialPageResult,
    PageOcrResult,
    json_safe,
)
from app.parsing.page_strategy import (
    LayoutBox,
    map_label_to_block_type,
    normalize_bbox,
    resolve_reading_order,
    sort_boxes_reading_order,
)
from app.pipeline import normalize as norm
from app.pipeline.page_renderer import PageRenderer
from app.pipeline.preflight import run_preflight
from app.pipeline.quality import run_quality_checks
from app.rendering.html_renderer import HtmlRenderer
from app.rendering.markdown_util import markdown_table_to_html

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int, str], None]


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件写完后原子替换，避免中断留下半截 JSON/HTML。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

# 优先使用原生文本的版面类别
NATIVE_TEXT_LABELS = {
    "paragraph",
    "text",
    "text_region",
    "heading",
    "head",
    "paragraph_title",
    "section_header",
    "title",
    "doc_title",
    "document_title",
    "abstract",
    "caption",
    "figure_title",
    "figure_caption",
    "table_caption",
    "table_title",
    "chart_title",
    "footnote",
    "reference",
    "list",
}
# 需要保留原图截图的类别
CROP_LABELS = {
    "equation", "formula", "display_formula", "inline_formula",
    "table", "figure", "image", "picture", "chart", "graph",
}
# 直接丢弃的类别
# 直接丢弃的类别（旁注 aside_text 不混入正文，与页眉页脚同类处理）
DROP_LABELS = {"header", "page_header", "footer", "page_footer", "page_number", "seal", "aside_text"}

# 下载/系统水印强特征（Wiley/Elsevier/ScholarOne 等，OCR 分块不固定无法跨页匹配）
_WATERMARK_RE = re.compile(
    r"downloaded from|onlinelibrary|sci-hub|doi\.org/10\.\d{4}|scholarone|\bManuscripts\b",
    re.I,
)

PAGE_LABEL_TYPES = {
    "equation": BlockType.EQUATION_BLOCK,
    "formula": BlockType.EQUATION_BLOCK,
    "display_formula": BlockType.EQUATION_BLOCK,
    "inline_formula": BlockType.EQUATION_INLINE,
    "table": BlockType.TABLE,
    "figure": BlockType.FIGURE,
    "image": BlockType.FIGURE,
    "picture": BlockType.FIGURE,
    "chart": BlockType.FIGURE,
    "graph": BlockType.FIGURE,
    "code": BlockType.CODE,
}


@dataclass
class ConvertOptions:
    ocr_mode: str = "official"  # official | auto | native | full | hybrid
    ocr_device: str = "auto"  # auto | cpu | gpu
    render_dpi: int = 220
    pages: str = ""  # 空 = 全部；支持 "1-5,8,10-12"
    keep_raw: bool = True
    force: bool = False  # 忽略缓存


@dataclass
class ConversionResult:
    ok: bool = False
    document_id: str = ""
    doc_dir: Path | None = None
    ir: Optional[DocumentIR] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    status: str = "failed"  # ready | needs_review | failed

    @property
    def review_needed(self) -> bool:
        return any(b.quality.needs_review for b in (self.ir.blocks if self.ir else []))


def _parse_page_range(spec: str, page_count: int) -> list[int]:
    """解析页码范围 "1-5,8,10-12"。"""
    if not spec or spec.strip() == "":
        return list(range(1, page_count + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return sorted({p for p in pages if 1 <= p <= page_count})


class Orchestrator:
    """PDF → IR → HTML 的完整流程编排。"""

    def __init__(
        self,
        workspace: Path,
        recognizer: Optional[DocumentRecognizer] = None,
        renderer_factory: Callable[..., PageRenderer] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.recognizer = recognizer
        self._recognizer_lock = threading.Lock()
        self._runtime_recognizers: dict[str, DocumentRecognizer] = {}
        self._renderer_factory = renderer_factory or PageRenderer
        self._crop_seq = 0  # 裁剪文件全局序号（保证文件名唯一）
        (self.workspace / "documents").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------

    def convert(
        self,
        pdf_path: str | Path,
        options: ConvertOptions | None = None,
        progress: ProgressFn | None = None,
    ) -> ConversionResult:
        opts = options or ConvertOptions()
        t0 = time.time()
        pdf_path = Path(pdf_path)
        result = ConversionResult()
        total_pages = 0

        def report(stage: str, page: int = 0, message: str = "") -> None:
            if progress:
                progress(stage, page, total_pages, message)
            logger.info("[%s] %s", stage, message or f"page {page}")

        # 1. 预检
        report("preflight", message=f"预检 {pdf_path.name}")
        pre = run_preflight(str(pdf_path), ocr_mode=opts.ocr_mode)
        total_pages = pre.page_count
        result.warnings += pre.warnings
        if not pre.ok:
            result.errors += pre.errors
            return result
        result.document_id = pre.pdf_hash
        doc_dir = self.workspace / "documents" / result.document_id
        ir_path = doc_dir / "document.json"
        index_path = doc_dir / "index.html"

        # 2. 缓存命中（须匹配解析器缓存版本，算法升级后旧缓存自动失效）
        if (
            not opts.force
            and opts.ocr_mode in ("official", "auto")
            and ir_path.is_file()
            and index_path.is_file()
        ):
            try:
                ir = DocumentIR.model_validate_json(ir_path.read_text(encoding="utf-8"))
                if ir.document.parser.get("cache_version") != self._cache_version():
                    logger.info("解析器缓存版本升级，旧文档重新解析")
                else:
                    result.ok = True
                    result.ir = ir
                    result.doc_dir = doc_dir
                    result.status = "needs_review" if ir and any(
                        b.quality.needs_review for b in ir.blocks
                    ) else "ready"
                    result.warnings.append("命中缓存，直接复用已生成文档")
                    result.duration_ms = int((time.time() - t0) * 1000)
                    return result
            except Exception as e:  # noqa: BLE001
                logger.warning("缓存文档损坏，重新生成：%s", e)

        pages = _parse_page_range(opts.pages, pre.page_count)
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "assets").mkdir(exist_ok=True)
        (doc_dir / "assets" / "crops").mkdir(exist_ok=True)
        (doc_dir / "assets" / "figures").mkdir(exist_ok=True)
        (doc_dir / "assets" / "page-previews").mkdir(exist_ok=True)
        (doc_dir / "raw").mkdir(exist_ok=True)
        # 文档目录由内容哈希命名；目标已存在时必然是同一份 PDF。
        # 不重复覆盖还能避开 Windows 上 copy2 继承源文件只读属性后的 PermissionError。
        original_pdf = doc_dir / "original.pdf"
        if not original_pdf.is_file():
            shutil.copy2(pdf_path, original_pdf)
        self._prepare_static_assets(doc_dir)

        # 识别器可用性
        recognizer = self._resolve_recognizer(opts)
        if opts.ocr_mode == "official":
            return self._convert_official(
                pdf_path=pdf_path,
                pre=pre,
                pages=pages,
                recognizer=recognizer,
                doc_dir=doc_dir,
                result=result,
                report=report,
                started_at=t0,
            )
        if recognizer is None:
            if opts.ocr_mode in ("full", "hybrid"):
                result.errors.append(
                    "当前 OCR 模式需要 PaddleOCR-VL，但后端不可用；请安装 OCR 依赖"
                    "或改用 --ocr-mode auto/native"
                )
                result.duration_ms = int((time.time() - t0) * 1000)
                return result
            if opts.ocr_mode != "native":
                result.warnings.append("PaddleOCR-VL 后端不可用，回退到原生文本模式")
        # 2.5 模型层缓存（缓存分离）：算法版本（cache_version）失效时，
        #     若模型层缓存（raw）仍有效，跳过 VLM 推理、只重放后处理。
        #     _process_pages 内部逐页优先读取 raw 缓存，未命中才调模型。
        replay_raw = (
            not opts.force
            and recognizer is not None
            and self._raw_cache_valid(doc_dir, result.document_id, pages, recognizer, opts)
        )
        if replay_raw:
            result.warnings.append(
                "命中模型层缓存：跳过 VLM 推理，仅重放后处理（模型识别结果与上次一致）"
            )
            report("cache_replay", message="命中模型层缓存：跳过 VLM 推理，重放后处理")
        raw_outputs: dict[str, dict] = {}

        # 3. 逐页处理
        ir = DocumentIR()
        ir.document = DocumentMeta(
            id=result.document_id,
            source_pdf=pdf_path.name,
            page_count=pre.page_count,
            parser={
                "name": "paddleocr-vl" if recognizer else "native",
                "version": "v1.6",
                "cache_version": self._cache_version(),
                "settings": {
                    "ocr_mode": opts.ocr_mode,
                    "ocr_device": opts.ocr_device,
                    "render_dpi": opts.render_dpi,
                    "reading_order": self._reading_order_mode(),
                },
            },
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        try:
            ir = self._process_pages(
                ir=ir,
                pdf_path=pdf_path,
                pre_pages={p.number: p for p in pre.pages},
                pages=pages,
                opts=opts,
                recognizer=recognizer,
                doc_dir=doc_dir,
                raw_outputs=raw_outputs,
                report=report,
                # --force 只跳过本次缓存读取；新的模型结果仍应写回，
                # 否则强制重跑后留下的 meta 没有逐页文件，下一次无法热启动。
                use_raw_cache=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("转换失败")
            result.errors.append(f"转换失败：{type(e).__name__}: {e}")
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        # 2.7 模型层缓存元信息（算法版本失效时用于判断 raw 是否可回放）
        if recognizer is not None:
            self._save_raw_meta(doc_dir, result.document_id, pages, recognizer, opts)

        # 4. 规范化（跨页合并等）
        report("normalizing", message="规范化：跨页段落合并")
        self._promote_document_title(ir)
        ir.blocks = norm.merge_cross_page_paragraphs(ir.blocks)

        # 4.4 截断标题重识别修复（PP-DocLayoutV3 大标题检测框过窄 → "IV. E"）
        self._repair_truncated_headings(ir, doc_dir)

        # 4.5 脚注语义锚定（TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md §7）
        from app.pipeline.footnote_anchor import resolve_footnote_anchors

        fn_warnings = resolve_footnote_anchors(ir)
        result.warnings += fn_warnings

        # 5. 质量检查
        report("quality", message="质量检查")
        warnings, errors = run_quality_checks(ir, doc_dir)
        result.warnings += warnings
        result.errors += errors
        if not ir.blocks:
            result.errors.append("未生成任何内容块")

        # 6. 保存原始输出（调试附件）
        if opts.keep_raw:
            _atomic_write_text(
                doc_dir / "raw" / "parser-output.json",
                json.dumps(raw_outputs, ensure_ascii=False, indent=1, default=str),
            )

        # 7. 渲染 HTML
        report("rendering_html", message="生成阅读页")
        try:
            renderer = HtmlRenderer()
            index_html = renderer.render_document(ir, doc_dir)
            _atomic_write_text(index_path, index_html)
            # 渲染后检查：可见 LaTeX 定界符
            from app.pipeline.quality import check_rendered_html

            result.warnings += check_rendered_html(index_html)
        except Exception as e:  # noqa: BLE001
            logger.exception("HTML 渲染失败")
            result.errors.append(f"HTML 渲染失败：{type(e).__name__}: {e}")

        # 8. 保存 IR
        _atomic_write_text(
            ir_path,
            json.dumps(
                json.loads(ir.model_dump_json()), ensure_ascii=False, indent=2
            ),
        )

        result.ir = ir
        result.doc_dir = doc_dir
        result.duration_ms = int((time.time() - t0) * 1000)
        if result.errors:
            result.status = "failed"
        elif result.review_needed:
            result.status = "needs_review"
        else:
            result.status = "ready"
        result.ok = not result.errors
        report("done", message=f"完成：{result.status}，耗时 {result.duration_ms / 1000:.1f}s")
        return result

    # ------------------------------------------------------------------

    def _convert_official(
        self,
        *,
        pdf_path: Path,
        pre,
        pages: list[int],
        recognizer: Optional[DocumentRecognizer],
        doc_dir: Path,
        result: ConversionResult,
        report: ProgressFn,
        started_at: float,
    ) -> ConversionResult:
        """PaddleOCR 官方 PDF->Markdown 直通路径。

        这条默认路径刻意不调用原生 PDF 文本替换、阅读顺序重排、标题修复、
        脚注重锚定、跨页合并或二次表格识别。官方 Markdown 是唯一的内容与
        排版来源；IR 仅服务于 API 搜索和编辑接口。
        """
        recognize_document = getattr(recognizer, "recognize_document", None)
        if recognizer is None or not callable(recognize_document):
            result.errors.append(
                "官方模式需要 PaddleOCR-VL；请在论文翻译大师运行环境安装 paddleocr 和 paddlepaddle-gpu。"
            )
            result.duration_ms = int((time.time() - started_at) * 1000)
            return result

        report("recognizing", message=f"正在识别 0/{pre.page_count} 页")
        try:
            callbacks = {}
            if getattr(recognizer, "supports_document_progress", False):
                callbacks["progress"] = lambda page: report("recognizing", page, f"已识别 {page}/{pre.page_count} 页")
            official_pages: list[OfficialPageResult] = recognize_document(
                pdf_path, doc_dir / "assets" / "official", **callbacks
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("官方直通解析失败")
            result.errors.append(str(e))
            result.duration_ms = int((time.time() - started_at) * 1000)
            return result

        selected = set(pages)
        ir = DocumentIR(
            document=DocumentMeta(
                id=result.document_id,
                source_pdf=pdf_path.name,
                page_count=pre.page_count,
                parser={
                    "name": "paddleocr-vl-official",
                    "version": "PaddleOCRVL()",
                    "cache_version": self._cache_version(),
                    "settings": {"ocr_mode": "official"},
                },
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        )
        page_markdowns: list[tuple[int, str]] = []
        raw_outputs: dict[str, dict] = {}
        order_counter = 0

        for official_page in official_pages:
            if official_page.page not in selected:
                continue
            page_data = official_page.raw.get("res", official_page.raw)
            width = float(page_data.get("width") or 0)
            height = float(page_data.get("height") or 0)
            ir.pages.append(
                PageInfo(
                    number=official_page.page,
                    width=width,
                    height=height,
                    strategy="ocr",
                    reading_order_method="official",
                    reading_order_coverage=1.0,
                )
            )
            raw_outputs[f"page_{official_page.page:03d}"] = official_page.raw
            page_markdowns.append((official_page.page, official_page.markdown))

            for source_block in page_data.get("parsing_res_list") or []:
                label = str(
                    source_block.get("block_label", source_block.get("label", ""))
                ).strip().lower()
                text = str(
                    source_block.get("block_content", source_block.get("content", ""))
                    or ""
                )
                raw_bbox = source_block.get("block_bbox", source_block.get("bbox", [])) or []
                bbox = normalize_bbox(raw_bbox, width, height) if raw_bbox else []
                btype = BlockType(map_label_to_block_type(label))

                if btype in (BlockType.EQUATION_BLOCK, BlockType.EQUATION_INLINE):
                    content = BlockContent(latex=text or " ", display=btype is BlockType.EQUATION_BLOCK)
                elif btype is BlockType.TABLE and text.lstrip().lower().startswith("<table"):
                    content = BlockContent(html=text)
                elif btype is BlockType.FIGURE:
                    # 图片本身由官方 Markdown 的 imgs/ 目录引用，IR 中只保留可追踪占位。
                    content = BlockContent(image_path=f"assets/official/page-{official_page.page:03d}/imgs/")
                else:
                    content = BlockContent(text=text or " ")

                ir.add_block(
                    Block(
                        id=block_id(official_page.page, order_counter),
                        type=btype,
                        layout_label=label,
                        page=official_page.page,
                        order=order_counter,
                        bbox=bbox,
                        content=content,
                        source=SourceInfo(
                            method=SourceMethod.OCR,
                            page=official_page.page,
                            raw_ref=f"assets/official/page-{official_page.page:03d}",
                        ),
                        quality=QualityInfo(),
                    )
                )
                if btype is BlockType.DOCUMENT_TITLE and not ir.document.title:
                    ir.document.title = text.strip().replace("\n", " ")
                order_counter += 1

        if not ir.blocks:
            result.errors.append("PaddleOCR 官方流水线未生成任何内容块")
            result.duration_ms = int((time.time() - started_at) * 1000)
            return result

        report("rendering_html", message="按 PaddleOCR 官方 Markdown 生成 HTML")
        renderer = HtmlRenderer()
        _atomic_write_text(
            doc_dir / "index.html",
            renderer.render_official_document(ir, doc_dir, page_markdowns),
        )
        _atomic_write_text(
            doc_dir / "export.md",
            "\n\n".join(markdown for _page, markdown in page_markdowns),
        )
        _atomic_write_text(
            doc_dir / "raw" / "parser-output.json",
            json.dumps(raw_outputs, ensure_ascii=False, indent=1),
        )
        _atomic_write_text(
            doc_dir / "document.json",
            json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False, indent=2),
        )

        result.ok = True
        result.ir = ir
        result.doc_dir = doc_dir
        result.status = "ready"
        result.duration_ms = int((time.time() - started_at) * 1000)
        report("done", message=f"官方直通完成，耗时 {result.duration_ms / 1000:.1f}s")
        return result

    # ------------------------------------------------------------------

    def _repair_truncated_headings(self, ir: DocumentIR, doc_dir: Path) -> None:
        """重识别被截断的大标题（"IV. E" → "IV. EXPERIMENTS"）。

        复用 PaddleOCR-VL 服务（PAPERBRIDGE_OCR_VL_SERVER_URL）；失败或
        未配置时保留原文，不影响主流程。
        """
        try:
            from app.pipeline.heading_repair import repair_headings
        except Exception as e:  # noqa: BLE001
            logger.debug("标题修复模块不可用：%s", e)
            return
        server_url = os.environ.get("PAPERBRIDGE_OCR_VL_SERVER_URL", "").strip()
        if not server_url:
            return
        dict_blocks = [b.model_dump() for b in ir.blocks]
        try:
            fixed = repair_headings(
                dict_blocks,
                doc_dir / "assets" / "page-previews",
                server_url=server_url,
                api_key=os.environ.get("PAPERBRIDGE_OCR_VL_API_KEY", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("截断标题修复失败：%s", e)
            return
        if fixed:
            by_id = {d["id"]: d for d in dict_blocks}
            for b in ir.blocks:
                d = by_id.get(b.id)
                if d and (d.get("content") or {}).get("text") != b.content.text:
                    b.content.text = (d.get("content") or {}).get("text") or ""

    def _promote_document_title(self, ir: DocumentIR) -> None:
        """确定 document_title 并写入文档元信息。

        优先级：
        1. 模型标注的 doc_title（页面 1 第一个）；若其文本为 "Abstract" 视为误标。
        2. 回退：提升页面 1 第一个非 Abstract 的 heading。
        其余多余的 doc_title（如模型把 "Abstract" 也标成 doc_title）归为摘要标题。
        """
        blocks = ir.blocks

        doc_title = next(
            (b for b in blocks if b.page == 1 and b.type is BlockType.DOCUMENT_TITLE),
            None,
        )
        if doc_title is not None and re.fullmatch(
            r"Abstract.*", (doc_title.content.text or "").strip(), re.I
        ):
            doc_title.type = BlockType.ABSTRACT
            doc_title = None

        if doc_title is None:
            doc_title = next(
                (
                    b
                    for b in blocks
                    if b.page == 1
                    and b.type is BlockType.HEADING
                    and not re.fullmatch(
                        r"Abstract.*", (b.content.text or "").strip(), re.I
                    )
                ),
                None,
            )
            if doc_title is not None:
                doc_title.type = BlockType.DOCUMENT_TITLE

        if doc_title is not None:
            doc_title.quality.needs_review = False
            if not ir.document.title:
                ir.document.title = (
                    (doc_title.content.text or "").strip().replace("\n", " ")
                )

        for b in blocks:
            if b.type is BlockType.DOCUMENT_TITLE and b is not doc_title:
                text = (b.content.text or "").strip()
                if re.fullmatch(r"Abstract.*", text, re.I):
                    b.type = BlockType.ABSTRACT
                else:
                    b.type = BlockType.HEADING

    def _prepare_static_assets(self, doc_dir: Path) -> None:
        """把 reader.css / reader.js / MathJax 复制进文档目录，保证可整体移动。"""
        static_dir = Path(__file__).resolve().parents[1] / "static"
        assets = doc_dir / "assets"
        for name in ("reader.css", "reader.js", "table_excel.js"):
            src = static_dir / name
            if src.is_file():
                shutil.copy2(src, assets / name)
        mj_src = static_dir / "mathjax"
        if mj_src.is_dir():
            # 拷贝完整 MathJax 分发（tex-mml-chtml.js + input/tex/extensions + output/chtml/fonts），
            # 否则 \boldsymbol 等扩展触发动态加载时 404，导致整页公式不渲染
            mj_dst = assets / "mathjax"
            if mj_dst.exists():
                shutil.rmtree(mj_dst)
            shutil.copytree(mj_src, mj_dst)

    def _cache_version(self) -> str:
        from app.config import get_settings

        # The accelerated WSL service and local inference can produce different
        # floating-point decoding details.  Do not silently reuse a document
        # generated by the other execution backend.
        backend = os.environ.get("PAPERBRIDGE_OFFICIAL_ACCELERATOR", "").strip()
        return f"{get_settings().parser_cache_version}:{backend or 'local'}"

    def _reading_order_mode(self) -> str:
        from app.config import get_settings

        return get_settings().reading_order

    def _resolve_recognizer(self, opts: ConvertOptions) -> Optional[DocumentRecognizer]:
        if opts.ocr_mode == "native":
            return None
        if self.recognizer is not None:
            return self.recognizer
        device = (opts.ocr_device or "auto").strip().lower()
        # Official mode keeps PaddleOCR's output as the only content source.
        # A configured WSL FastDeploy endpoint may accelerate the VLM call, but
        # is not allowed to enable any PaperLoom post-processing rule.
        profile = f"{device}:{opts.ocr_mode}"
        with self._recognizer_lock:
            cached = self._runtime_recognizers.get(profile)
            if cached is not None:
                return cached
            if opts.ocr_mode == "official":
                accelerator = os.environ.get(
                    "PAPERBRIDGE_OFFICIAL_ACCELERATOR", ""
                ).strip().lower()
                if accelerator == "fastdeploy-vl16":
                    from app.parsing.paddleocr_vl import PaddleOcrVlParser

                    parser = PaddleOcrVlParser(device=device)
                    if not parser.is_available():
                        return None
                    self._runtime_recognizers[profile] = parser
                    return parser
            from app.parsing.paddleocr_vl import PaddleOcrVlParser

            parser = PaddleOcrVlParser(
                device=device,
                vl_backend="" if opts.ocr_mode == "official" else None,
                use_queues=False if opts.ocr_mode == "official" else None,
            )
            if not parser.is_available():
                return None
            self._runtime_recognizers[profile] = parser
            return parser

    # ---- 模型层缓存（缓存分离，L1）----------------------------------------
    #
    # 一次转换拆成两段：VLM 模型推理（贵，64 分钟级）→ 后处理（快，秒级）。
    # raw/page_NNN.json 保存每页模型原始输出，raw/meta.json 记录其有效性
    # （pdf_hash + 模型签名 + 渲染参数）。算法版本（cache_version）失效时
    # 只要模型层仍有效，就跳过 VLM 推理、从 raw 回放后处理（见 convert）。

    def _vlm_signature(self, recognizer: Optional[DocumentRecognizer]) -> str:
        sig = getattr(recognizer, "cache_signature", None)
        return sig() if callable(sig) else "unknown"

    @staticmethod
    def _raw_meta_path(doc_dir: Path) -> Path:
        return doc_dir / "raw" / "meta.json"

    @staticmethod
    def _page_raw_path(doc_dir: Path, page_no: int) -> Path:
        return doc_dir / "raw" / f"page_{page_no:03d}.json"

    def _raw_cache_valid(
        self,
        doc_dir: Path,
        pdf_hash: str,
        pages: list[int],
        recognizer: DocumentRecognizer,
        opts: ConvertOptions,
    ) -> bool:
        """模型层缓存是否覆盖所需页（meta 门禁 + 逐页文件存在性）。"""
        meta_path = self._raw_meta_path(doc_dir)
        if not meta_path.is_file():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if meta.get("schema") != 2:
            return False
        if meta.get("pdf_hash") != pdf_hash:
            return False
        if meta.get("render_dpi") != opts.render_dpi:
            return False
        if meta.get("vlm_version") != self._vlm_signature(recognizer):
            return False
        cached = set(meta.get("pages") or [])
        return all(
            p in cached and self._page_raw_path(doc_dir, p).is_file() for p in pages
        )

    def _load_page_ocr(
        self,
        doc_dir: Path,
        page_no: int,
        recognizer: DocumentRecognizer,
    ) -> Optional[PageOcrResult]:
        """从模型层缓存回放一页；未命中/版本不符/损坏返回 None（重跑模型）。"""
        path = self._page_raw_path(doc_dir, page_no)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("schema") != 2:
            return None
        if data.get("pdf_hash") != doc_dir.name:
            return None
        if data.get("vlm_version") != self._vlm_signature(recognizer):
            return None
        restore = getattr(recognizer, "restore_result", None)
        if not callable(restore):
            return None
        return restore(data.get("raw") or {}, page_no)

    def _save_page_ocr(
        self,
        doc_dir: Path,
        page_no: int,
        ocr_res: PageOcrResult,
        recognizer: DocumentRecognizer,
        pdf_hash: str,
    ) -> None:
        data = {
            "schema": 2,
            "pdf_hash": pdf_hash,
            "vlm_version": self._vlm_signature(recognizer),
            "page": page_no,
            "duration_ms": ocr_res.duration_ms,
            "raw": json_safe(ocr_res.raw),
        }
        try:
            _atomic_write_text(
                self._page_raw_path(doc_dir, page_no),
                json.dumps(data, ensure_ascii=False, default=str),
            )
        except OSError:  # noqa: BLE001
            logger.warning("第 %d 页模型层缓存写入失败", page_no)

    def _save_raw_meta(
        self,
        doc_dir: Path,
        pdf_hash: str,
        pages: list[int],
        recognizer: DocumentRecognizer,
        opts: ConvertOptions,
    ) -> None:
        meta = {
            "schema": 2,
            "pdf_hash": pdf_hash,
            "vlm_version": self._vlm_signature(recognizer),
            "render_dpi": opts.render_dpi,
            "pages": sorted(pages),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            _atomic_write_text(
                self._raw_meta_path(doc_dir),
                json.dumps(meta, ensure_ascii=False, indent=1),
            )
        except OSError:  # noqa: BLE001
            logger.warning("模型层缓存 meta 写入失败")

    def _process_pages(
        self,
        ir: DocumentIR,
        pdf_path: Path,
        pre_pages: dict[int, object],
        pages: list[int],
        opts: ConvertOptions,
        recognizer: Optional[DocumentRecognizer],
        doc_dir: Path,
        raw_outputs: dict,
        report: ProgressFn,
        use_raw_cache: bool = True,
    ) -> DocumentIR:
        from app.pipeline.preflight import PagePreflight

        with PageRenderer(
            str(pdf_path), render_dpi=opts.render_dpi
        ) as renderer, NativePdfExtractor(pdf_path) as native:
            pdf_path = Path(pdf_path)
            # 收集页眉页脚候选（跨页重复短行）
            all_native: dict[int, list] = {}
            for page_no in pages:
                blocks, _ = native.extract_text_blocks(page_no)
                all_native[page_no] = [
                    (
                        [
                            min(ln.bbox[0] for ln in bl.lines) / max(1.0, native.page_size(page_no)[0]),
                            min(ln.bbox[1] for ln in bl.lines) / max(1.0, native.page_size(page_no)[1]),
                            max(ln.bbox[2] for ln in bl.lines) / max(1.0, native.page_size(page_no)[0]),
                            max(ln.bbox[3] for ln in bl.lines) / max(1.0, native.page_size(page_no)[1]),
                        ],
                        " ".join(ln.text for ln in bl.lines).strip(),
                    )
                    for bl in blocks
                    if bl.lines
                ]
            header_footer_texts = norm.collect_repeated_short_lines(
                [all_native[p] for p in pages if p in all_native]
            )

            # 官方 PaddleOCR-VL 的 use_queues 模式仅在一次输入多页时才能把
            # 数据加载、布局检测和 VLM 推理流水化。强制冷解析时预先渲染所有
            # OCR 页并批量提交；缓存回放及本地默认模式仍保留逐页路径。
            batch_ocr_results: dict[int, PageOcrResult] = {}
            recognize_pages = getattr(recognizer, "recognize_pages", None)
            batch_enabled = bool(getattr(recognizer, "supports_batch", False))
            if (
                opts.force
                and recognizer is not None
                and batch_enabled
                and callable(recognize_pages)
            ):
                batch_page_nos: list[int] = []
                batch_images: list[bytes] = []
                for candidate_page in pages:
                    candidate_pre: PagePreflight = pre_pages.get(candidate_page)
                    candidate_strategy = (
                        candidate_pre.strategy if candidate_pre else "native"
                    )
                    if opts.ocr_mode == "full":
                        candidate_strategy = "ocr"
                    elif opts.ocr_mode == "hybrid":
                        candidate_strategy = "hybrid"
                    use_ocr_for_page = candidate_strategy in ("ocr", "hybrid") or (
                        opts.ocr_mode == "auto"
                    )
                    if use_ocr_for_page:
                        batch_page_nos.append(candidate_page)
                        batch_images.append(renderer.render_page(candidate_page))
                if batch_page_nos:
                    logger.info(
                        "官方 OCR 队列批量提交 %d 页（后端=%s）",
                        len(batch_page_nos),
                        getattr(recognizer, "vl_backend", "unknown"),
                    )
                    batch_ocr_results = {
                        res.page: res
                        for res in recognize_pages(batch_images, batch_page_nos)
                    }

            order_counter = 0
            for page_no in pages:
                pre: PagePreflight = pre_pages.get(page_no)
                strategy = pre.strategy if pre else "native"
                if opts.ocr_mode == "full":
                    strategy = "ocr"
                elif opts.ocr_mode == "hybrid":
                    strategy = "hybrid"
                elif opts.ocr_mode == "native":
                    strategy = "native"
                # auto 模式下原生页同样运行完整流水线：版面分析定位公式/表格
                # 区域，正文区域仍优先使用原生文本（方案 §5.3 混合提取）。
                # 只有显式 --ocr-mode native 才完全跳过模型。
                use_ocr = (
                    recognizer is not None
                    and strategy in ("ocr", "hybrid")
                    or (recognizer is not None and opts.ocr_mode == "auto")
                )
                if opts.ocr_mode == "native":
                    use_ocr = False

                page_w, page_h = native.page_size(page_no)
                # 策略展示（READING_ORDER_TECHNICAL_DESIGN.md §9.3）：
                # auto 模式下原生页实际运行了版面分析 + 模型识别（公式/表格），
                # 只是正文优先原生文本，应展示为混合提取而非纯 native。
                display_strategy = strategy
                if (
                    strategy == "native"
                    and use_ocr
                    and opts.ocr_mode == "auto"
                    and recognizer is not None
                ):
                    display_strategy = "hybrid"
                ir.pages.append(
                    PageInfo(
                        number=page_no,
                        width=page_w,
                        height=page_h,
                        rotation=pre.rotation if pre else 0,
                        strategy=display_strategy,
                    )
                )
                report("rendering_pages", page_no, f"渲染第 {page_no} 页")
                preview_name = renderer.save_page_render(
                    page_no, doc_dir / "assets" / "page-previews", "preview"
                )
                ir.pages[-1].preview = f"assets/page-previews/{preview_name}"

                # 原生文本块
                native_blocks, _ = native.extract_text_blocks(page_no)

                if not use_ocr:
                    report("recognizing", page_no, f"原生文本提取第 {page_no} 页")
                    order_counter = self._emit_native_blocks(
                        ir, native_blocks, page_no, order_counter, page_w, page_h,
                        header_footer_texts, native, renderer, doc_dir, pdf_path,
                    )
                    continue

                # OCR 路径
                report("layout_analysis", page_no, f"版面分析第 {page_no} 页")
                # 缓存分离：模型层缓存（raw）命中时跳过 VLM 推理直接回放，
                # 未命中（含 --force）才渲染页面调模型并回写缓存。
                ocr_res: Optional[PageOcrResult] = None
                if page_no in batch_ocr_results:
                    ocr_res = batch_ocr_results[page_no]
                    if use_raw_cache and not ocr_res.error:
                        self._save_page_ocr(
                            doc_dir, page_no, ocr_res, recognizer, doc_dir.name
                        )
                elif use_raw_cache and not opts.force:
                    ocr_res = self._load_page_ocr(doc_dir, page_no, recognizer)
                if ocr_res is None:
                    img_bytes = renderer.render_page(page_no)
                    ocr_res = recognizer.recognize_page(img_bytes, page_no)
                    if use_raw_cache and not ocr_res.error:
                        self._save_page_ocr(
                            doc_dir, page_no, ocr_res, recognizer, doc_dir.name
                        )
                if ocr_res.error and not ocr_res.boxes:
                    result_warn = f"第 {page_no} 页 OCR 失败：{ocr_res.error}，回退原生文本"
                    logger.warning(result_warn)
                    ir.pages[-1].strategy = "native"
                    order_counter = self._emit_native_blocks(
                        ir, native_blocks, page_no, order_counter, page_w, page_h,
                        header_footer_texts, native, renderer, doc_dir, pdf_path,
                    )
                    continue
                if ocr_res.raw:
                    raw_outputs[f"page_{page_no:03d}"] = {
                        "boxes": [
                            {
                                "label": b.label,
                                "bbox": b.bbox,
                                "score": b.score,
                                "order": b.order,
                                "text": b.text,
                            }
                            for b in ocr_res.boxes
                        ],
                        "duration_ms": ocr_res.duration_ms,
                    }

                # 阅读顺序决策：有效模型顺序优先，几何排序仅作回退
                boxes, order_diag = resolve_reading_order(
                    ocr_res.boxes, 1.0, mode=self._reading_order_mode()
                )
                if ocr_res.raw:
                    raw_outputs[f"page_{page_no:03d}"]["reading_order"] = order_diag.to_dict()
                ir.pages[-1].reading_order_method = order_diag.method
                ir.pages[-1].reading_order_coverage = order_diag.model_coverage
                ir.pages[-1].detected_columns = order_diag.detected_columns
                order_counter = self._emit_ocr_blocks(
                    ir=ir,
                    boxes=boxes,
                    native_blocks=native_blocks,
                    page_no=page_no,
                    page_w=page_w,
                    page_h=page_h,
                    strategy=strategy,
                    order_counter=order_counter,
                    header_footer_texts=header_footer_texts,
                    native=native,
                    renderer=renderer,
                    doc_dir=doc_dir,
                    recognizer=recognizer,
                    use_raw_cache=use_raw_cache,
                )
        return ir

    # ------------------------------------------------------------------

    def _emit_native_blocks(
        self,
        ir: DocumentIR,
        native_blocks,
        page_no: int,
        order_counter: int,
        page_w: float,
        page_h: float,
        header_footer_texts: set[str],
        native: NativePdfExtractor,
        renderer: PageRenderer,
        doc_dir: Path,
        pdf_path: Path | None = None,
    ) -> int:
        for nb in native_blocks:
            b = norm.native_block_to_ir(
                nb, page_no, order_counter, page_w, page_h, header_footer_texts
            )
            for blk in [b] if isinstance(b, Block) else (b or []):
                if blk is None:
                    continue
                # 下载/系统水印（Wiley/ScholarOne 等）丢弃
                if _WATERMARK_RE.search(blk.content.text or ""):
                    continue
                blk.order = order_counter
                ir.add_block(blk)
                order_counter += 1
        # 图片块（PyMuPDF dict 中的 type=1 块）
        self._emit_native_figures(
            ir, page_no, page_w, page_h, renderer, doc_dir, order_counter, pdf_path
        )
        return order_counter

    def _emit_native_figures(
        self,
        ir: DocumentIR,
        page_no: int,
        page_w: float,
        page_h: float,
        renderer: PageRenderer,
        doc_dir: Path,
        order_counter: int,
        pdf_path: Path | None = None,
    ) -> None:
        import fitz

        with fitz.open(str(pdf_path)) as doc:
            page = doc[page_no - 1]
            d = page.get_text("dict")
            for block in d.get("blocks", []):
                if block.get("type") != 1:
                    continue
                bbox_px = block["bbox"]
                norm_bbox = [
                    bbox_px[0] / max(1.0, page_w),
                    bbox_px[1] / max(1.0, page_h),
                    bbox_px[2] / max(1.0, page_w),
                    bbox_px[3] / max(1.0, page_h),
                ]
                try:
                    png = renderer.crop_region(page_no, norm_bbox)
                except Exception:  # noqa: BLE001
                    continue
                name = f"p{page_no:03d}-fig{order_counter:03d}.png"
                (doc_dir / "assets" / "figures" / name).write_bytes(png)
                ir.add_block(
                    Block(
                        id=block_id(page_no, order_counter),
                        type=BlockType.FIGURE,
                        page=page_no,
                        order=order_counter,
                        bbox=norm_bbox,
                        content=BlockContent(
                            image_path=f"assets/figures/{name}",
                        ),
                        source=SourceInfo(method=SourceMethod.NATIVE, page=page_no),
                    )
                )
                order_counter += 1

    def _emit_ocr_blocks(
        self,
        ir: DocumentIR,
        boxes: list[LayoutBox],
        native_blocks,
        page_no: int,
        page_w: float,
        page_h: float,
        strategy: str,
        order_counter: int,
        header_footer_texts: set[str],
        native: NativePdfExtractor,
        renderer: PageRenderer,
        doc_dir: Path,
        recognizer: Optional[DocumentRecognizer] = None,
        use_raw_cache: bool = True,
    ) -> int:
        # 原生块按段落切分并建 bbox 索引，供区域匹配
        native_texts: list[tuple[list[float], str]] = []
        for nb in native_blocks:
            for para in norm.assemble_paragraphs(nb.lines):
                text = norm.dehyphenate("\n".join(ln.text for ln in para))
                if not text:
                    continue
                bbox = [
                    min(ln.bbox[0] for ln in para) / max(1.0, page_w),
                    min(ln.bbox[1] for ln in para) / max(1.0, page_h),
                    max(ln.bbox[2] for ln in para) / max(1.0, page_w),
                    max(ln.bbox[3] for ln in para) / max(1.0, page_h),
                ]
                native_texts.append((bbox, text))

        for box in boxes:
            label = (box.label or "").strip().lower()
            if label in DROP_LABELS:
                continue
            # 页眉/页脚过滤：顶部或底部区域且跨页重复的文本（下载水印等）
            if header_footer_texts and box.text.strip() in header_footer_texts:
                if box.bbox and (
                    box.bbox[1] <= 0.08 or box.bbox[3] >= 0.92
                ):
                    continue
            # 下载/系统水印强特征（Wiley/Elsevier/ScholarOne 等）
            if _WATERMARK_RE.search(box.text):
                continue
            btype = PAGE_LABEL_TYPES.get(label)
            if btype is None:
                btype = BlockType(map_label_to_block_type(label))
            text = box.text.strip()

            # 行内公式区域：模型不输出独立识别文本（以内嵌 $...$ 形式
            # 出现在正文中），不单独成块，避免公式框错配正文。
            if label == "inline_formula" and not text:
                continue

            # 公式编号（如 "(1)"）：并入前一个公式块
            if label == "formula_number" and text:
                if ir.blocks and ir.blocks[-1].type is BlockType.EQUATION_BLOCK:
                    ir.blocks[-1].content.note = text
                continue

            # 1) 正文类：优先原生文本；含行内公式（$...$）的段落用 OCR
            #    文本，否则原生文本中的公式会丢失（原生提取对嵌入字体
            #    公式只能得到碎片）。
            native_text = ""
            ocr_has_inline_math = "$" in text
            if label in NATIVE_TEXT_LABELS:
                native_text = self._match_native_text(box, native_texts)
            if label in NATIVE_TEXT_LABELS and (native_text or strategy == "hybrid"):
                if native_text and not ocr_has_inline_math:
                    if norm.is_list_text(text):
                        # 列表保留 OCR 文本:原生提取会丢失项目符号与连字符
                        method = SourceMethod.OCR
                    else:
                        text = native_text
                        method = SourceMethod.MIXED if strategy == "hybrid" else SourceMethod.NATIVE
                else:
                    method = SourceMethod.OCR

            content: dict = {}
            source_method = SourceMethod.OCR
            crop_rel: str | None = None

            if btype in (BlockType.EQUATION_BLOCK, BlockType.EQUATION_INLINE):
                latex = box.extra.get("latex") or text
                if not latex:
                    continue
                crop_rel = self._save_crop(page_no, box, renderer, doc_dir)
                content = {"latex": latex, "display": btype is BlockType.EQUATION_BLOCK}
                if btype is BlockType.EQUATION_INLINE:
                    btype = BlockType.EQUATION_BLOCK
            elif btype is BlockType.TABLE:
                # PaddleOCR-VL-1.6 对表格元素直接输出 HTML；回退到 Markdown 转换
                html = text if text.lstrip().lower().startswith("<table") else ""
                if not html:
                    html = markdown_table_to_html(text)
                    if not html and box.extra.get("markdown"):
                        html = markdown_table_to_html(box.extra["markdown"])
                # 表格区域高 DPI 二次识别：小字号表头在低 DPI 下可能丢失
                # （如 Medlyn Table 2 的表头），用 300 DPI 裁剪重识别保证
                # 质量；整页表格（面积 >60%）跳过（成本高且首版已可靠）；
                # 若二次结果为空或行数明显更少则保留首版。
                table_area = (
                    (box.bbox[2] - box.bbox[0]) * (box.bbox[3] - box.bbox[1])
                    if len(box.bbox) == 4
                    else 0.0
                )
                retry_reason = _table_retry_reason(html, box.score)
                if recognizer is not None and table_area < 0.6 and retry_reason:
                    logger.info(
                        "第 %d 页表格触发高 DPI 重试：%s", page_no, retry_reason
                    )
                    retry_html = self._retry_table_ocr(
                        page_no, box, renderer, recognizer, doc_dir,
                        use_raw_cache=use_raw_cache,
                    )
                    if retry_html and _table_rows(retry_html) >= max(1, _table_rows(html)):
                        html = retry_html
                crop_rel = self._save_crop(page_no, box, renderer, doc_dir)
                rows = len(re.findall(r"<tr>", html or "")) or (text.count("\n") + 1 if text else 0)
                cols = 0
                if html:
                    first_tr = re.search(r"<tr>(.*?)</tr>", html, re.S)
                    if first_tr:
                        cols = len(re.findall(r"<t[dh][ >]", first_tr.group(1)))
                content = {
                    "html": html or "",
                    "rows": rows,
                    "cols": cols,
                }
                if not html:
                    content["note"] = "表格识别失败，请查看原图"
            elif btype is BlockType.FIGURE:
                crop_rel = self._save_crop(page_no, box, renderer, doc_dir, subdir="figures")
                content = {"image_path": crop_rel}
            elif btype is BlockType.CODE:
                content = {"text": text, "note": "代码块"}
            else:
                if not text:
                    continue
                if norm.is_list_text(text):
                    # 带项目符号的段落识别为无序/有序列表
                    btype = BlockType.LIST
                    content = {"text": text, "items": norm.split_list_items(text)}
                    source_method = SourceMethod.OCR  # 列表文本来自 OCR(原生提取无 bullet)
                else:
                    content = {"text": text}
                    if label in NATIVE_TEXT_LABELS:
                        source_method = (
                            SourceMethod.MIXED if strategy == "hybrid" else SourceMethod.NATIVE
                        )

            quality = QualityInfo(confidence=box.score)
            if box.score < 0.5 or (btype in (BlockType.EQUATION_BLOCK, BlockType.TABLE) and box.score < 0.6):
                quality.warnings.append(f"低置信度识别（{box.score:.2f}）")
                quality.needs_review = True

            ir.add_block(
                Block(
                    id=block_id(page_no, order_counter),
                    type=btype,
                    page=page_no,
                    order=order_counter,
                    bbox=box.bbox,
                    layout_label=label,
                    content=BlockContent(**content),
                    source=SourceInfo(
                        method=source_method,
                        page=page_no,
                        crop=crop_rel,
                    ),
                    quality=quality,
                )
            )
            order_counter += 1
        return order_counter

    def _retry_table_ocr(
        self,
        page_no: int,
        box: LayoutBox,
        renderer: PageRenderer,
        recognizer: DocumentRecognizer,
        doc_dir: Path,
        use_raw_cache: bool = True,
    ) -> str:
        """对表格区域用 300 DPI 裁剪二次识别，返回 HTML（可能为空）。

        结果按 页+bbox 缓存到 raw/table-retry/（模型层缓存的一部分）：
        算法迭代重放后处理时，表格二次识别同样跳过模型推理。
        """
        cache_path = self._table_retry_path(doc_dir, page_no, box)
        if use_raw_cache and cache_path.is_file():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    data.get("schema") == 2
                    and data.get("vlm_version") == self._vlm_signature(recognizer)
                ):
                    retry = recognizer.restore_result(data.get("raw") or {}, page_no)
                    return self._table_html_from_result(retry)
            except (OSError, ValueError):
                pass
        try:
            crop_img = renderer.crop_region(page_no, box.bbox, dpi=300)
        except Exception:  # noqa: BLE001
            return ""
        retry = recognizer.recognize_page(crop_img, page_no)
        if not retry.error and use_raw_cache:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(
                    cache_path,
                    json.dumps(
                        {
                            "schema": 2,
                            "pdf_hash": doc_dir.name,
                            "vlm_version": self._vlm_signature(recognizer),
                            "raw": json_safe(retry.raw),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            except OSError:  # noqa: BLE001
                logger.warning("第 %d 页表格重试缓存写入失败", page_no)
        return self._table_html_from_result(retry)

    @staticmethod
    def _table_html_from_result(retry: PageOcrResult) -> str:
        """从二次识别结果中提取表格 HTML（整页/裁剪回放共用同一解析）。"""
        for rb in retry.boxes:
            if rb.label.strip().lower() == "table":
                html = rb.text if rb.text.lstrip().lower().startswith("<table") else ""
                if not html:
                    html = markdown_table_to_html(rb.text) or ""
                return html
        return ""

    @staticmethod
    def _table_retry_path(doc_dir: Path, page_no: int, box: LayoutBox) -> Path:
        key = "-".join(f"{v:.3f}" for v in (box.bbox or []))
        return doc_dir / "raw" / "table-retry" / f"p{page_no:03d}-{key}.json"

    def _match_native_text(self, box: LayoutBox, native_texts: list) -> str:
        """用 IoU 匹配原生文本段落；重叠足够时返回其文本。"""
        best_text, best_iou = "", 0.0
        for bbox, text in native_texts:
            iou = _bbox_iou(box.bbox, bbox)
            if iou > best_iou:
                best_iou, best_text = iou, text
        return best_text if best_iou >= 0.4 else ""

    def _save_crop(
        self,
        page_no: int,
        box: LayoutBox,
        renderer: PageRenderer,
        doc_dir: Path,
        subdir: str = "crops",
    ) -> str:
        try:
            png = renderer.crop_region(page_no, box.bbox)
        except Exception:  # noqa: BLE001
            return ""
        # 文件名必须唯一：chart/figure 等框无模型 order（None），
        # 若用 order 命名会互相覆盖（Figure 2a/2b 保存成同一文件）。
        self._crop_seq += 1
        name = f"p{page_no:03d}-{box.label.replace(' ', '_')}-{self._crop_seq:03d}.png"
        (doc_dir / "assets" / subdir / name).write_bytes(png)
        return f"assets/{subdir}/{name}"


def _table_rows(html: str) -> int:
    """统计表格 HTML 行数（<tr> 数量）。"""
    return len(re.findall(r"<tr\b", html or "", re.I))


def _table_retry_reason(html: str, confidence: float) -> str:
    """返回表格需要高 DPI 重试的原因；空字符串表示首次结果可用。"""
    if not (html or "").strip():
        return "首次结果为空"
    if not re.search(r"<table\b", html, re.I):
        return "未生成 table 结构"
    rows = _table_rows(html)
    cells = len(re.findall(r"<t[dh]\b", html, re.I))
    if rows < 1 or cells < 2:
        return "行列结构不完整"
    if confidence < 0.6:
        return f"低置信度 {confidence:.2f}"
    return ""


def _bbox_iou(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if inter <= 0:
        return 0.0
    a_area = (a[2] - a[0]) * (a[3] - a[1])
    b_area = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(a_area + b_area - inter, 1e-9)
