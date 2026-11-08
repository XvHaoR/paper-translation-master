"""One leased owner for the dedicated WSL model, across all runtime versions.

Never restart on an OOM loop or stop unrelated WSL jobs. The runtime override
disables systemd's automatic restart only while this owner holds the model lock.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

UNIT = "wsl-fastdeploy-vl16"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OVERRIDE = "/run/systemd/system/wsl-fastdeploy-vl16.service.d/90-paperloom-lease.conf"


class StartupCancelled(RuntimeError):
    pass


def command(*args, timeout=15, check=True):
    return subprocess.run(["wsl", "-u", "root", "-e", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          check=check, creationflags=NO_WINDOW)


def acquire_model_lock():
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "PaperTranslationMaster" / "locks"
    base.mkdir(parents=True, exist_ok=True)
    handle = (base / "wsl-fastdeploy-vl16.lock").open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("另一个 Zotero OCR 会话正在使用模型，请先停止该会话后重试")
    return handle


def check_gpu_memory():
    result = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, timeout=10, check=True, creationflags=NO_WINDOW)
    total, free = [float(value.strip()) for value in result.stdout.splitlines()[0].split(",")]
    required = min(6144, total * .6)  # Weights + prefill headroom; KV cache adapts to remaining memory.
    if free < required:
        raise RuntimeError(f"OCR 模型未启动：显存不足，当前空闲 {free/1024:.1f} GB，启动需约 {required/1024:.1f} GB。请关闭占用显存的程序后重试。")


class WSLModel:
    def __init__(self):
        self.lock = acquire_model_lock()
        self.cancelled = threading.Event()
        self.mutation = threading.Lock()
        self.closed = False
        self.configured = False

    def start(self, ready, timeout=240):
        with self.mutation:
            if self.cancelled.is_set():
                raise StartupCancelled("Zotero 已取消启动")
            # An existing warm model is retained; no extra model or VRAM needed.
            warm = ready()
            if not warm:
                check_gpu_memory()
            command("python3", "-c", "from pathlib import Path; p=Path(" + repr(OVERRIDE) +
                    "); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('[Service]\\nRestart=no\\n')")
            self.configured = True
            command("systemctl", "daemon-reload")
            if warm:
                return
            command("systemctl", "reset-failed", UNIT, check=False)
            command("systemctl", "start", UNIT, timeout=30)
        deadline = time.monotonic() + timeout
        while not self.cancelled.is_set() and time.monotonic() < deadline:
            if ready():
                return
            state = command("systemctl", "show", UNIT, "--property=ActiveState", "--value").stdout.strip()
            if state in {"inactive", "failed", "deactivating"}:
                raise RuntimeError("OCR 模型启动失败，已停止自动重启。请查看 FastDeploy 日志并检查显存后重试。")
            self.cancelled.wait(1)
        if self.cancelled.is_set():
            raise StartupCancelled("Zotero 已取消启动")
        raise RuntimeError("OCR 模型加载超时，已停止启动")

    def close(self):
        self.cancelled.set()
        with self.mutation:
            if self.closed:
                return
            # Queue a cgroup-wide stop first. If the model cannot exit promptly,
            # kill only this dedicated unit, then verify it really is inactive.
            command("systemctl", "stop", "--no-block", UNIT)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = command("systemctl", "show", UNIT, "--property=ActiveState", "--value").stdout.strip()
                if state in {"inactive", "failed"}:
                    break
                time.sleep(.25)
            else:
                command("systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", UNIT, check=False)
                command("systemctl", "stop", UNIT, timeout=15)
            state = command("systemctl", "show", UNIT, "--property=ActiveState", "--value").stdout.strip()
            if state not in {"inactive", "failed"}:
                raise RuntimeError("OCR 模型尚未完全停止，请重试停止服务")
            if self.configured:
                command("python3", "-c", "from pathlib import Path; Path(" + repr(OVERRIDE) + ").unlink(missing_ok=True)")
                command("systemctl", "daemon-reload")
            self.closed = True
            self.lock.close()
