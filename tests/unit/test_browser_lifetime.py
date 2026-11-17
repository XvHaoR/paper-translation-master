import json
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.config import Settings
from app.server import create_app
from app.services.browser_sessions import BrowserSessions


def test_sessions_track_last_disconnect_and_reconnect(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.services.browser_sessions.time.monotonic", lambda: now[0])
    sessions = BrowserSessions()
    assert not sessions.status()["seen_browser"]
    sessions.connect("home")
    sessions.connect("reader")
    sessions.disconnect("home")
    now[0] += 100
    assert sessions.status()["clients"] == 1
    assert sessions.status()["idle_seconds"] == 0
    sessions.disconnect("reader")
    now[0] += 31
    assert sessions.status()["idle_seconds"] == 31
    sessions.connect("reload")
    assert sessions.status()["idle_seconds"] == 0


def test_websocket_lifetime_and_existing_document_assets(tmp_path):
    settings = Settings(workspace=tmp_path)
    doc = settings.documents_dir / "sample"
    doc.mkdir()
    (doc / "document.json").write_text(json.dumps({"document": {"id": "sample"}, "blocks": []}))
    (doc / "index.html").write_text("<html><body>old reader</body></html>")
    with TestClient(create_app(settings)) as client:
        assert "/static/runtime.js" in client.get("/").text
        assert "/static/runtime.js" in client.get("/documents/sample/").text
        assert "tr-s-test" in client.get("/documents/sample/assets/reader.js").text
        assert client.get("/static/runtime.js").status_code == 200
        with client.websocket_connect("/api/runtime/browser"):
            with client.websocket_connect("/api/runtime/browser"):
                assert client.get("/api/runtime").json()["clients"] == 2
            assert client.get("/api/runtime").json()["clients"] == 1
        status = client.get("/api/runtime").json()
        assert status["clients"] == 0
        assert status["seen_browser"]


def test_translation_does_not_block_browser_liveness(tmp_path, monkeypatch):
    settings = Settings(workspace=tmp_path)
    doc = settings.documents_dir / "sample"
    doc.mkdir()
    (doc / "document.json").write_text(json.dumps({"document": {"id": "sample"}, "blocks": []}))
    (tmp_path / "settings.json").write_text(json.dumps({"llm_base_url": "http://example.com", "llm_model": "test"}))
    entered, release = threading.Event(), threading.Event()

    def translate(*args):
        entered.set()
        release.wait(5)
        return {"results": {}}

    monkeypatch.setattr("app.services.translator.translate_batch", translate)
    with TestClient(create_app(settings)) as client, ThreadPoolExecutor() as pool:
        translation = pool.submit(client.post, "/api/documents/sample/translate", json={"block_ids": ["b1"]})
        assert entered.wait(2)
        start = time.monotonic()
        try:
            assert client.get("/api/runtime").status_code == 200
            assert time.monotonic() - start < 1
        finally:
            release.set()
        assert translation.result().status_code == 200


def test_launcher_waits_for_last_page_and_reload_grace(monkeypatch):
    from scripts import launcher

    states = iter([
        {"seen_browser": True, "clients": 0, "idle_seconds": 29},
        {"seen_browser": True, "clients": 1, "idle_seconds": 0},
        {"seen_browser": True, "clients": 0, "idle_seconds": 2},
        {"seen_browser": True, "clients": 0, "idle_seconds": 31},
    ])
    calls = []

    def status(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(json.dumps(next(states)).encode())

    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    monkeypatch.setattr(launcher.urllib.request, "urlopen", status)
    launcher.monitor_browser()
    assert len(calls) == 4


def test_launcher_does_not_kill_reused_unrelated_pid(tmp_path, monkeypatch):
    from scripts import launcher
    from types import SimpleNamespace

    (tmp_path / "scripts").mkdir()
    pid_file = tmp_path / "scripts" / "paperloom.pid"
    pid_file.write_text("12345")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=b"python other_program.py", returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.stop_paperloom(tmp_path)
    assert not any(command[0] == "taskkill" for command in calls)
    assert not pid_file.exists()
