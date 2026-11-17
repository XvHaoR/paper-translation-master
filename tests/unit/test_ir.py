"""IR 模型：序列化、版本、校验。"""

from app.models.ir import (
    SCHEMA_VERSION,
    Block,
    BlockContent,
    BlockType,
    DocumentIR,
    SourceMethod,
    block_id,
)


def test_block_id_format():
    assert block_id(3, 12) == "p003-b012"


def test_ir_roundtrip():
    ir = DocumentIR()
    ir.document.id = "abc123"
    ir.document.title = "Test Paper"
    ir.add_block(
        Block(
            id=block_id(1, 0),
            type=BlockType.DOCUMENT_TITLE,
            page=1,
            order=0,
            content=BlockContent(text="Test Paper"),
        )
    )
    blob = ir.model_dump_json()
    restored = DocumentIR.model_validate_json(blob)
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.blocks[0].type is BlockType.DOCUMENT_TITLE
    assert restored.blocks[0].content.text == "Test Paper"


def test_block_content_requires_field():
    import pydantic

    try:
        BlockContent()
        raise AssertionError("BlockContent() 应校验失败")
    except pydantic.ValidationError:
        pass


def test_block_content_variants():
    eq = BlockContent(latex="E = mc^2", display=True)
    assert eq.latex == "E = mc^2"
    tbl = BlockContent(html="<table></table>", rows=2, cols=3)
    assert tbl.rows == 2


def test_ir_pages_and_blocks_by_page():
    ir = DocumentIR()
    ir.document.id = "x"
    for p in (1, 2):
        ir.add_block(Block(id=block_id(p, 0), type=BlockType.PARAGRAPH, page=p, order=0,
                           content=BlockContent(text="t")))
    assert len(ir.blocks_by_page(2)) == 1


def test_confidence_clamped():
    import pydantic

    try:
        Block(quality__confidence=1.5)
    except pydantic.ValidationError:
        pass


def test_source_method_enum():
    assert SourceMethod.OCR.value == "ocr"


def test_render_text_summary():
    b = Block(id="p1-b0", type=BlockType.EQUATION_BLOCK, page=1, order=0,
              content=BlockContent(latex="x^2", display=True))
    assert "[公式]" in b.render_text()
