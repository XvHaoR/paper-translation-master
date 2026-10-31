"""Small Tk setup UI; the reviewed runtime ZIP is bundled in the EXE."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
import webbrowser

from core import Cancelled, Installer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--self-test", action="store_true", help="Check bundled files without installing")
    args = parser.parse_args()
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    payload = args.payload or resources / "runtime.zip"
    release_path = args.release or resources / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if args.self_test:
        import zipfile
        with zipfile.ZipFile(payload) as archive:
            required = {"scripts/zotero_service.py", "app/cli.py", "packaging/installer/warmup.py",
                        "packaging/installer/requirements-runtime.txt", "release.json"}
            if not required.issubset(set(archive.namelist())):
                raise RuntimeError("Bundled runtime is incomplete")
            if archive.testzip():
                raise RuntimeError("Bundled runtime failed ZIP verification")
        if getattr(sys, "frozen", False):
            for name in ("PYTHON-LICENSE.txt", "PROJECT-AGPL.txt", "THIRD_PARTY_NOTICES.md",
                         "PyInstaller-6.21.0.txt", "Python-3.13.txt"):
                if not (resources / "licenses" / name).is_file():
                    raise RuntimeError(f"Bundled license notice missing: {name}")
        return

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    base = args.target or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / release["runtimeDirectory"]
    root = tk.Tk()
    root.title(release["name"] + " · OCR 安装")
    root.geometry("760x590")
    root.minsize(680, 520)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="安装本地 OCR 引擎", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
    ttk.Label(frame, text="无需手动安装 Python，无需打开终端。安装完成后由 Zotero 按需启动。",
              wraplength=680).pack(anchor="w", pady=(8, 10))
    ttk.Label(frame, text="适用：Windows 10/11 x64，NVIDIA 显卡 ≥ 8 GB 显存（推荐 12 GB），"
              "驱动 560.76 或更新。\n首次下载依赖和模型需要数 GB 流量、至少 15 GB 空间；支持失败后重新运行继续。",
              wraplength=680).pack(anchor="w")
    target = tk.StringVar(value=str(base))
    row = ttk.Frame(frame)
    row.pack(fill="x", pady=12)
    ttk.Label(row, text="安装目录").pack(side="left", padx=(0, 8))
    entry = ttk.Entry(row, textvariable=target)
    entry.pack(side="left", fill="x", expand=True)
    def browse():
        chosen = filedialog.askdirectory(initialdir=str(base.parent), title="选择安装位置")
        if chosen:
            target.set(str(Path(chosen) / release["runtimeDirectory"]))
    choose = ttk.Button(row, text="更改…", command=browse)
    choose.pack(side="left", padx=(8, 0))
    progress = ttk.Progressbar(frame, maximum=100)
    progress.pack(fill="x", pady=(4, 8))
    status = tk.StringVar(value="准备安装。点击下方按钮后开始检查和下载。")
    ttk.Label(frame, textvariable=status, wraplength=680).pack(anchor="w", pady=(0, 10))
    text = tk.Text(frame, height=10, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
    text.pack(fill="both", expand=True)
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    cancel = threading.Event()
    events = queue.Queue()
    busy = False
    installed = False
    current_base = base
    def add_line(value):
        text.configure(state="normal")
        text.insert("end", value + "\n")
        text.see("end")
        text.configure(state="disabled")
    def notify(phase, message, value):
        events.put((phase, message, value))
    def start():
        nonlocal busy, current_base
        current_base = Path(target.get()).expanduser().resolve()
        cancel.clear()
        busy = True
        start_button.configure(state="disabled")
        choose.configure(state="disabled")
        entry.configure(state="disabled")
        close_button.configure(text="取消安装")
        def worker():
            try:
                Installer(current_base, payload, release, notify, cancel).install()
            except Cancelled:
                pass
            except Exception as error:
                notify("failed", str(error), 0)
            finally:
                notify("finished", "", None)
        threading.Thread(target=worker, daemon=True).start()
    def close():
        if busy:
            cancel.set()
            close_button.configure(state="disabled")
            status.set("正在停止安装进程，保留已下载文件…")
        else:
            root.destroy()
    def open_log():
        current_base.mkdir(parents=True, exist_ok=True)
        os.startfile(str(current_base))
    ttk.Button(buttons, text="查看日志目录", command=open_log).pack(side="left")
    start_button = ttk.Button(buttons, text="安装 OCR 引擎", command=start)
    start_button.pack(side="right")
    close_button = ttk.Button(buttons, text="关闭", command=close)
    close_button.pack(side="right", padx=8)
    def poll():
        nonlocal busy, installed
        try:
            while True:
                phase, message, value = events.get_nowait()
                if phase == "finished":
                    busy = False
                    close_button.configure(text="关闭", state="normal")
                    start_button.configure(text="重新检查 / 修复", state="normal")
                    choose.configure(state="normal")
                    entry.configure(state="normal")
                    continue
                if value is not None:
                    progress["value"] = value
                if phase != "detail":
                    status.set(message)
                if message:
                    add_line(message)
                if phase == "ready":
                    installed = True
                    # The fixed discovery file always lives in LocalAppData even for a custom install drive.
                    discovery = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / release["runtimeDirectory"]
                    if discovery.resolve() != current_base:
                        from core import atomic_json
                        discovery.mkdir(parents=True, exist_ok=True)
                        descriptor = json.loads((current_base / "runtime.json").read_text(encoding="utf-8"))
                        atomic_json(discovery / "runtime.json", descriptor)
                    messagebox.showinfo("安装完成", "本地 OCR 引擎已通过实际 PDF 识别自检。\n"
                                        "请安装 Zotero 插件并重启 Zotero。", parent=root)
        except queue.Empty:
            pass
        root.after(100, poll)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Resource errors must stay visible in a windowed build.
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "OCR 安装器错误", 0x10)
        raise
