import json
import socket
import pytest
from types import SimpleNamespace

from scripts import zotero_service


def test_occupied_port_falls_back_without_stopping_existing_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied = listener.getsockname()[1]
        alternative = zotero_service.available_port(occupied)
        assert alternative != occupied
        assert listener.getsockname()[1] == occupied
        with socket.create_connection(("127.0.0.1", occupied), timeout=1):
            connection, _ = listener.accept()
            connection.close()


def test_available_preferred_port_is_preserved():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        preferred = probe.getsockname()[1]
    assert zotero_service.available_port(preferred) == preferred


def test_lease_requires_matching_instance_and_recent_heartbeat(tmp_path):
    lease = tmp_path / "lease.json"
    assert not zotero_service.lease_valid(lease, "one", 100)
    lease.write_text(json.dumps({"instance": "one", "time": 80}))
    assert zotero_service.lease_valid(lease, "one", 100)
    assert not zotero_service.lease_valid(lease, "other", 100)
    assert not zotero_service.lease_valid(lease, "one", 141)
    assert not zotero_service.lease_valid(lease, "one", 40)


def test_reuses_existing_service_without_taking_ownership(tmp_path, monkeypatch):
    lease = tmp_path / "lease.json"
    lease.write_text(json.dumps({"instance": "one", "time": 100}))
    monkeypatch.setattr(zotero_service.time, "time", lambda: 100)
    monkeypatch.setattr(zotero_service, "compatible_service", lambda url: True)
    monkeypatch.setattr(zotero_service, "acquire_lock", lambda root: (_ for _ in ()).throw(AssertionError("no new process")))
    args = SimpleNamespace(state_file=tmp_path / "state.json", lease_file=lease, instance="one", port=8765)
    assert zotero_service.run(args) == 0
    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    assert state["phase"] == "ready" and state["owned"] is False


def test_invalid_lease_fails_before_launching_any_process(tmp_path, monkeypatch):
    monkeypatch.setattr(zotero_service, "compatible_service", lambda url: (_ for _ in ()).throw(AssertionError("lease checked first")))
    args = SimpleNamespace(state_file=tmp_path / "state.json", lease_file=tmp_path / "missing", instance="one", port=8765)
    assert zotero_service.run(args) == 1
    assert json.loads(args.state_file.read_text(encoding="utf-8"))["phase"] == "failed"


def test_unhealthy_live_model_restarts_and_passes_probe_before_api_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(zotero_service, "WindowsJob", lambda: SimpleNamespace(assign=lambda p: None, resume=lambda p: None, close=lambda: None))
    monkeypatch.setattr(zotero_service, "__file__", str(tmp_path / "scripts" / "zotero_service.py"))
    leases = iter([True, True, True, False])
    compatible = iter([False, True])
    monkeypatch.setattr(zotero_service, "lease_valid", lambda *a: next(leases))
    monkeypatch.setattr(zotero_service, "compatible_service", lambda *a: next(compatible))
    monkeypatch.setattr(zotero_service, "acquire_lock", lambda *a: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(zotero_service, "available_port", lambda value: value)
    monkeypatch.setattr(zotero_service.launcher, "ensure_wsl_keepalive", lambda *a: None)
    monkeypatch.setattr(zotero_service.launcher, "is_up", lambda *a: True)
    monkeypatch.setattr(zotero_service.launcher, "start_vlm", lambda: True)
    commands, probes, launches = [], [], []
    def probe():
        probes.append(True)
        if len(probes) <= 2:
            raise RuntimeError("HTTP works but OCR is corrupt")
    monkeypatch.setattr(zotero_service, "probe_vlm", probe)
    monkeypatch.setattr(zotero_service.subprocess, "run", lambda command, **kw: commands.append(command))
    def launch(command, **kwargs):
        assert len(probes) == 3, "no API may launch before the OCR probe succeeds"
        launches.append(kwargs)
        return SimpleNamespace(pid=123, poll=lambda: None, terminate=lambda: None, wait=lambda **kw: None)
    monkeypatch.setattr(zotero_service.subprocess, "Popen", launch)
    args = SimpleNamespace(state_file=tmp_path / "state.json", lease_file=tmp_path / "lease.json",
                           instance="test", port=8765, workspace=str(tmp_path / "data"), backend="wsl")
    assert zotero_service.run(args) == 0
    assert any("restart" in command for command in commands)
    assert launches[0]["env"]["PAPERBRIDGE_OCR_PAGE_BATCH_SIZE"] == "2"


def test_single_slow_probe_reuses_warm_model(monkeypatch):
    probes = []
    def probe():
        probes.append(True)
        if len(probes) == 1:
            raise TimeoutError("model still warming up")
    monkeypatch.setattr(zotero_service, "probe_vlm", probe)
    monkeypatch.setattr(zotero_service.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(zotero_service.subprocess, "run", lambda *a, **kw: pytest.fail("healthy model must not restart"))
    zotero_service.probe_warm_model(lambda *a: None)
    assert len(probes) == 2
def test_old_export_service_is_not_mistaken_for_current_backend(monkeypatch):
    from io import BytesIO
    monkeypatch.setattr(zotero_service.urllib.request, "urlopen", lambda *a, **kw: BytesIO(b'{"protocol":2}'))
    assert not zotero_service.compatible_service("http://127.0.0.1:8765")
    monkeypatch.setattr(zotero_service.urllib.request, "urlopen", lambda *a, **kw: BytesIO(b'{"protocol":2,"export_schema":3}'))
    assert not zotero_service.compatible_service("http://127.0.0.1:8765")
    monkeypatch.setattr(zotero_service.urllib.request, "urlopen", lambda *a, **kw: BytesIO(b'{"protocol":2,"export_schema":3,"features":["force-ocr","cancel-ocr"]}'))
    assert not zotero_service.compatible_service("http://127.0.0.1:8765")
    monkeypatch.setattr(zotero_service.urllib.request, "urlopen", lambda *a, **kw: BytesIO(b'{"protocol":2,"export_schema":5,"features":["force-ocr","cancel-ocr"]}'))
    assert not zotero_service.compatible_service("http://127.0.0.1:8765")
    monkeypatch.setattr(zotero_service.urllib.request, "urlopen", lambda *a, **kw: BytesIO(b'{"protocol":2,"export_schema":7,"features":["force-ocr","cancel-ocr","edited-source"]}'))
    assert zotero_service.compatible_service("http://127.0.0.1:8765")


