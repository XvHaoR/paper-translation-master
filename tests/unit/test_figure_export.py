import fitz
from PIL import Image

from app.models.ir import Block, BlockContent, BlockType
from app.services.figure_export import export_figures


def panel(key, box, order):
    return Block(id=key, type=BlockType.FIGURE, page=1, order=order,
                 bbox=box, content=BlockContent(image_path="assets/imgs/"))


def caption(key, text, box, order):
    return Block(id=key, type=BlockType.CAPTION, page=1, order=order,
                 bbox=box, content=BlockContent(text=text))


def test_composite_figure_keeps_spatial_arrangement_labels_and_one_caption(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=400)
    for index, color in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1)]):
        x = 40 + index * 180
        page.draw_rect(fitz.Rect(x, 60, x + 140, 170), fill=color)
        page.insert_text((x, 50), f"({chr(97 + index)})")
    page.insert_text((40, 195), "Common legend for all three panels")
    pdf.save(root / "original.pdf")
    pdf.close()
    blocks = [panel("a", [.067, .15, .3, .425], 1),
              panel("b", [.367, .15, .6, .425], 2),
              panel("c", [.667, .15, .9, .425], 3),
              caption("legend", "Common legend", [.067, .46, .85, .49], 4),
              Block(id="scale", type=BlockType.EQUATION_INLINE, page=1, order=4,
                    bbox=[.4, .45, .5, .49], content=BlockContent(latex="R")),
              caption("caption", "Figure 1. Three panels.", [.06, .53, .94, .58], 5)]
    out = tmp_path / "output"
    exported = export_figures(blocks, {}, root, out)
    assert [b.type for b in exported] == [BlockType.FIGURE, BlockType.CAPTION]
    assert exported[-1].id == "caption"
    image = Image.open(out / exported[0].content.image_path)
    assert image.width / image.height > 3
    colors = image.getdata()
    assert all(any(pixel[channel] > 200 and sum(pixel) < 330 for pixel in colors) for channel in range(3))


def test_each_legacy_panel_maps_to_its_own_asset_and_never_repeats_other_panels(tmp_path):
    root = tmp_path / "source"
    images = root / "assets" / "imgs"
    images.mkdir(parents=True)
    for name, color in [("img_in_chart_box_10_20_40_50.png", "red"),
                        ("img_in_chart_box_50_20_90_50.png", "blue")]:
        Image.new("RGB", (20, 20), color).save(images / name)
    pdf = fitz.open()
    pdf.new_page(width=100, height=100)
    pdf.save(root / "original.pdf")
    pdf.close()
    blocks = [panel("a", [.1, .2, .4, .5], 1),
              caption("caption1", "Figure 1. Independent A", [.1, .52, .4, .6], 2),
              panel("b", [.5, .2, .9, .5], 3),
              caption("caption2", "Figure 2. Independent B", [.5, .52, .9, .6], 4)]
    output = tmp_path / "output"
    exported = export_figures(blocks, {"pages": [{"number": 1, "width": 100, "height": 100}]}, root, output)
    figures = [b for b in exported if b.type is BlockType.FIGURE]
    assert len(figures) == 2
    assert Image.open(output / figures[0].content.image_path).getpixel((0, 0)) == (255, 0, 0)
    assert Image.open(output / figures[1].content.image_path).getpixel((0, 0)) == (0, 0, 255)
