"""Hidden CPU service owned by a Zotero heartbeat and parent-process lease."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
import tempfile
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_lifetime import WindowsJob, ParentProcess
from runtime_config import runtime_environment

class StartupCancelled(Exception):
    pass

def compatible_service(url: str, owner: str | None = None) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/zotero/status", timeout=2) as response:
            status = json.load(response)
        return (status.get("protocol") == 2 and status.get("export_schema", 0) >= 13
                and (not status.get("managed_owner") or status["managed_owner"] == owner)
                and {"native-only", "reading-flow", "auto-layout", "edited-source"}.issubset(status.get("features", [])))
    except Exception:
        return False

def write_state(path: Path, instance: str, phase: str, message: str, **extra) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Startup and the owner watcher may both report shutdown. A shared .tmp
    # filename can be truncated by one writer while the other renames it.
    descriptor, filename = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"instance": instance, "phase": phase, "message": message,
                       "updated_at": time.time(), **extra}, stream, ensure_ascii=False)
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                # Windows readers may briefly open without delete sharing.
                if attempt == 9:
                    raise
                time.sleep(.02)
    finally:
        temporary.unlink(missing_ok=True)


def lease_valid(path: Path, instance: str, now: float | None = None) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(data["time"])
        return data.get("instance") == instance and -30 < age < 60
    except (OSError, ValueError, KeyError, TypeError):
        return False


def available_port(preferred: int) -> int:
    """Leave any existing service alone and choose another loopback port."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", candidate))
                return probe.getsockname()[1]
            except OSError:
                if candidate == 0:
                    raise RuntimeError("无法分配本机服务端口，请检查网络设置")
    raise RuntimeError("无法分配本机服务端口")


def acquire_lock(root: Path):
    # An isolated test workspace can supply its own lock without contending
    # with the user's live project service or sharing any model processes.
    path = Path(os.environ.get("PAPERLOOM_LAUNCHER_LOCK", root / "scripts" / "launcher.lock"))
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("a+b")
    lock.seek(0)
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                lock.close()
                raise RuntimeError("另一个 PaperLoom 启动器仍在运行，请停止该启动器后重试")
            time.sleep(.25)
    return lock


def run(args) -> int:
    root = Path(__file__).resolve().parents[1]
    state = Path(args.state_file).resolve()
    lease = Path(args.lease_file).resolve()
    port = args.port
    url = f"http://127.0.0.1:{port}"
    instance = args.instance
    process = None
    lock = None
    log_file = None
    failed = False
    update = lambda phase, message, **kw: write_state(state, instance, phase, message, **kw)
    parent = ParentProcess(getattr(args, "parent_pid", None))
    job = None
    finished = threading.Event()
    cancelled = threading.Event()
    cleanup_lock = threading.Lock()
    cleaned = False

    def cleanup():
        nonlocal cleaned
        with cleanup_lock:
            if cleaned:
                return
            if job:
                job.close()  # Also owns any venv child process.
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            cleaned = True

    def watch_owner():
        # This also runs while model startup/probing is blocking the main thread.
        while not finished.wait(.5):
            if not parent.alive() or not lease_valid(lease, instance):
                cancelled.set()
                try:
                    cleanup()
                    update("stopped", "Zotero 已关闭，解析进程已停止")
                except Exception as exc:
                    update("failed", "停止解析服务 失败：" + str(exc))
                finally:
                    os._exit(0)  # Release the launcher lock even during startup.

    try:
        update("checking", "检查本地运行环境…")
        if not lease_valid(lease, instance):
            raise RuntimeError("Zotero 启动请求已失效，请重试")
        if compatible_service(url, instance):
            update("ready", "服务已就绪", owned=False, service_url=url)
            return 0
        if getattr(args, "parent_pid", None):
            threading.Thread(target=watch_owner, name="zotero-owner-watch", daemon=True).start()
        lock = acquire_lock(root)
        port = available_port(port)
        url = f"http://127.0.0.1:{port}"
        if port != args.port:
            update("checking", "原端口被占用，正在使用空闲端口启动…", service_url=url)
        env = os.environ.copy()
        env.update(runtime_environment(args.runtime_config))
        env["PAPERBRIDGE_ZOTERO_OWNER"] = instance
        env["PAPERBRIDGE_WORKSPACE"] = str(Path(args.workspace).resolve() if args.workspace
                                          else Path(env.get("PAPERBRIDGE_WORKSPACE", root / "workspace")).resolve())
        if not lease_valid(lease, instance):
            raise StartupCancelled("Zotero 已取消启动")
        update("starting_api", "正在启动文档处理服务…")
        log_dir = root / "log"
        log_dir.mkdir(exist_ok=True)
        log_file = (log_dir / "paperloom-zotero.log").open("a", encoding="utf-8")
        job = WindowsJob()
        process = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(root / "scripts" / "auto_service_entry.py"), "serve", "--port", str(port)],
            cwd=root, env=env, stdout=log_file, stderr=subprocess.STDOUT,
            # Suspend before assignment so even a fast venv launcher cannot
            # spawn an unowned grandchild between Popen and Job assignment.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | (0x4 if os.name == "nt" else 0),
        )
        job.assign(process)
        job.resume(process)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("文档处理服务启动失败，请查看 log/paperloom-zotero.log")
            if not lease_valid(lease, instance):
                raise StartupCancelled("Zotero 已取消启动")
            if compatible_service(url, instance):
                break
            time.sleep(.5)
        else:
            raise RuntimeError("文档处理服务启动超时，请查看 log/paperloom-zotero.log")
        update("ready", "服务已就绪", owned=True, pid=process.pid, service_url=url)
        while parent.alive() and lease_valid(lease, instance) and process.poll() is None:
            time.sleep(.5)
        update("stopping", "正在释放文档处理服务…")
    except Exception as exc:
        if isinstance(exc, StartupCancelled) or cancelled.is_set():
            cancelled.set()
            update("stopping", "启动已取消，正在释放服务…")
        else:
            failed = True
            update("failed", str(exc))
    finally:
        finished.set()
        try:
            cleanup()
        except Exception as exc:
            failed = True
            update("failed", "停止解析服务 失败：" + str(exc))
        parent.close()
        if log_file:
            log_file.close()
        if lock:
            lock.close()
        if not failed and (process is not None or cancelled.is_set()):
            update("stopped", "服务已停止")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--lease-file", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--backend", choices=["local"], default="local")
    parser.add_argument("--workspace")
    parser.add_argument("--runtime-config")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    sys.exit(run(args))
