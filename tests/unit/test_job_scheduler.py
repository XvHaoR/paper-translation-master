"""任务去重和单消费者 GPU 队列测试。"""

from __future__ import annotations

import threading
import time

from app.config import Settings
from app.parsing.native_pdf import NativePdfExtractor
from app.pipeline.orchestrator import ConversionResult, ConvertOptions
from app.server import JobStore


class _BlockingOrchestrator:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def convert(self, pdf_path, options, progress=None):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        if progress:
            progress("recognizing", 1, 1, "test")
        self.release.wait(timeout=5)
        with self._lock:
            self.active -= 1
        return ConversionResult(
            ok=True,
            document_id=NativePdfExtractor.content_hash(pdf_path),
            status="ready",
            duration_ms=1,
        )


def _wait_for_state(store: JobStore, job_id: str, states: set[str]) -> str:
    deadline = time.time() + 5
    while time.time() < deadline:
        state = store.get(job_id)["state"]
        if state in states:
            return state
        time.sleep(0.01)
    return store.get(job_id)["state"]


def test_duplicate_pdf_coalesces_to_one_job(tmp_path):
    settings = Settings(workspace=tmp_path / "workspace")
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.7 same content")
    second_pdf.write_bytes(first_pdf.read_bytes())
    orch = _BlockingOrchestrator()
    store = JobStore(settings, orch)
    opts = ConvertOptions(ocr_mode="auto")

    first_id = store.create(first_pdf, opts)
    assert orch.started.wait(timeout=2)
    second_id = store.create(second_pdf, opts)
    assert second_id == first_id

    orch.release.set()
    assert _wait_for_state(store, first_id, {"ready"}) == "ready"
    assert orch.calls == 1


def test_different_pdfs_are_serialized_by_single_worker(tmp_path):
    settings = Settings(workspace=tmp_path / "workspace")
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.7 first")
    second_pdf.write_bytes(b"%PDF-1.7 second")
    orch = _BlockingOrchestrator()
    store = JobStore(settings, orch)

    first_id = store.create(first_pdf, ConvertOptions(ocr_mode="auto"))
    assert orch.started.wait(timeout=2)
    second_id = store.create(second_pdf, ConvertOptions(ocr_mode="auto"))
    assert store.get(second_id)["state"] == "queued_gpu"
    assert orch.calls == 1
    assert orch.max_active == 1

    orch.release.set()
    assert _wait_for_state(store, first_id, {"ready"}) == "ready"
    assert _wait_for_state(store, second_id, {"ready"}) == "ready"
    assert orch.calls == 2
    assert orch.max_active == 1


def test_every_completed_force_request_runs_again(tmp_path):
    settings = Settings(workspace=tmp_path / "workspace")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7 force fixture")
    orch = _BlockingOrchestrator()
    orch.release.set()
    store = JobStore(settings, orch)
    opts = ConvertOptions(force=True)
    first = store.create(pdf, opts)
    assert _wait_for_state(store, first, {"ready"}) == "ready"
    second = store.create(pdf, opts)
    assert second != first
    assert _wait_for_state(store, second, {"ready"}) == "ready"
    assert orch.calls == 2


def test_cancelled_active_job_cannot_publish_success_and_retry_is_new(tmp_path):
    settings = Settings(workspace=tmp_path / "workspace")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7 cancel fixture")
    orch = _BlockingOrchestrator()
    store = JobStore(settings, orch)
    first = store.create(pdf)
    assert orch.started.wait(2)
    assert store.cancel(first)
    second = store.create(pdf)
    assert second != first
    orch.release.set()
    assert _wait_for_state(store, second, {"ready"}) == "ready"
    assert store.get(first)["state"] == "cancelled"
    assert orch.max_active == 1
