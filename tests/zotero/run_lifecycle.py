"""Real runner shutdown/restart checks; --wsl also verifies GPU model cleanup.

Uses a temporary owner process instead of the user's Zotero profile. All API
workspaces are isolated; WSL mode controls the project's dedicated model service.
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
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.process_lifetime import ParentProcess, WindowsJob


def up(url):
    try:
        with urllib.request.urlopen(url, timeout=1):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wsl", action="store_true")
    args = parser.parse_args()
    folder = ROOT / "workspace" / "lifecycle" / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    checks = []
    report = {"checks": checks, "ok": False}
    try:
        # Test a real venv-style grandchild, not just the immediate API PID.
        child_file = folder / "grandchild.pid"
        job = WindowsJob()
        worker = subprocess.Popen([sys.executable, "-c",
            "import subprocess,sys,pathlib,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(600)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(600)", str(child_file)],
            creationflags=no_window)
        job.assign(worker)
        for _ in range(100):
            if child_file.exists():
                break
            time.sleep(.05)
        child = ParentProcess(int(child_file.read_text()))
        assert child.alive()
        job.close()
        worker.wait(5)
        for _ in range(50):
            if not child.alive():
                break
            time.sleep(.1)
        assert not child.alive(), "grandchild survived Job Object close"
        child.close()
        checks.append("Windows Job Object kills immediate process and grandchild")

        # Block model startup deliberately; parent monitoring must not depend
        # on reaching the API-ready loop or the 60-second heartbeat timeout.
        owner = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(600)"], creationflags=no_window)
        startup_lease = folder / "startup-lease.json"
        startup_lease.write_text(json.dumps({"instance": "startup", "time": time.time()}))
        entered = folder / "loading.txt"
        stopped = folder / "model-stop.txt"
        injection = f'''
from scripts import zotero_service as service
from types import SimpleNamespace
from pathlib import Path
import time
service.launcher.ensure_wsl_keepalive = lambda root: None
service.launcher.is_up = lambda *args: False
def load():
    Path({str(entered)!r}).write_text("loading")
    time.sleep(600)
service.launcher.start_vlm = load
service.subprocess.run = lambda *args, **kwargs: Path({str(stopped)!r}).write_text("stopped")
service.run(SimpleNamespace(state_file={str(folder / 'startup-state.json')!r}, lease_file={str(startup_lease)!r},
    instance="startup", port=18765, workspace={str(folder / 'data')!r}, backend="wsl", parent_pid={owner.pid}))
'''
        startup_runner = subprocess.Popen([sys.executable, "-c", injection], cwd=ROOT, creationflags=no_window,
            env={**os.environ, "PAPERLOOM_LAUNCHER_LOCK": str(folder / "startup.lock")})
        try:
            for _ in range(100):
                if entered.exists():
                    break
                time.sleep(.05)
            assert entered.exists()
            owner.terminate()
            owner.wait(5)
            assert startup_runner.wait(5) == 0
            assert stopped.exists(), "model cleanup did not run during startup cancellation"
            checks.append("owner exit during blocked model loading cleans up and exits within five seconds")
        finally:
            startup_lease.unlink(missing_ok=True)
            if owner.poll() is None:
                owner.terminate()
                owner.wait(5)
            if startup_runner.poll() is None:
                startup_runner.kill()
                startup_runner.wait(5)

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # Same workspace, port and lock must be reusable immediately after exit.
        for index in range(2):
            owner = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(600)"], creationflags=no_window)
            lease = folder / "lease.json"
            state = folder / "state.json"
            instance = f"lifecycle-{index}"
            lease.write_text(json.dumps({"instance": instance, "time": time.time()}))
            with (folder / f"runner-{index}.log").open("w") as log:
                runner = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "zotero_service.py"),
                    "--parent-pid", str(owner.pid), "--state-file", str(state), "--lease-file", str(lease),
                    "--instance", instance, "--port", str(port), "--workspace", str(folder / "data"),
                    "--backend", "wsl" if args.wsl and index == 0 else "local"],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=no_window,
                    env={**os.environ, **({} if args.wsl else {"PAPERLOOM_LAUNCHER_LOCK": str(folder / "launcher.lock")})})
                try:
                    deadline = time.monotonic() + (300 if args.wsl else 30)
                    while time.monotonic() < deadline:
                        lease.write_text(json.dumps({"instance": instance, "time": time.time()}))
                        try:
                            status = json.loads(state.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            status = {}
                        if status.get("instance") == instance:
                            assert status["phase"] != "failed", status
                            if status["phase"] == "ready":
                                break
                        assert runner.poll() is None, "runner exited before ready"
                        time.sleep(.2)
                    else:
                        raise AssertionError("runner startup timed out")
                    assert status["owned"]
                    url = status["service_url"]
                    api = ParentProcess(status["pid"])
                    assert api.alive() and up(url + "/api/zotero/status")
                    # Leave a VALID lease in place to prove parent death alone
                    # stops the service (including forced Zotero termination).
                    started = time.monotonic()
                    owner.terminate()
                    owner.wait(5)
                    assert runner.wait(30) == 0
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and (api.alive() or up(url + "/api/zotero/status")):
                        time.sleep(.05)
                    assert not api.alive() and not up(url + "/api/zotero/status")
                    api.close()
                    checks.append(f"session {index + 1}: owner exit stops runner and API in {time.monotonic()-started:.2f}s; lock/port reusable")
                    if args.wsl and index == 0:
                        active = subprocess.run(["wsl", "-u", "root", "-e", "systemctl", "is-active", "wsl-fastdeploy-vl16"],
                            capture_output=True, timeout=10, creationflags=no_window)
                        assert active.returncode != 0 and not up("http://127.0.0.1:8185/v1/models"), active.stdout
                        assert not (ROOT / "scripts" / "wsl-keepalive.pid").exists()
                        checks.append("WSL model cgroup stopped, GPU endpoint closed and keepalive removed")
                finally:
                    lease.unlink(missing_ok=True)
                    if owner.poll() is None:
                        owner.terminate()
                        owner.wait(5)
                    if runner.poll() is None:
                        try:
                            runner.wait(30)
                        except subprocess.TimeoutExpired:
                            runner.kill()
                            runner.wait(5)
        report["ok"] = True
    finally:
        (folder / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"folder": str(folder), **report}, indent=2), flush=True)


if __name__ == "__main__":
    main()
