"""Hidden, leased service process controlled by the Zotero plugin.

The UI reads atomic stage updates. A Zotero heartbeat owns this process; losing
that lease or the parent process stops the owned API tree and dedicated model.
"""
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
import urllib.request

if __package__:
    from . import launcher
    from .ocr_health import probe_vlm
    from .process_lifetime import WindowsJob, ParentProcess
    from .runtime_config import runtime_environment
else:
    import launcher
    from ocr_health import probe_vlm
    from process_lifetime import WindowsJob, ParentProcess
    from runtime_config import runtime_environment


def write_state(path: Path, instance: str, phase: str, message: str, **extra) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"instance": instance, "phase": phase, "message": message,
                                     "updated_at": time.time(), **extra}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def lease_valid(path: Path, instance: str, now: float | None = None) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(data["time"])
        return data.get("instance") == instance and -30 < age < 60
    except (OSError, ValueError, KeyError, TypeError):
        return False


def compatible_service(url: str, ocr_mode: str = "official") -> bool:
    try:
        release_path = Path(__file__).resolve().parents[1] / "release.json"
        release = json.loads(release_path.read_text(encoding="utf-8")) if release_path.is_file() else {}
        minimum = max(7, int(release.get("minimumExportSchema", 7)))
        with urllib.request.urlopen(url + "/api/zotero/status", timeout=2) as response:
            status = json.load(response)
            return (status.get("protocol") == 2 and status.get("export_schema", 0) >= minimum
                    and (ocr_mode != "rapidocr" or "rapidocr" in status.get("ocr_modes", []))
                    and {"force-ocr", "cancel-ocr", "edited-source"}.issubset(status.get("features", [])))
    except Exception:
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


def probe_warm_model(update) -> None:
    """One slow/failed warm-up request is not evidence that a restart is needed."""
    for attempt in range(2):
        try:
            probe_vlm()
            return
        except Exception:
            if attempt:
                raise
            update("checking_ocr", "OCR 自检暂未通过，正在重试；保留已加载模型…")
            time.sleep(1)


def run(args) -> int:
    root = Path(__file__).resolve().parents[1]
    state = Path(args.state_file).resolve()
    lease = Path(args.lease_file).resolve()
    port = args.port
    url = f"http://127.0.0.1:{port}"
    instance = args.instance
    process = None
    lock = None
    owns_model = False
    owns_keepalive = False
    log_file = None
    failed = False
    update = lambda phase, message, **kw: write_state(state, instance, phase, message, **kw)
    parent = ParentProcess(getattr(args, "parent_pid", None))
    job = None
    finished = threading.Event()
    cleanup_lock = threading.Lock()
    cleaned = False

    def cleanup():
        nonlocal cleaned
        with cleanup_lock:
            if cleaned:
                return
            cleaned = True
            if job:
                job.close()  # Also kills venv grandchildren and OCR subprocesses.
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if owns_model:
                try:
                    subprocess.run(["wsl", "-u", "root", "-e", "systemctl", "stop", "wsl-fastdeploy-vl16"],
                                   capture_output=True, timeout=20, creationflags=launcher.NO_WINDOW)
                except (OSError, subprocess.SubprocessError):
                    pass
            if owns_keepalive:
                launcher.stop_recorded_process(root, "wsl-keepalive.pid", "sleep 2147483")

    def watch_owner():
        # This also runs while model startup/probing is blocking the main thread.
        while not finished.wait(.5):
            if not parent.alive() or not lease_valid(lease, instance):
                try:
                    cleanup()
                    update("stopped", "Zotero 已关闭，OCR 进程已停止")
                finally:
                    os._exit(0)  # Release the launcher lock even during startup.

    try:
        update("checking", "检查本地运行环境…")
        if not lease_valid(lease, instance):
            raise RuntimeError("Zotero 启动请求已失效，请重试")
        requested_mode = getattr(args, "ocr_mode", None) or "official"
        if compatible_service(url, requested_mode):
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
        if args.backend == "local" and getattr(args,"runtime_config",None):
            for name in ("PAPERBRIDGE_OCR_VL_BACKEND", "PAPERBRIDGE_OCR_VL_SERVER_URL",
                         "PAPERBRIDGE_OFFICIAL_ACCELERATOR", "PAPERBRIDGE_OFFICIAL_WSL_URL"):
                env.pop(name, None)
        env.update(runtime_environment(getattr(args, "runtime_config", None)))
        if requested_mode == "rapidocr":
            env["PAPERBRIDGE_OCR_MODE"] = "rapidocr"
            env["PAPERBRIDGE_OCR_DEVICE"] = "cpu"
            if args.backend != "local":
                raise RuntimeError("RapidOCR 版使用本地 CPU，请在设置中将后端改为 local")
        env["PAPERBRIDGE_WORKSPACE"] = str(Path(args.workspace).resolve() if args.workspace
                                          else Path(env.get("PAPERBRIDGE_WORKSPACE", root / "workspace")).resolve())
        if args.backend == "wsl":
            # The exclusive project launcher lock owns this dedicated model,
            # including a warm model left behind by an earlier Zotero session.
            owns_model = True
            update("starting_ocr", "正在启动 WSL OCR 服务…")
            keepalive_file = root / "scripts" / "wsl-keepalive.pid"
            launcher.ensure_wsl_keepalive(root)
            owns_keepalive = keepalive_file.exists()
            if not launcher.is_up(launcher.VLM_URL):
                owns_model = True
                update("loading_model", "正在加载 OCR 模型，首次启动可能需要几分钟…")
                if not launcher.start_vlm():
                    raise RuntimeError("OCR 模型未能启动，请检查 WSL FastDeploy 配置")
            update("checking_ocr", "正在检查 OCR 模型实际识别能力…")
            try:
                probe_warm_model(update)
            except Exception:
                update("loading_model", "OCR 模型状态异常，正在重启恢复…")
                owns_model = True
                subprocess.run(["wsl", "-u", "root", "-e", "systemctl", "restart", "wsl-fastdeploy-vl16"],
                               capture_output=True, timeout=60, check=True, creationflags=launcher.NO_WINDOW)
                if not launcher.start_vlm():
                    raise RuntimeError("OCR 模型重启失败，请重试")
                probe_vlm()
            env.update({
                "PAPERBRIDGE_OFFICIAL_ACCELERATOR": "fastdeploy-vl16",
                "PAPERBRIDGE_OCR_VL_BACKEND": "fastdeploy-server",
                "PAPERBRIDGE_OCR_VL_SERVER_URL": "http://127.0.0.1:8185/v1",
                "PAPERBRIDGE_OCR_VL_MAX_CONCURRENCY": "4",
                "PAPERBRIDGE_OCR_USE_QUEUES": "true",
                "PAPERBRIDGE_OCR_PAGE_BATCH_SIZE": "2",
            })
            env.pop("PAPERBRIDGE_OFFICIAL_WSL_URL", None)
        if not lease_valid(lease, instance):
            raise RuntimeError("Zotero 已取消启动")
        update("starting_api", "正在启动文档处理服务…")
        log_dir = root / "log"
        log_dir.mkdir(exist_ok=True)
        log_file = (log_dir / "paperloom-zotero.log").open("a", encoding="utf-8")
        job = WindowsJob()
        process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "app.cli", "serve", "--port", str(port)],
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
                raise RuntimeError("Zotero 已取消启动")
            if compatible_service(url, requested_mode):
                break
            time.sleep(.5)
        else:
            raise RuntimeError("文档处理服务启动超时，请查看 log/paperloom-zotero.log")
        update("ready", "服务已就绪", owned=True, pid=process.pid, service_url=url)
        while parent.alive() and lease_valid(lease, instance) and process.poll() is None:
            time.sleep(.5)
        update("stopping", "正在释放文档处理服务…")
    except Exception as exc:
        failed = True
        update("failed", str(exc))
    finally:
        finished.set()
        cleanup()
        parent.close()
        if log_file:
            log_file.close()
        if lock:
            lock.close()
        if process is not None and not failed:
            update("stopped", "服务已停止")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--lease-file", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--backend", choices=["wsl", "local"], default="wsl" if os.name == "nt" else "local")
    parser.add_argument("--workspace")
    parser.add_argument("--runtime-config", help="Installer runtime descriptor")
    parser.add_argument("--ocr-mode", choices=["official", "rapidocr"], default="official")
    parser.add_argument("--parent-pid", type=int, help="Owning Zotero main process")
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65535:
        parser.error("invalid port")
    sys.exit(run(arguments))
