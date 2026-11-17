"""版面策略路由：按页或内容块决定原生提取与 OCR 的分工。

对应方案 §5.3 混合提取原则：

    原生正文区域  → 优先读取 PDF 内嵌文本
    扫描正文区域  → OCR
    行内/行间公式 → 公式识别为 LaTeX
    表格区域      → 表格结构识别为 HTML
    图片区域      → 从 PDF 提取或按区域裁剪
    图注/脚注     → 原生文本优先，OCR 回退
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 版面类别 → IR 块类型映射（PaddleOCR-VL-1.6 常用 label）
LAYOUT_LABEL_MAP = {
    "title": "document_title",
    "doc_title": "document_title",
    "document_title": "document_title",
    "abstract": "abstract",
    "heading": "heading",
    "head": "heading",
    "paragraph_title": "heading",
    "section_header": "heading",
    "paragraph": "paragraph",
    "text": "paragraph",
    "text_region": "paragraph",
    "list": "list",
    "equation": "equation_block",
    "formula": "equation_block",
    "display_formula": "equation_block",
    "inline_formula": "equation_inline",
    "table": "table",
    "figure": "figure",
    "image": "figure",
    "picture": "figure",
    "chart": "figure",
    "graph": "figure",
    "caption": "caption",
    "figure_title": "caption",
    "figure_caption": "caption",
    "table_caption": "caption",
    "table_title": "caption",
    "chart_title": "caption",
    "footnote": "footnote",
    "vision_footnote": "footnote",
    "reference": "reference",
    "reference_content": "reference",
    "number": "page_number",
    "formula_number": "unknown",
    "code": "code",
    "header": "page_header",
    "page_header": "page_header",
    "footer": "page_footer",
    "page_footer": "page_footer",
    "page_number": "page_number",
    "seal": "figure",
    "stamp": "figure",
}


@dataclass
class LayoutBox:
    """版面框：类别 + 归一化坐标 + 置信度。

    ``order`` 为模型给出的阅读顺序；``None`` 表示模型未提供，
    与 ``0``（合法顺序）必须严格区分（READING_ORDER_TECHNICAL_DESIGN §6.1）。
    """

    label: str
    bbox: list[float]  # [x0, y0, x1, y1] 归一化 0~1
    score: float = 1.0
    order: int | None = None
    text: str = ""  # 识别文本（VLM 输出）
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 框角色分类（方案 §4.2 / §7.2）：集中维护，避免解析器/排序器/渲染器不一致
# ---------------------------------------------------------------------------

# 内容框：参与正文排序与渲染
CONTENT_LABELS = {
    "doc_title",
    "title",
    "document_title",
    "paragraph_title",
    "heading",
    "head",
    "section_header",
    "abstract",
    "text",
    "text_region",
    "paragraph",
    "reference_content",
    "caption",
    "figure_title",
    "figure_caption",
    "table_caption",
    "table_title",
    "chart_title",
    "display_formula",
    "equation",
    "formula",
    "table",
    "figure",
    "image",
    "picture",
    "chart",
    "graph",
    "list",
    "code",
}

# 父级结构容器：表达结构，不直接输出内容（§7.3）
CONTAINER_LABELS = {"reference"}

# 辅助元素：不参与正文列检测，按特殊规则放置（§4.3 / §7.5）
AUXILIARY_LABELS = {
    "footnote",
    "vision_footnote",
    "page_number",
    "number",
    "header",
    "page_header",
    "footer",
    "page_footer",
    "aside_text",
}

# 不得用于推断栏结构的元素（§4.3）
NON_COLUMN_LABELS = {
    "inline_formula",
    "formula_number",
    "header",
    "page_header",
    "footer",
    "page_footer",
    "aside_text",
    "page_number",
    "number",
    "seal",
    "stamp",
}

# 模型预期输出阅读顺序的框（PaddleOCR-VL 对 text/heading 等文本框给出
# order；table/chart/figure 等区域框不参与编号，顺序由几何位置决定）。
# 覆盖率计算只针对这些框，避免区域框导致误判模型顺序不可用。
ORDER_EXPECTED_LABELS = CONTENT_LABELS - {
    "table",
    "figure",
    "image",
    "picture",
    "chart",
    "graph",
    "caption",
    "figure_title",
    "figure_caption",
    "table_caption",
    "table_title",
    "chart_title",
}


@dataclass
class ReadingOrderDiagnostics:
    """每页阅读顺序诊断（方案 §4.5 / §8）。"""

    method: str = "unknown"  # model | model_with_unordered_tail | geometric_fallback | native_geometric
    model_coverage: float = 0.0  # 有 order 的内容框占比
    duplicate_ratio: float = 0.0  # order 重复占比
    detected_columns: int = 1
    region_count: int = 1
    removed_containers: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "model_coverage": round(self.model_coverage, 3),
            "duplicate_ratio": round(self.duplicate_ratio, 3),
            "detected_columns": self.detected_columns,
            "region_count": self.region_count,
            "removed_containers": self.removed_containers,
            "warnings": self.warnings,
        }


def map_label_to_block_type(label: str) -> str:
    """版面类别 → IR BlockType，未知类别归为 unknown。"""
    key = (label or "").strip().lower()
    return LAYOUT_LABEL_MAP.get(key, "unknown")


def normalize_bbox(box: list | tuple, page_width: float, page_height: float) -> list[float]:
    """将像素/点坐标转换为归一化 0~1 坐标，并裁剪到 [0,1]。"""
    if not box:
        return []
    x0, y0, x1, y1 = (float(v) for v in box[:4])
    out = [
        max(0.0, min(1.0, x0 / page_width if page_width else 0.0)),
        max(0.0, min(1.0, y0 / page_height if page_height else 0.0)),
        max(0.0, min(1.0, x1 / page_width if page_width else 0.0)),
        max(0.0, min(1.0, y1 / page_height if page_height else 0.0)),
    ]
    return out


def sort_boxes_reading_order(boxes: list[LayoutBox], page_width: float) -> list[LayoutBox]:
    """按阅读顺序排序：列主序 + 通栏块按纵向位置插入（与原生路径一致）。"""
    if not boxes:
        return []

    centers = sorted((b.bbox[0] + b.bbox[2]) / 2 for b in boxes)
    bounds: list[float] = [0.0]
    gap = page_width * 0.18
    clusters: list[list[float]] = [[centers[0]]]
    for c in centers[1:]:
        if c - clusters[-1][-1] > gap:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    for i in range(len(clusters) - 1):
        bounds.append((clusters[i][-1] + clusters[i + 1][0]) / 2)
    bounds.append(page_width)

    def col_of(b: LayoutBox) -> int:
        cx = (b.bbox[0] + b.bbox[2]) / 2
        for i in range(len(bounds) - 1):
            if bounds[i] <= cx <= bounds[i + 1]:
                return i
        return len(bounds) - 1

    span_min = page_width * 0.6
    span_blocks = [b for b in boxes if (b.bbox[2] - b.bbox[0]) >= span_min]
    col_blocks = [b for b in boxes if (b.bbox[2] - b.bbox[0]) < span_min]
    col_blocks.sort(key=lambda b: (col_of(b), b.bbox[1]))
    span_blocks.sort(key=lambda b: b.bbox[1])

    out: list[LayoutBox] = list(col_blocks)
    for sb in span_blocks:
        insert_at = 0
        for i, cb in enumerate(out):
            if cb.bbox[1] > sb.bbox[1] + 1e-6:
                insert_at = i
                break
        else:
            insert_at = len(out)
        out.insert(insert_at, sb)
    return out


def sort_boxes_reading_order(boxes: list[LayoutBox], page_width: float) -> list[LayoutBox]:
    """兼容入口：切换到新的分区式几何回退实现（方案 §9.1）。"""
    return _geometric_fallback(boxes, page_width, ReadingOrderDiagnostics())


# ===========================================================================
# 阅读顺序决策（READING_ORDER_TECHNICAL_DESIGN.md）
# ===========================================================================

# 模型顺序有效性阈值（方案 §7.4）
MODEL_ORDER_MIN_COVERAGE = 0.80
MODEL_ORDER_MAX_DUPLICATE_RATIO = 0.05
# 几何回退：跨栏块宽度阈值（§7.6.2）
SPAN_WIDTH_RATIO = 0.62
# 几何回退：栏检测候选宽度范围（§7.6.1）
COLUMN_CANDIDATE_MIN_WIDTH = 0.10
COLUMN_CANDIDATE_MAX_WIDTH = 0.58
COLUMN_CANDIDATE_MIN_HEIGHT = 0.008
# 沟槽检测（§7.6.3）
GUTTER_X_RANGE = (0.35, 0.65)
GUTTER_MIN_WIDTH = 0.03
GUTTER_MIN_SIDE_BLOCKS = 2
GUTTER_MIN_SIDE_COVERAGE = 0.20


def dedupe_containers(boxes: list[LayoutBox], diag: ReadingOrderDiagnostics) -> list[LayoutBox]:
    """父级结构框去重（方案 §7.3）。

    如果一个 ``reference`` 父框包含两个或以上 ``reference_content`` 子框，
    则父框视为结构容器：删除，子框保留输出。
    """
    kept: list[LayoutBox] = []
    removed_ids: set[int] = set()
    containers = [b for b in boxes if b.label in CONTAINER_LABELS]
    for parent in containers:
        children = [
            b
            for b in boxes
            if b.label == "reference_content"
            and b is not parent
            and _bbox_contains(parent.bbox, b.bbox)
        ]
        if len(children) >= 2:
            removed_ids.add(id(parent))
            diag.removed_containers += 1
    for b in boxes:
        if id(b) not in removed_ids:
            kept.append(b)
    return kept


def _bbox_contains(outer: list[float], inner: list[float]) -> bool:
    if len(outer) < 4 or len(inner) < 4:
        return False
    return (
        outer[0] <= inner[0] + 1e-6
        and outer[1] <= inner[1] + 1e-6
        and outer[2] >= inner[2] - 1e-6
        and outer[3] >= inner[3] - 1e-6
    )


def model_order_valid(
    boxes: list[LayoutBox], diag: ReadingOrderDiagnostics
) -> bool:
    """模型顺序有效性检查（方案 §7.4）：覆盖率与重复率。

    覆盖率分母只统计模型预期输出 order 的框（ORDER_EXPECTED_LABELS）；
    table/chart/figure 等区域框无 order 属正常，不拉低覆盖率。
    """
    expected = [b for b in boxes if b.label in ORDER_EXPECTED_LABELS]
    ordered = [b for b in expected if b.order is not None]
    diag.model_coverage = len(ordered) / len(expected) if expected else 1.0
    if ordered:
        orders = [b.order for b in ordered]
        dup = len(orders) - len(set(orders))
        diag.duplicate_ratio = dup / len(orders)
    return (
        diag.model_coverage >= MODEL_ORDER_MIN_COVERAGE
        and diag.duplicate_ratio <= MODEL_ORDER_MAX_DUPLICATE_RATIO
    )


# 由 resolve_reading_order 直接删除的辅助元素（orchestrator 的 DROP 规则对齐）
_DROP_IN_RESOLVE = {
    "header", "page_header", "footer", "page_footer",
    "page_number", "number", "seal", "stamp",
}
# 追加到页尾的辅助元素
_TAIL_APPEND_LABELS = {"footnote", "vision_footnote", "aside_text"}


def _sort_by_position(boxes: list[LayoutBox]) -> list[LayoutBox]:
    return sorted(boxes, key=lambda b: (b.bbox[1], b.bbox[0]))


def resolve_reading_order(
    boxes: list[LayoutBox],
    page_width: float = 1.0,
    mode: str = "model-first",
) -> tuple[list[LayoutBox], ReadingOrderDiagnostics]:
    """阅读顺序决策入口（方案 §8）。

    流程：父框去重 → 模型顺序有效性判定 → 采用模型顺序 或 分区式几何回退
    → 无顺序辅助元素追加页尾。

    ``mode``：``model-first``（默认）或 ``legacy-geometric``（回滚开关，
    方案 §13）。
    """
    diag = ReadingOrderDiagnostics()
    boxes = dedupe_containers(boxes, diag)

    valid = model_order_valid(boxes, diag) and mode != "legacy-geometric"

    if valid:
        diag.method = "model"
        result = _apply_model_order(boxes, diag)
    else:
        if mode != "legacy-geometric":
            diag.warnings.append(
                f"模型顺序不可用（覆盖率 {diag.model_coverage:.2f}，"
                f"重复率 {diag.duplicate_ratio:.2f}），启用几何回退"
            )
        diag.method = "geometric_fallback"
        result = _geometric_fallback(boxes, page_width, diag)
    return result, diag


def _apply_model_order(boxes: list[LayoutBox], diag: ReadingOrderDiagnostics) -> list[LayoutBox]:
    """采用模型顺序（方案 §7.5）。

    - 有序内容框按 order 排序。
    - 页眉/页脚/页码等辅助元素删除。
    - 无 order 的内容框（表格/图表/图注等，模型不给编号）按 y 位置
      插入有序序列，保持版面相对位置。
    - 脚注、页边注等辅助元素追加页尾（内部按 (y, x) 排序）。
    """
    ordered = sorted(
        [b for b in boxes if b.order is not None],
        key=lambda b: (b.order, b.bbox[1], b.bbox[0]),
    )
    tail: list[LayoutBox] = []
    warnings: list[str] = []
    for b in boxes:
        if b.order is not None:
            continue
        if b.label in _DROP_IN_RESOLVE:
            continue  # 由现有过滤规则删除
        if b.label in _TAIL_APPEND_LABELS:
            tail.append(b)
        elif b.label in CONTENT_LABELS:
            # 无顺序内容框：按 y 插入序列（表格/图表/图注保持在正文位置）
            insert_at = len(ordered)
            for i, ob in enumerate(ordered):
                if ob.bbox[1] > b.bbox[1] + 1e-6:
                    insert_at = i
                    break
            ordered.insert(insert_at, b)
        else:
            tail.append(b)
            warnings.append(f"无顺序元素追加页尾：{b.label}")
    diag.warnings.extend(warnings[:10])
    if tail:
        tail.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        if diag.method == "model":
            diag.method = "model_with_unordered_tail"
        result = ordered + tail
    else:
        result = ordered
    return result


# ---------------------------------------------------------------------------
# 分区式几何回退（方案 §7.6）
# ---------------------------------------------------------------------------


def _column_candidates(boxes: list[LayoutBox]) -> list[LayoutBox]:
    """清理用于栏检测的候选框（§7.6.1）。"""
    out = []
    for b in boxes:
        if b.label in NON_COLUMN_LABELS or b.label in CONTAINER_LABELS:
            continue
        if len(b.bbox) < 4:
            continue
        w = b.bbox[2] - b.bbox[0]
        h = b.bbox[3] - b.bbox[1]
        if not (COLUMN_CANDIDATE_MIN_WIDTH <= w <= COLUMN_CANDIDATE_MAX_WIDTH):
            continue
        if h < COLUMN_CANDIDATE_MIN_HEIGHT:
            continue
        out.append(b)
    return out


def _detect_gutter(boxes: list[LayoutBox], page_width: float) -> float | None:
    """在页面中央区域寻找稳定低密度沟槽（§7.6.3），返回沟槽中点 x 或 None。"""
    if not boxes:
        return None
    nbins = 50
    bins = [0.0] * nbins
    for b in boxes:
        x0 = max(0, int(b.bbox[0] * nbins))
        x1 = min(nbins - 1, int(b.bbox[2] * nbins))
        for i in range(x0, x1 + 1):
            bins[i] += 1.0
    # 中央沟槽搜索：连续 bin 覆盖为 0（或远低于均值）
    mean = sum(bins) / nbins
    low_threshold = max(0.2, mean * 0.3)
    lo, hi = int(GUTTER_X_RANGE[0] * nbins), int(GUTTER_X_RANGE[1] * nbins)
    best_start, best_len = -1, 0
    i = lo
    while i <= hi:
        if bins[i] <= low_threshold:
            j = i
            while j <= hi and bins[j] <= low_threshold:
                j += 1
            if j - i > best_len:
                best_len, best_start = j - i, i
            i = j
        else:
            i += 1
    if best_len <= 0:
        return None
    gutter_width = best_len / nbins
    if gutter_width < GUTTER_MIN_WIDTH:
        return None
    mid = (best_start + best_len / 2) / nbins
    left = [b for b in boxes if (b.bbox[0] + b.bbox[2]) / 2 < mid]
    right = [b for b in boxes if (b.bbox[0] + b.bbox[2]) / 2 >= mid]
    if len(left) < GUTTER_MIN_SIDE_BLOCKS or len(right) < GUTTER_MIN_SIDE_BLOCKS:
        return None
    left_w = max(b.bbox[2] for b in left) - min(b.bbox[0] for b in left) if left else 0
    right_w = max(b.bbox[2] for b in right) - min(b.bbox[0] for b in right) if right else 0
    if left_w < GUTTER_MIN_SIDE_COVERAGE or right_w < GUTTER_MIN_SIDE_COVERAGE:
        return None
    return mid


def _sort_region(region: list[LayoutBox], page_width: float, diag: ReadingOrderDiagnostics) -> list[LayoutBox]:
    """区域内排序：沟槽检测双栏，否则单栏（§7.6.4）。"""
    candidates = _column_candidates(region)
    gutter_mid = _detect_gutter(candidates, page_width)
    if gutter_mid is None:
        return _sort_by_position(region)
    left = sorted(
        (b for b in region if (b.bbox[0] + b.bbox[2]) / 2 < gutter_mid),
        key=lambda b: b.bbox[1],
    )
    right = sorted(
        (b for b in region if (b.bbox[0] + b.bbox[2]) / 2 >= gutter_mid),
        key=lambda b: b.bbox[1],
    )
    if diag.detected_columns < 2:
        diag.detected_columns = 2
    return left + right


def _geometric_fallback(
    boxes: list[LayoutBox], page_width: float, diag: ReadingOrderDiagnostics
) -> list[LayoutBox]:
    """分区式几何回退（§7.6）。

    1. 识别跨栏块，按 y 把页面切成垂直分区；
    2. 分区内沟槽检测列并排序；
    3. 跨栏块作为子分隔插入（§7.6.5）。
    """
    kept = [b for b in boxes if b.label not in _DROP_IN_RESOLVE]
    # 辅助元素（行内公式、公式编号等）不参与列检测与区域排序，
    # 追加到页面末尾（方案 §4.3 / §7.5 保守策略）
    aux = [b for b in kept if b.label in NON_COLUMN_LABELS]
    main = [b for b in kept if b.label not in NON_COLUMN_LABELS]
    candidates = _column_candidates(main)
    span_width = page_width * SPAN_WIDTH_RATIO
    span_blocks = [
        b
        for b in main
        if (b.bbox[2] - b.bbox[0]) >= span_width
        or (b.label in ("doc_title", "title", "document_title") and (b.bbox[2] - b.bbox[0]) >= page_width * 0.4)
    ]
    non_span = [b for b in main if b not in span_blocks]

    # 按 y 切分区域：跨栏块把页面切成多个垂直分区
    all_by_y = sorted(main, key=lambda b: b.bbox[1])
    regions: list[list[LayoutBox]] = []
    current: list[LayoutBox] = []
    for b in all_by_y:
        if b in span_blocks:
            if current:
                regions.append(current)
                current = []
            regions.append([b])  # 跨栏块独占一个"区域"，稍后作为分隔插入
        else:
            current.append(b)
    if current:
        regions.append(current)

    # 交替组装：非跨栏区域排序 + 跨栏块保持 y 位置
    result: list[LayoutBox] = []
    for region in regions:
        if len(region) == 1 and region[0] in span_blocks:
            result.append(region[0])
        else:
            result.extend(_sort_region(region, page_width, diag))
    aux.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
    result.extend(aux)
    diag.region_count = max(1, len(regions))
    return result
