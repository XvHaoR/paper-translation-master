"""版面策略与阅读顺序。"""

from app.parsing.native_pdf import NativeBlock, NativeLine, detect_columns, sort_blocks_reading_order
from app.parsing.page_strategy import LayoutBox, map_label_to_block_type, normalize_bbox, sort_boxes_reading_order


def _nb(x0, y0, x1, y1, text="t"):
    return NativeBlock(bbox=(x0, y0, x1, y1), lines=[NativeLine(text=text, bbox=(x0, y0, x1, y1), size=10, font="helv")])


def test_detect_columns_two_columns():
    blocks = [_nb(60, 100, 280, 120), _nb(320, 100, 540, 120), _nb(60, 200, 280, 220), _nb(320, 200, 540, 220)]
    cols = detect_columns(blocks, 595)
    assert len(cols) == 3  # [0, mid, 595]


def test_sort_column_major():
    blocks = [
        _nb(320, 100, 540, 120, "right-top"),
        _nb(60, 100, 280, 120, "left-top"),
        _nb(60, 200, 280, 220, "left-bottom"),
        _nb(320, 200, 540, 220, "right-bottom"),
    ]
    ordered = sort_blocks_reading_order(blocks, [0, 300, 595], 595)
    texts = [b.text for b in ordered]
    assert texts == ["left-top", "left-bottom", "right-top", "right-bottom"]


def test_sort_spanning_title_first():
    blocks = [
        _nb(60, 100, 280, 120, "left"),
        _nb(127, 40, 595, 60, "title"),
        _nb(320, 100, 540, 120, "right"),
    ]
    ordered = sort_blocks_reading_order(blocks, [0, 300, 595], 595)
    assert ordered[0].text == "title"


def test_normalize_bbox():
    assert normalize_bbox([0, 0, 200, 100], 400, 200) == [0.0, 0.0, 0.5, 0.5]
    assert normalize_bbox([-10, 0, 800, 300], 400, 200) == [0.0, 0.0, 1.0, 1.0]


def test_map_label():
    assert map_label_to_block_type("equation") == "equation_block"
    assert map_label_to_block_type("table") == "table"
    assert map_label_to_block_type("unknown_label") == "unknown"


def test_sort_boxes_reading_order():
    boxes = [
        LayoutBox(label="paragraph", bbox=[0.5, 0.1, 0.9, 0.2], order=0, text="right"),
        LayoutBox(label="paragraph", bbox=[0.05, 0.1, 0.45, 0.2], order=1, text="left"),
    ]
    ordered = sort_boxes_reading_order(boxes, 1.0)
    assert [b.text for b in ordered] == ["left", "right"]
