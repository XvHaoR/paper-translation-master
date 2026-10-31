"""生成测试用 PDF fixture（tests/fixtures/*.pdf）。

- sample_native.pdf：3 页原生文本论文（标题/摘要/双栏正文/公式/表格/参考文献/图片）
- sample_scanned.pdf：渲染为图像的无文本 PDF（模拟扫描件，供 OCR 测试）
"""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image, ImageDraw

FIXTURES = Path(__file__).resolve().parent

W, H = 595, 842  # A4 pt


def build_native_pdf(path: Path) -> None:
    doc = fitz.open()

    # 第 1 页：标题 + 摘要 + 双栏正文
    p = doc.new_page(width=W, height=H)
    p.insert_text((W / 2 - 170, 60), "A Benchmark Study of Formula-Aware Document Reading",
                  fontsize=18, fontname="hebo")
    p.insert_text((W / 2 - 120, 85), "Alice Zhang and Bob Li", fontsize=12, fontname="helv")
    p.insert_text((60, 120), "Abstract", fontsize=13, fontname="hebo")
    p.insert_textbox(
        fitz.Rect(60, 130, W - 60, 200),
        "We present a benchmark for formula-aware document reading. The proposed method "
        "combines native text extraction with vision-language recognition, achieving high "
        "fidelity on equations and tables while preserving paragraph structure for machine "
        "translation.",
        fontsize=10,
    )
    col1 = fitz.Rect(60, 220, 290, 800)
    col2 = fitz.Rect(320, 220, 535, 800)
    text1 = (
        "1. Introduction\n\n"
        "Recent advances in vision-language models have improved document parsing. However, "
        "most systems still struggle with reading order in multi-column layouts. In this work, "
        "we study how to preserve the semantic structure of academic papers when converting "
        "PDF to HTML. Our key insight is that translation quality depends on paragraph-level "
        "DOM structure rather than pixel fidelity. We evaluate the approach on a corpus of 20 "
        "papers across computer science and physics.\n\n"
        "The rest of this paper is organized as follows. Section 2 describes the pipeline. "
        "Section 3 reports experimental results on formulas and tables. Section 4 discusses "
        "limitations and future work."
    )
    text2 = (
        "2. Method\n\n"
        "Our pipeline first performs preflight checks to detect whether a page contains native "
        "text. Pages with insufficient text are routed to a full OCR pipeline. For native pages, "
        "we extract embedded text directly and analyze the layout to locate formulas and tables. "
        "Formula regions are cropped and recognized into LaTeX, which is rendered with MathJax "
        "in the final HTML.\n\n"
        "3. Results\n\n"
        "We found that native text extraction achieves a character accuracy of 99.2 percent. "
        "Formula recognition reached 96 percent accuracy on the test set. The reading order was "
        "correct for 97 percent of multi-column pages."
    )
    p.insert_textbox(col1, text1, fontsize=10, fontname="helv")
    p.insert_textbox(col2, text2, fontsize=10, fontname="helv")

    # 第 2 页：公式 + 表格 + 参考文献
    p2 = doc.new_page(width=W, height=H)
    p2.insert_text((60, 60), "4. Equations", fontsize=14, fontname="hebo")
    p2.insert_textbox(
        fitz.Rect(60, 70, W - 60, 140),
        "The energy-momentum relation is given by the following equation, which is central to "
        "relativistic mechanics and appears in many physics papers.",
        fontsize=10,
    )
    p2.draw_rect(fitz.Rect(180, 150, 420, 180), color=(0, 0, 0), width=1)
    p2.insert_text((210, 168), "E^2 = (pc)^2 + (mc^2)^2", fontsize=12, fontname="cour")
    p2.insert_text((60, 210), "5. Tables", fontsize=14, fontname="hebo")
    p2.insert_textbox(
        fitz.Rect(60, 220, W - 60, 260),
        "Table 1 summarizes the benchmark results across the three methods.",
        fontsize=10,
    )
    rows = [
        ("Method", "Text Acc", "Formula Acc", "Order"),
        ("Ours", "99.2%", "96.0%", "97%"),
        ("Baseline A", "97.5%", "91.0%", "90%"),
        ("Baseline B", "96.1%", "88.5%", "86%"),
    ]
    y = 270
    for r in rows:
        x = 80
        for c in r:
            p2.insert_text((x, y), c, fontsize=10, fontname="cour")
            x += 110
        y += 18
    p2.insert_text((60, y + 30), "6. References", fontsize=14, fontname="hebo")
    refs = [
        "[1] Smith J, et al. Formula-aware document parsing. In Proc. ACL 2025.",
        "[2] Doe A. Reading order recovery for two-column layouts. arXiv:2601.00001.",
        "[3] Chen X. MathJax rendering in translated documents. 2024.",
    ]
    yy = y + 55
    for r in refs:
        p2.insert_text((60, yy), r, fontsize=9, fontname="helv")
        yy += 16

    # 第 3 页：图片
    p3 = doc.new_page(width=W, height=H)
    p3.insert_text((60, 60), "7. Figures", fontsize=14, fontname="hebo")
    p3.insert_textbox(
        fitz.Rect(60, 70, W - 60, 110),
        "Figure 1 shows the overall pipeline of the proposed system.",
        fontsize=10,
    )
    p3.draw_rect(fitz.Rect(150, 120, 450, 320), color=(0.2, 0.2, 0.8), width=0, fill=(0.85, 0.9, 1))
    p3.insert_text((200, 220), "Figure 1: Pipeline diagram", fontsize=11, fontname="helv")
    fig_png = FIXTURES / "_fig.png"
    p3.insert_image(fitz.Rect(150, 340, 350, 460), filename=str(fig_png))

    doc.save(str(path))
    doc.close()


def build_scanned_pdf(path: Path) -> None:
    """把样例文本渲染成页面图像再组 PDF，模拟扫描件（无文本层）。"""
    doc = fitz.open()
    tmp = FIXTURES / "_scan_tmp.pdf"
    build_native_pdf(tmp)
    src = fitz.open(str(tmp))
    for i in range(src.page_count):
        pix = src[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        jpg = FIXTURES / "_scan_page.jpg"
        pix.save(str(jpg), "jpeg", jpg_quality=80)
        page = doc.new_page(width=W, height=H)
        page.insert_image(fitz.Rect(0, 0, W, H), filename=str(jpg))
    src.close()
    tmp.unlink(missing_ok=True)
    jpg.unlink(missing_ok=True)
    doc.save(str(path), deflate=True)
    doc.close()


def build_fig_png() -> None:
    img = Image.new("RGB", (200, 120), (200, 200, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([60, 30, 140, 90], fill=(136, 136, 204))
    d.rectangle([20, 20, 60, 100], fill=(80, 80, 160))
    img.save(str(FIXTURES / "_fig.png"))


if __name__ == "__main__":
    build_fig_png()
    build_native_pdf(FIXTURES / "sample_native.pdf")
    build_scanned_pdf(FIXTURES / "sample_scanned.pdf")
    print("fixtures ready")
