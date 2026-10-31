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
