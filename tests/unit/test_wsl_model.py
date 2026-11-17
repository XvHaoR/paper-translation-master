import subprocess
from types import SimpleNamespace

import pytest

from scripts import wsl_model as model


def test_model_lock_is_shared_across_runtime_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    first = model.acquire_model_lock()
    try:
        with pytest.raises(RuntimeError, match="另一个 Zotero"):
            model.acquire_model_lock()
    finally:
        first.close()
    model.acquire_model_lock().close()


def test_vram_pressure_fails_before_systemd_start(monkeypatch):
    commands = []
    monkeypatch.setattr(model, "acquire_model_lock", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(model.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="12288, 986\n"))
    monkeypatch.setattr(model, "command", lambda *a, **kw: commands.append(a))
    with pytest.raises(RuntimeError, match="显存不足"):
        model.WSLModel().start(lambda: False)
    assert not commands


def test_failed_start_never_restarts_and_close_verifies_stop(monkeypatch):
    commands, closed = [], []
    monkeypatch.setattr(model, "acquire_model_lock", lambda: SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(model, "check_gpu_memory", lambda: None)
    def command(*args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout="failed\n")
    monkeypatch.setattr(model, "command", command)
    owner = model.WSLModel()
    with pytest.raises(RuntimeError, match="已停止自动重启"):
        owner.start(lambda: False)
    owner.close()
    assert commands.count(("systemctl", "start", model.UNIT)) == 1
    assert not any("restart" in args for args in commands)
    assert ("systemctl", "stop", "--no-block", model.UNIT) in commands
    assert closed == [True]
    owner.close()
    with pytest.raises(RuntimeError, match="取消"):
        owner.start(lambda: False)


def test_warm_model_keeps_weights_and_shutdown_failure_is_not_success(monkeypatch):
    commands = []
    monkeypatch.setattr(model, "acquire_model_lock", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(model, "check_gpu_memory", lambda: pytest.fail("warm model must not reserve twice"))
    def command(*args, **kwargs):
        commands.append(args)
        if "stop" in args:
            raise subprocess.TimeoutExpired(args, 15)
        return SimpleNamespace(stdout="active\n")
    monkeypatch.setattr(model, "command", command)
    owner = model.WSLModel()
    owner.start(lambda: True)
    assert not any("start" in args or "restart" in args for args in commands)
    with pytest.raises(subprocess.TimeoutExpired):
        owner.close()
    assert not owner.closed
