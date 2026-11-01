"""Installer regressions without installing Python, drivers, models, or WSL."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import threading
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ocr_setup_core", ROOT / "packaging/installer/core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)


def test_extract_rejects_path_escape_before_writing(tmp_path):
    package = tmp_path / "payload.zip"
    write_zip(package, {"okay.txt": "ok", "../escape": "bad"})
    with pytest.raises(core.InstallError, match="无效路径"):
        core.safe_extract(package, tmp_path / "output")
    assert not (tmp_path / "output/okay.txt").exists()
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("path", ["C:/escape", "/escape", "..\\escape"])
def test_extract_rejects_windows_absolute_and_backslash_paths(tmp_path, path):
    package = tmp_path / "payload.zip"
    write_zip(package, {path: "bad"})
    with pytest.raises(core.InstallError):
        core.safe_extract(package, tmp_path / "output")


class Response(io.BytesIO):
    def __init__(self, data, status=200, **headers):
        super().__init__(data)
        self.status = status
        self.headers = headers


def test_download_resumes_and_checks_hash(tmp_path, monkeypatch):
    target = tmp_path / "file.zip"
    target.with_suffix(".zip.part").write_bytes(b"abc")
    seen = []
    def open_(request, timeout):
        seen.append(request.get_header("Range"))
        return Response(b"def", 206, **{"Content-Range": "bytes 3-5/6", "Content-Length": "3"})
    monkeypatch.setattr(core.urllib.request, "urlopen", open_)
    expected = hashlib.sha256(b"abcdef").hexdigest()
    core.download("https://official.example/file", target, expected, lambda _: None, threading.Event())
    assert seen == ["bytes=3-"]
    assert target.read_bytes() == b"abcdef"
    core.download("https://official.example/file", target, expected, lambda _: None, threading.Event())
    assert len(seen) == 1  # Fully verified file needs no request.


def test_download_server_without_range_replaces_partial(tmp_path, monkeypatch):
    target = tmp_path / "file.zip"
    target.with_suffix(".zip.part").write_bytes(b"partial")
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: Response(b"complete", 200))
    core.download("https://official.example/file", target, hashlib.sha256(b"complete").hexdigest(),
                  lambda _: None, threading.Event())
    assert target.read_bytes() == b"complete"


def test_download_bad_hash_never_promotes(tmp_path, monkeypatch):
    target = tmp_path / "file.zip"
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: Response(b"bad", 200))
    with pytest.raises(core.InstallError, match="校验失败"):
        core.download("https://official.example/file", target, "0" * 64, lambda _: None, threading.Event())
    assert not target.exists()
    assert not target.with_suffix(".zip.part").exists()


def test_cancelled_download_preserves_partial(tmp_path):
    target = tmp_path / "file.zip"
    target.with_suffix(".zip.part").write_bytes(b"partial")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(core.Cancelled):
        core.download("https://official.example/file", target, "0" * 64, lambda _: None, cancel)
    assert target.with_suffix(".zip.part").read_bytes() == b"partial"


def test_gpu_requirements_do_not_silently_fallback_cpu():
    devices = core.parse_gpu("NVIDIA RTX 3060, 12288, 581.80, 8.6\n")
    assert core.gpu_requirement(devices)["name"] == "NVIDIA RTX 3060"
    for output in ("GPU, 4096, 581.80, 8.6", "GPU, 12288, 551.76, 8.6", "GPU, 16384, 581.80, 12.0", ""):
        with pytest.raises(core.InstallError):
            core.gpu_requirement(core.parse_gpu(output))


@pytest.fixture
def installer(tmp_path, monkeypatch):
    base = tmp_path / "private-runtime"
    payload = tmp_path / "runtime.zip"
    write_zip(payload, {"scripts/zotero_service.py": "# no execute", "packaging/installer/requirements-runtime.txt": "paddleocr==3.7.0"})
    release = {"name": "test", "version": "0.3.0"}
    events = []
    instance = core.Installer(base, payload, release, lambda *args: events.append(args))
    base.mkdir()
    monkeypatch.setattr(core, "windows_x64", lambda: True)
    monkeypatch.setattr(core.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(core.platform, "version", lambda: "10.0.26100")
    monkeypatch.setattr(core.subprocess, "run", lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, "NVIDIA RTX 3060, 12288, 581.80, 8.6", ""))
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 30 * 1024 ** 3})())
    def download(_url, target, _sha, _notify, _cancel):
        target.parent.mkdir(parents=True, exist_ok=True)
        write_zip(target, {"python.exe": "fake private interpreter"})
    monkeypatch.setattr(core, "download", download)
    return instance, events


def test_descriptor_written_only_after_actual_gpu_pdf_check(installer, monkeypatch):
    instance, events = installer
    calls = []
    def command(args, **kw):
        assert not (instance.base / "runtime.json").exists()
        calls.append(args)
        if "--report" in args:
            (instance.base / "warmup.json").write_text(json.dumps({"ok": True, "gpu": True}), encoding="utf-8")
    monkeypatch.setattr(instance, "run_command", command)
    descriptor = instance.install()
    assert descriptor["backend"] == "local"
    assert descriptor["schema"] == 1
    assert set(descriptor["environment"]) == set(core.ENV_KEYS)
    assert Path(descriptor["pythonPath"]).is_relative_to(instance.base)
    assert calls[-1][-2] == "--report"
    assert "../engine/scripts" in (instance.python.parent / "python313._pth").read_text(encoding="utf-8")
    assert "import site" not in (instance.python.parent / "python313._pth").read_text(encoding="utf-8").splitlines()
    assert events[-1][0] == "ready"
    assert (instance.base / "runtime.json").is_file()


def test_missing_warmup_report_does_not_reuse_old_success(installer, monkeypatch):
    instance, events = installer
    (instance.base / "warmup.json").write_text('{"ok":true,"gpu":true}', encoding="utf-8")
    monkeypatch.setattr(instance, "run_command", lambda *args, **kwargs: None)
    with pytest.raises(core.InstallError, match="有效报告"):
        instance.install()
    assert not (instance.base / "runtime.json").exists()
    assert events[-1][0] == "failed"


def test_failed_repair_revokes_descriptor_for_half_updated_runtime(installer, monkeypatch):
    instance, events = installer
    (instance.base / "runtime.json").write_text(json.dumps({"projectRoot": str(instance.project)}), encoding="utf-8")
    monkeypatch.setattr(instance, "run_command", lambda *args, **kwargs: None)
    with pytest.raises(core.InstallError):
        instance.install()
    assert not (instance.base / "runtime.json").exists()
    assert (instance.base / "runtime.previous.json").is_file()


def test_cpu_report_is_not_advertised_as_ready(installer, monkeypatch):
    instance, events = installer
    def command(args, **kw):
        if "--report" in args:
            (instance.base / "warmup.json").write_text('{"ok":true,"gpu":false}', encoding="utf-8")
    monkeypatch.setattr(instance, "run_command", command)
    with pytest.raises(core.InstallError, match="实际识别自检"):
        instance.install()
    assert not (instance.base / "runtime.json").exists()


def test_rapid_full_install_needs_no_gpu_but_requires_formula_and_table_self_test(installer, monkeypatch):
    instance, events = installer
    instance.mode = "rapidocr"
    instance.release["ocrMode"] = "rapidocr"
    monkeypatch.setattr(core.subprocess,"run",lambda *a,**kw: pytest.fail("CPU installer must never probe NVIDIA or WSL"))
    calls=[]
    def command(args,**kw):
        calls.append(args)
        if "--report" in args:
            (instance.base/"warmup.json").write_text(json.dumps({"ok":True,"gpu":False,"pipeline":"RapidOCR","formulas":1,"tables":1}))
    monkeypatch.setattr(instance,"run_command",command)
    descriptor=instance.install()
    assert descriptor["ocrMode"] == "rapidocr" and descriptor["gpu"] is None
    assert descriptor["environment"]["PAPERBRIDGE_OCR_MODE"] == "rapidocr"
    assert any(any("requirements-rapidocr.txt" in str(arg) for arg in args) for args in calls)
    assert not any(any("paddlepaddle-gpu" in str(arg) for arg in args) for args in calls)


def test_rapid_text_only_warmup_cannot_mark_a_full_install_successful(installer, monkeypatch):
    instance,_=installer; instance.mode="rapidocr"
    def command(args,**kw):
        if "--report" in args:
            (instance.base/"warmup.json").write_text('{"ok":true,"gpu":false,"pipeline":"RapidOCR"}')
    monkeypatch.setattr(instance,"run_command",command)
    with pytest.raises(core.InstallError,match="实际识别自检"):
        instance.install()
