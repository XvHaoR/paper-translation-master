"""Download official models and verify the same PDF pipeline Zotero will use."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report_path = Path(args.report)
    report_path.unlink(missing_ok=True)
    start = time.monotonic()
    if os.environ.get("PAPERBRIDGE_OCR_MODE") == "rapidocr":
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from app.parsing.rapidocr_backend import RapidDocumentRecognizer
        from app.pipeline.orchestrator import Orchestrator, ConvertOptions
        import fitz
        print("正在加载 RapidOCR CPU 模型并识别真实扫描 PDF…", flush=True)
        with tempfile.TemporaryDirectory(prefix="rapid-check-") as directory:
            directory = Path(directory)
            digital = fitz.open()
            page = digital.new_page(width=600, height=780)
            page.insert_text((35, 70), "Paper OCR Ready 12345", fontsize=28)
            page.insert_text((35, 130), "This document verifies local OCR installation.", fontsize=16)
            page.insert_text((240, 240), "E = mc", fontsize=28, fontname="tiro")
            page.insert_text((324, 225), "2", fontsize=17, fontname="tiro")
            page.insert_text((35, 345), "Table 1. Recognition check", fontsize=16)
            for x in (35, 205, 375, 545):
                page.draw_line((x, 370), (x, 550))
            for y in (370, 415, 460, 505, 550):
                page.draw_line((35, y), (545, y))
            for row, cells in enumerate((("Model", "Score", "Count"), ("Alpha", "95.2", "100"), ("Beta", "98.3", "200"), ("Gamma", "99.1", "300"))):
                for col, text in enumerate(cells):
                    page.insert_text((48+170*col, 400+45*row), text, fontsize=15)
            pixmap = page.get_pixmap(dpi=180)
            scan = fitz.open()
            scan.new_page(width=600, height=780).insert_image(page.rect, stream=pixmap.tobytes("png"))
            source = directory / "scanned.pdf"
            scan.save(source)
            scan.close()
            digital.close()
            result = Orchestrator(directory / "workspace").convert(source, ConvertOptions(ocr_mode="rapidocr"))
            text = " ".join(block.content.text or "" for block in (result.ir.blocks if result.ir else []))
            if not result.ok or "paperocrready12345" not in re.sub(r"[^a-z0-9]", "", text.lower()):
                raise RuntimeError("RapidOCR 扫描 PDF 自检失败：" + "; ".join(result.errors))
            formulas = [b for b in result.ir.blocks if b.content.latex]
            tables = [b for b in result.ir.blocks if b.content.html and "<table" in b.content.html]
            if not formulas or not tables:
                raise RuntimeError("完整模型自检失败：公式或表格识别未生效，不能把纯文字识别标记为安装成功")
            table_markup = " ".join(b.content.html for b in tables)
            if "Alpha" not in table_markup or "95.2" not in table_markup:
                raise RuntimeError("表格结构自检失败：识别的单元格未包含预期数据")
        report_path.write_text(json.dumps({"ok": True, "gpu": False, "pipeline": "RapidOCR",
            "rapid_doc": RapidDocumentRecognizer().version, "formulas": len(formulas), "tables": len(tables),
            "seconds": round(time.monotonic()-start, 1), "testedAt": time.time()}), encoding="utf-8")
        print("RapidOCR CPU 扫描 PDF 自检通过。", flush=True)
        return
    print("正在检查 GPU 推理运行库…", flush=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.parsing.paddleocr_vl import PaddleOcrVlParser
    PaddleOcrVlParser._add_cuda_dll_dirs()
    import paddle
    if not paddle.is_compiled_with_cuda() or paddle.device.cuda.device_count() < 1:
        raise RuntimeError("GPU 推理不可用，请检查 NVIDIA 驱动；安装器不会回退到 CPU 后称为成功")
    paddle.set_device("gpu:0")
    paddle.utils.run_check()

    import fitz
    with tempfile.TemporaryDirectory(prefix="ocr-check-") as directory:
        directory = Path(directory)
        document = fitz.open()
        page = document.new_page(width=600, height=320)
        page.insert_text((35, 70), "Paper OCR Ready 12345", fontsize=28)
        page.insert_text((35, 130), "This document verifies local OCR installation.", fontsize=16)
        source = directory / "self-test.pdf"
        document.save(source)
        document.close()
        print("正在下载/加载官方布局与文字模型；下载字节进度会显示在下方…", flush=True)
        pipeline = PaddleOcrVlParser(device="gpu")
        pages = pipeline.recognize_document(source, directory / "output")
        text = "\n".join(page.markdown for page in pages)
        normalized = re.sub(r"[^a-z0-9]", "", text.lower())
        if "paperocrready12345" not in normalized:
            raise RuntimeError("实际 PDF 识别自检未通过，返回内容缺少预期文字，请检查 install.log")
    report = {"ok": True, "gpu": True, "pipeline": "PaddleOCR-VL-1.6", "paddle": paddle.__version__,
              "seconds": round(time.monotonic() - start, 1), "testedAt": time.time()}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("官方模型实际 PDF 识别自检通过。", flush=True)


if __name__ == "__main__":
    main()
