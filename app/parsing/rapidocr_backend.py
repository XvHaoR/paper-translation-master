"""Complete RapidAI document recognition with layout, inline/display math and tables."""
from __future__ import annotations
import importlib.metadata
import os
import threading

class RapidDocumentRecognizer:
    def __init__(self, factory=None):
        self._factory, self._engine = factory, None
        self._lock = threading.Lock()

    @property
    def version(self):
        try:
            return importlib.metadata.version("rapid-doc")
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    def _load(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    os.environ["MINERU_DEVICE_MODE"] = "cpu"
                    if root := os.environ.get("PAPERBRIDGE_RAPIDOCR_MODELS"):
                        os.environ["RAPID_MODELS_DIR"] = root
                    try:
                        from rapid_doc import RapidDoc
                        from rapidocr import EngineType
                        from rapid_doc.model.formula.rapid_formula_self import ModelType as FormulaType
                    except ImportError as error:
                        raise RuntimeError("缺少完整 RapidOCR 引擎，请运行 RapidOCR 版安装器（含版面、公式和表格模型）") from error
                    self._engine = (self._factory or RapidDoc)(
                        formula_enable=True, table_enable=True, parse_method="auto", pdf_pages_batch=4,
                        ocr_config={"engine_type": EngineType.ONNXRUNTIME,
                            "EngineConfig.onnxruntime.intra_op_num_threads": min(4, os.cpu_count() or 1),
                            "EngineConfig.onnxruntime.inter_op_num_threads": 1},
                        formula_config={"model_type": FormulaType.PP_FORMULANET_PLUS_M, "formula_level": 0},
                        table_config={"table_formula_enable": True, "table_image_enable": True})
        return self._engine

    def recognize(self, pdf_bytes):
        engine = self._load()
        with self._lock:
            return engine(pdf_bytes, formula_enable=True, table_enable=True,
                          f_dump_middle_json=True, f_dump_content_list=True)
