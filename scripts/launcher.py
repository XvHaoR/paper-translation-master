"""PaperLoom 一键启动器(双击运行,按需启动,不随开机自启)。

设计:
- WSL VLM 服务由 systemd 按需拉起(systemctl start,已 disable 不自启)
- 启动器挂载一个常驻 WSL 保活会话,防止 WSL 虚拟机空闲自动关闭导致 VLM 掉线
- 记录浏览器连接;最后一个页面关闭 30 秒后停止服务并释放 GPU
- 启动失败、正常退出与停止脚本均使用同一清理流程
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PAPERLOOM_URL = "http://127.0.0.1:8765/"
VLM_URL = "http://127.0.0.1:8185/v1/models"
VLM_START_TIMEOUT = 240  # 首次模型加载较久
PAPERLOOM_START_TIMEOUT = 90
BROWSER_CLOSE_GRACE = 30
BROWSER_OPEN_TIMEOUT = 180
VLM_SERVICES = ("wsl-fastdeploy-vl16", "vlm-server")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def show_error(message: str) -> None:
    """Show a useful error even when the launcher is built without a console."""
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "PaperLoom startup failed", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def project_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        exe_dir = Path(sys.executable).resolve().parent
        # EXE 可能放在项目根或 dist/ 下,统一归到项目根
        if exe_dir.name == "dist":
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent


def is_up(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def is_service_active() -> bool:
    try:
        r = subprocess.run(
            ["wsl", "-u", "root", "-e", "systemctl", "is-active", "wsl-fastdeploy-vl16"],
            capture_output=True,
            timeout=30,
            creationflags=NO_WINDOW,
        )
        return r.returncode == 0 and b"active" in r.stdout
    except Exception:
        return False


def start_vlm() -> bool:
    """通过 WSL systemd 启动 VLM 服务(带重试),并挂载保活会话。"""
    for attempt in range(1, 4):
        if attempt > 1:
            print(f"  重试启动 VLM 服务 ({attempt}/3)...")
        r = subprocess.run(
            ["wsl", "-u", "root", "-e", "systemctl", "start", "wsl-fastdeploy-vl16"],
            capture_output=True,
            timeout=60,
            creationflags=NO_WINDOW,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            print(f"  systemctl start 返回 {r.returncode}: {err[:200]}")
        if not is_service_active():
            time.sleep(5)
            continue
        deadline = time.time() + VLM_START_TIMEOUT
        while time.time() < deadline:
            if is_up(VLM_URL):
                return True
            elapsed = int(VLM_START_TIMEOUT - (deadline - time.time()))
            print(f"  VLM 服务加载中... {elapsed}s", end="\r", flush=True)
            time.sleep(3)
        print()
        return is_up(VLM_URL)
    print()
    return False


def ensure_wsl_keepalive(root: Path) -> None:
    """Keep WSL alive while the accelerated OCR service is in use."""
    pid_file = root / "scripts" / "wsl-keepalive.pid"
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=NO_WINDOW,
        )
        if str(pid) in result.stdout:
            return
    except Exception:
        pass

    proc = subprocess.Popen(
        ["wsl", "-e", "bash", "-lc", "sleep 2147483"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        pid_file.write_text(str(proc.pid), encoding="ascii")
    except Exception:
        pass


def find_paperloom_python(root: Path) -> str | None:
    """优先使用已有 OCR 环境,回退到 PATH 中的 python。"""
    candidates = [
        str(root / ".venv" / "Scripts" / "python.exe"),
        str(root.parent / "python" / "python.exe"),
        "python",
    ]
    for c in candidates:
        try:
            r = subprocess.run(
                [c, "-c", "import fastapi, uvicorn, websockets, app"],
                capture_output=True,
                timeout=20,
                cwd=str(root),
                creationflags=NO_WINDOW,
            )
            if r.returncode == 0:
                return c
        except Exception:
            continue
    return None


def start_paperloom(root: Path) -> int | None:
    """独立控制台启动 PaperLoom,返回 PID(用于停止)。"""
    py = find_paperloom_python(root)
    if py is None:
        show_error(
            "未找到论文翻译大师 Python 环境。\n\n"
            "请先运行 OCR 安装器，或在项目目录创建 .venv 并安装依赖。"
        )
        return None
    env = os.environ.copy()
    # The verified WSL service uses PaddleOCR-VL-1.6.  Its Markdown matched
    # the local official output on the fixed two-page regression sample.
    env.update(
        {
            "PAPERBRIDGE_OFFICIAL_ACCELERATOR": "fastdeploy-vl16",
            "PAPERBRIDGE_OCR_VL_BACKEND": "fastdeploy-server",
            "PAPERBRIDGE_OCR_VL_SERVER_URL": "http://127.0.0.1:8185/v1",
            "PAPERBRIDGE_OCR_VL_MAX_CONCURRENCY": "4",
            "PAPERBRIDGE_OCR_USE_QUEUES": "true",
            "PAPERBRIDGE_OCR_PAGE_BATCH_SIZE": "2",
        }
    )
    env.pop("PAPERBRIDGE_OFFICIAL_WSL_URL", None)
    log_dir = root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "paperloom-server.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [py, "-m", "app.cli", "serve", "--port", "8765"],
        cwd=str(root),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log_file.close()
    pid_file = root / "scripts" / "paperloom.pid"
    try:
        pid_file.write_text(str(proc.pid), encoding="ascii")
    except Exception:
        pass
    return proc.pid


def stop_recorded_process(root: Path, filename: str, marker: str) -> None:
    """Verify a recorded PID still belongs to this application before killing its tree."""
    pid_file = root / "scripts" / filename
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine"],
            capture_output=True, timeout=15, creationflags=NO_WINDOW,
        )
        command = result.stdout.decode("utf-8", "replace")
        if marker in command:
            subprocess.run(["taskkill", "/f", "/t", "/pid", str(pid)],
                           capture_output=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    finally:
        pid_file.unlink(missing_ok=True)


def stop_paperloom(root: Path) -> None:
    stop_recorded_process(root, "paperloom.pid", "-m app.cli serve --port 8765")


def stop_services(root: Path) -> None:
    stop_paperloom(root)
    try:
        # systemd stops the complete model cgroup (workers included).
        result = subprocess.run(["wsl", "-u", "root", "-e", "systemctl", "stop", *VLM_SERVICES],
                                capture_output=True, timeout=60, creationflags=NO_WINDOW)
        if result.returncode:
            print("Warning: VLM service stop returned an error:", result.stderr.decode("utf-8", "replace")[:300])
    except Exception:
        print("Warning: could not stop WSL VLM service")
    stop_recorded_process(root, "wsl-keepalive.pid", "sleep 2147483")


def monitor_browser() -> None:
    started = time.monotonic()
    failures = 0
    while True:
        time.sleep(2)
        try:
            with urllib.request.urlopen(PAPERLOOM_URL + "api/runtime", timeout=4) as response:
                state = json.load(response)
            failures = 0
        except Exception:
            failures += 1
            if failures >= 3:
                return
            continue
        if state["seen_browser"] and not state["clients"] and state["idle_seconds"] >= BROWSER_CLOSE_GRACE:
            return
        if not state["seen_browser"] and time.monotonic() - started >= BROWSER_OPEN_TIMEOUT:
            return


def main() -> None:
    """Start the verified official reader with WSL PaddleOCR-VL-1.6 acceleration."""
    print("=" * 52)
    print("  PaperLoom official OCR + WSL PaddleOCR-VL-1.6")
    print("=" * 52)
    root = project_root()
    zotero_mode = "--zotero" in sys.argv

    if "--stop" in sys.argv:
        stop_services(root)
        return

    # A second double-click opens a tab; only the first launcher owns cleanup.
    import msvcrt
    lock = (root / "scripts" / "launcher.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        if not zotero_mode:
            webbrowser.open(PAPERLOOM_URL)
        else:
            print("PaperLoom launcher is already running. Keep the existing session open.")
        return

    if is_up(PAPERLOOM_URL):
        print("[OK] PaperLoom is already running (8765)")
        if not zotero_mode:
            webbrowser.open(PAPERLOOM_URL)
        lock.close()
        return
    try:
        ensure_wsl_keepalive(root)
        if not is_up(VLM_URL):
            print("[1/3] Starting WSL PaddleOCR-VL-1.6 service...")
            if not start_vlm():
                show_error(
                    "The WSL PaddleOCR-VL-1.6 service did not become ready.\n\n"
                    "Check WSL and then start PaperLoom again."
                )
                return
        print("[2/3] Starting PaperLoom official OCR mode (8765)...")
        if start_paperloom(root) is None:
            return
        deadline = time.time() + PAPERLOOM_START_TIMEOUT
        while time.time() < deadline and not is_up(PAPERLOOM_URL):
            time.sleep(1)
        if is_up(PAPERLOOM_URL):
            print("[OK] PaperLoom is ready (8765)")
        else:
            show_error(
                "PaperLoom did not become ready in 90 seconds.\n\n"
                f"See the startup log:\n{root / 'log' / 'paperloom-server.log'}"
            )
            return

        if zotero_mode:
            print("[3/3] Zotero service ready. Keep this window open; press Ctrl+C to stop.")
            failures = 0
            while failures < 3:
                time.sleep(2)
                failures = 0 if is_up(PAPERLOOM_URL) else failures + 1
        else:
            print("[3/3] Opening browser...")
            webbrowser.open(PAPERLOOM_URL)
            monitor_browser()
    except KeyboardInterrupt:
        print("Stopping PaperLoom and OCR services...")
    finally:
        stop_services(root)
        lock.close()


if __name__ == "__main__":
    main()
