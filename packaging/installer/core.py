"""Private Windows OCR provisioning. No system Python, PATH or WSL changes.

The frozen setup contains only this controller and the reviewed engine sources.
CPython and pip are downloaded from pinned official URLs with SHA-256 checks.
Paddle wheels come from the official CUDA 12.6 index, never an arbitrary script.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile

PYTHON_VERSION = "3.13.17"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_SHA256 = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
PIP_URL = "https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl"
PIP_SHA256 = "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
PADDLE_INDEX = "https://www.paddlepaddle.org.cn/packages/stable/cu126/"
PADDLE_VERSION = "3.3.1"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENV_KEYS = ("PAPERBRIDGE_WORKSPACE", "PADDLE_PDX_CACHE_HOME", "HF_HOME", "MODELSCOPE_CACHE",
            "PAPERBRIDGE_OCR_PAGE_BATCH_SIZE", "PAPERBRIDGE_OCR_USE_QUEUES",
            "PAPERBRIDGE_OCR_MODE", "PAPERBRIDGE_RAPIDOCR_MODELS")


class InstallError(RuntimeError):
    pass


class Cancelled(InstallError):
    pass


def windows_x64() -> bool:
    return os.name == "nt" and platform.machine().upper() in ("AMD64", "X86_64")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def safe_extract(archive: Path, destination: Path) -> None:
    """Reject path escapes, symlinks and zip bombs before writing any member."""
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if sum(member.file_size for member in members) > 512 * 1024 * 1024:
            raise InstallError("安装包解压体积异常，请重新下载官方安装器")
        for member in members:
            name = member.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or ":" in name
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise InstallError("安装包包含无效路径，请重新下载官方安装器")
        package.extractall(destination)


def download(url: str, target: Path, sha256: str, notify, cancel: threading.Event) -> None:
    """Reuse verified files; resume Range downloads and verify before promotion."""
    if target.is_file() and digest(target) == sha256:
        notify(f"已验证下载缓存：{target.name}")
        return
    part = target.with_suffix(target.suffix + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        if cancel.is_set():
            raise Cancelled("安装已取消，下载缓存已保留，下次可继续")
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "PaperTranslationMaster-Setup/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as response:
                if offset and response.status != 206:
                    offset = 0
                if offset and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise InstallError("下载服务器返回了不匹配的断点数据")
                total = offset + int(response.headers.get("Content-Length", 0))
                done = offset
                last = 0.0
                with part.open("ab" if offset else "wb") as output:
                    while chunk := response.read(256 * 1024):
                        if cancel.is_set():
                            raise Cancelled("安装已取消，下载缓存已保留，下次可继续")
                        output.write(chunk)
                        done += len(chunk)
                        if time.monotonic() - last > .3:
                            notify(f"下载 {target.name}：{done / 1048576:.1f} / {total / 1048576:.1f} MB")
                            last = time.monotonic()
            if digest(part) != sha256:
                part.unlink(missing_ok=True)
                raise InstallError("下载文件校验失败，已丢弃不完整文件；请重试")
            part.replace(target)
            return
        except Cancelled:
            raise
        except (OSError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code == 416:
                part.unlink(missing_ok=True)
            if attempt == 2:
                raise InstallError(f"下载失败：{url}\n请检查网络后重试。{error}") from error
            notify("网络暂时不可用，正在保留断点重试…")


def parse_gpu(output: str) -> list[dict]:
    devices = []
    for line in output.splitlines():
        columns = [column.strip() for column in line.split(",")]
        if len(columns) != 4:
            continue
        try:
            devices.append({"name": columns[0], "memoryMB": int(columns[1]),
                            "driver": columns[2], "compute": float(columns[3])})
        except ValueError:
            continue
    return devices


def gpu_requirement(devices: list[dict]) -> dict:
    for device in devices:
        numbers = tuple(int(part) for part in re.findall(r"\d+", device["driver"])[:2])
        if device["memoryMB"] >= 8 * 1024 and numbers >= (560, 76) and 7.0 <= device["compute"] < 12:
            return device
    raise InstallError("当前一键安装包需要 NVIDIA 显卡（至少 8 GB 显存，推荐 12 GB）、"
                       "CUDA 12.6 兼容驱动（Windows 560.76 或更新）和 CC 7.x–9.x。\n"
                       "请更新 NVIDIA 驱动后重试；RTX 50 系列及其他显卡暂需按官方文档单独配置。"
                       "\n无需自行安装 CUDA 工具包。已有 OCR/译文仍可在 Zotero 中离线查看。")


def runtime_environment(base: Path, mode="official") -> dict[str, str]:
    return {"PAPERBRIDGE_WORKSPACE": str(base / "workspace"),
            "PADDLE_PDX_CACHE_HOME": str(base / "models" / "paddlex"),
            "HF_HOME": str(base / "models" / "huggingface"),
            "MODELSCOPE_CACHE": str(base / "models" / "modelscope"),
            "PAPERBRIDGE_OCR_PAGE_BATCH_SIZE": "2", "PAPERBRIDGE_OCR_USE_QUEUES": "true",
            "PAPERBRIDGE_OCR_MODE": mode, "PAPERBRIDGE_RAPIDOCR_MODELS": str(base / "models/rapidocr")}


@contextmanager
def install_lock(base: Path, filename="install.lock", message="另一个安装器正在运行，请关闭它后重试"):
    base.mkdir(parents=True, exist_ok=True)
    lock = (base / filename).open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise InstallError(message) from error
        yield
    finally:
        lock.close()


class Installer:
    def __init__(self, base: Path, payload: Path, release: dict, notify, cancel=None):
        self.base, self.payload, self.release, self.notify = base, payload, release, notify
        self.cancel = cancel or threading.Event()
        self.project = base / "engine"
        self.python = base / "python" / "python.exe"
        self.log = base / "install.log"
        self.mode = release.get("ocrMode", "official")
        if self.mode not in {"official", "rapidocr"}:
            raise InstallError("无效的 OCR 安装版本")
        self.env = os.environ.copy()
        self.env.update(runtime_environment(base, self.mode))
        self.env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
                        PIP_CACHE_DIR=str(base / "downloads" / "pip"), PIP_NO_INPUT="1")
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONHOME", None)
        for key in ("PAPERBRIDGE_OCR_VL_BACKEND", "PAPERBRIDGE_OCR_VL_SERVER_URL",
                    "PAPERBRIDGE_OFFICIAL_ACCELERATOR", "PAPERBRIDGE_OFFICIAL_WSL_URL"):
            self.env.pop(key, None)

    def status(self, phase: str, message: str, progress: int):
        atomic_json(self.base / "install-state.json", {"phase": phase, "message": message,
                    "progress": progress, "version": self.release["version"], "updatedAt": time.time()})
        self.notify(phase, message, progress)

    def run_command(self, arguments: list[str], timeout=1200, cwd=None, echo=True):
        if self.cancel.is_set():
            raise Cancelled("安装已取消；可再次运行安装器继续")
        # Isolated embedded Python ignores PYTHONUTF8; pass the explicit flag
        # so Chinese progress and error messages remain readable in the GUI.
        if arguments and str(arguments[0]) == str(self.python):
            arguments = [arguments[0], "-X", "utf8", *arguments[1:]]
        # A Job object owns pip and model-worker descendants when frozen/setup exits.
        # Parent process helper is part of the engine payload, imported only after extraction.
        job = None
        if os.name == "nt" and (self.project / "scripts" / "process_lifetime.py").is_file():
            import importlib.util
            spec = importlib.util.spec_from_file_location("installer_process_lifetime", self.project / "scripts/process_lifetime.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            job = module.WindowsJob()
        creation = NO_WINDOW | (0x4 if job else 0)
        process = subprocess.Popen(arguments, cwd=cwd or self.base, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   creationflags=creation)
        if job:
            job.assign(process)
            job.resume(process)
        lines = queue.Queue()
        def read():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)
        threading.Thread(target=read, daemon=True).start()
        start = time.monotonic()
        tail = []
        try:
            with self.log.open("a", encoding="utf-8") as log:
                while True:
                    if self.cancel.is_set():
                        raise Cancelled("安装已取消；已下载文件保留，再次运行可继续")
                    if time.monotonic() - start > timeout:
                        raise InstallError(f"当前步骤超过 {timeout // 60} 分钟，请检查网络后重试")
                    try:
                        line = lines.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    log.write(line)
                    log.flush()
                    tail.append(line.strip())
                    tail = tail[-12:]
                    if echo and line.strip():
                        self.notify("detail", line.strip()[-300:], None)
                code = process.wait(timeout=10)
            if code:
                raise InstallError(f"安装步骤失败（退出码 {code}）。\n" + "\n".join(tail[-6:])
                                   + f"\n完整日志：{self.log}")
            return "\n".join(tail)
        finally:
            if job:
                job.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            process.stdout.close()

    def install(self):
        self.base.mkdir(parents=True, exist_ok=True)
        with install_lock(self.base):
            try:
                with install_lock(self.project / "scripts", "launcher.lock",
                                  "本地 OCR 引擎正在使用中。请先关闭 Zotero 和 OCR 启动器，再重新运行安装器"):
                    return self._install()
            except Cancelled as error:
                self.status("cancelled", str(error), 0)
                raise
            except Exception as error:
                self.status("failed", str(error), 0)
                raise

    def _install(self):
        rapid = self.mode == "rapidocr"
        self.status("checking", "1/6 检查 Windows 及可用空间…" if rapid else "1/6 检查 Windows、显卡及可用空间…", 2)
        if not windows_x64():
            raise InstallError("此安装器仅支持 Windows 10/11 64 位（x64）")
        if platform.version().split(".")[-1].isdigit() and int(platform.version().split(".")[-1]) < 19041:
            raise InstallError("需要 Windows 10 2004（19041）或更新系统")
        gpu = None
        if rapid:
            self.notify("detail", "RapidOCR + ONNX Runtime CPU：无需 NVIDIA、CUDA 或 WSL", None)
        else:
            try:
                result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap",
                                         "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                        timeout=20, creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError) as error:
                raise InstallError("没有检测到 NVIDIA 驱动。请先安装 NVIDIA 驱动后重试。") from error
            gpu = gpu_requirement(parse_gpu(result.stdout))
            self.notify("detail", f"显卡：{gpu['name']} / {gpu['memoryMB'] // 1024} GB", None)
        required_gb = 5 if rapid else 15
        if shutil.disk_usage(self.base).free < required_gb * 1024 ** 3:
            raise InstallError(f"安装盘至少需要 {required_gb} GB 可用空间（依赖、模型及下载缓存）")

        self.status("engine", "2/6 解压文档处理引擎…", 8)
        # A failed repair must not continue advertising the half-updated engine.
        discoveries = {self.base / "runtime.json"}
        directory = self.release.get("runtimeDirectory")
        if directory:
            discoveries.add(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / directory / "runtime.json")
        for descriptor in discoveries:
            if not descriptor.is_file():
                continue
            try:
                previous = json.loads(descriptor.read_text(encoding="utf-8"))
                if Path(previous.get("projectRoot", "")).resolve() == self.project.resolve():
                    descriptor.replace(descriptor.with_suffix(".previous.json"))
            except (OSError, ValueError):
                pass
        safe_extract(self.payload, self.project)
        if not (self.project / "scripts/zotero_service.py").is_file():
            raise InstallError("安装器缺少文档引擎，请重新下载完整安装器")

        self.status("python", "3/6 下载并配置应用专用 Python…", 14)
        cache = self.base / "downloads"
        progress = lambda message: self.notify("detail", message, None)
        python_zip = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
        pip_zip = cache / "pip-26.2.2-py3-none-any.whl"
        download(PYTHON_URL, python_zip, PYTHON_SHA256, progress, self.cancel)
        download(PIP_URL, pip_zip, PIP_SHA256, progress, self.cancel)
        safe_extract(python_zip, self.python.parent)
        safe_extract(pip_zip, self.python.parent / "Lib/site-packages")
        (self.python.parent / "python313._pth").write_text(
            "python313.zip\n.\nLib/site-packages\n../engine\n../engine/scripts\n"
            "# Explicit private paths; do not import site or load the user's packages.\n", encoding="utf-8")

        self.status("dependencies", "4/6 下载 RapidOCR CPU 依赖…" if rapid else "4/6 下载 OCR 依赖（数 GB，期间可能较慢）…", 28)
        if not rapid:
            self.run_command([str(self.python), "-m", "pip", "install", "--prefer-binary", "--retries", "3",
                              "--timeout", "60", f"paddlepaddle-gpu=={PADDLE_VERSION}", "--index-url", PADDLE_INDEX], timeout=3600)
        requirements = self.project / "packaging/installer" / ("requirements-rapidocr.txt" if rapid else "requirements-runtime.txt")
        self.run_command([str(self.python), "-m", "pip", "install", "--prefer-binary", "--retries", "3",
                          "--timeout", "60", "--index-url", "https://pypi.org/simple", "-r", str(requirements)], timeout=3600)
        self.run_command([str(self.python), "-m", "pip", "check"], timeout=60)

        self.status("models", "5/6 下载官方 OCR 模型并预热，首次通常需要数分钟…", 65)
        (self.base / "warmup.json").unlink(missing_ok=True)
        self.run_command([str(self.python), str(self.project / "packaging/installer/warmup.py"),
                          "--report", str(self.base / "warmup.json")], timeout=3600, cwd=self.project)
        try:
            report = json.loads((self.base / "warmup.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise InstallError("模型预热未返回有效报告，未标记为安装成功，请重试") from error
        if (report.get("ok") is not True or
                (rapid and (report.get("pipeline") != "RapidOCR" or report.get("formulas", 0) < 1 or report.get("tables", 0) < 1)) or
                (not rapid and report.get("gpu") is not True)):
            raise InstallError("模型实际识别自检未通过，未标记为安装成功，请查看安装日志")

        self.status("registering", "6/6 注册 Zotero 可发现的本地引擎…", 96)
        descriptor = {"schema": 1, "version": self.release["version"], "name": self.release["name"],
                      "projectRoot": str(self.project), "pythonPath": str(self.python), "backend": "local",
                      "wslDistro": None, "environment": runtime_environment(self.base, self.mode), "ocrMode": self.mode,
                      "installedAt": time.time(), "warmup": report, "gpu": gpu}
        atomic_json(self.base / "runtime.json", descriptor)
        self.status("ready", "安装完成。请在 Zotero 安装插件并重启，之后可直接点击 OCR。", 100)
        return descriptor
