"""Install or register the unified PDF runtime for Zotero."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def data_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"])
    if directory := os.environ.get("XDG_DATA_HOME"):
        return Path(directory)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".local" / "share"


def main() -> None:
    package = Path(__file__).resolve().parent
    python = package / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    if python.is_file():
        root = package / "runtime"
    else:
        root = package.parent
        env = root / ".venv"
        python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", ".[layout]"], cwd=root, check=True)
        subprocess.run([str(python), "zotero/build.py", "--portable"], cwd=root, check=True)

    subprocess.run([str(python), "-s", "-X", "utf8", "-c",
                    "import pymupdf; from app.server import create_app; "
                    "assert hasattr(pymupdf.Document, 'to_json'), 'PyMuPDF layout support is missing'"],
                   cwd=root, check=True)
    destination = data_directory() / "PaperTranslationMasterUnified"
    destination.mkdir(parents=True, exist_ok=True)
    workspace = destination / "workspace"
    if sys.platform == "darwin" and not (destination / "runtime.json").exists():
        legacy_workspace = Path.home() / ".local/share/PaperTranslationMasterUnified/workspace"
        if legacy_workspace.is_dir():
            workspace = legacy_workspace
    descriptor = destination / "runtime.json"
    if descriptor.is_file():
        try:
            previous = json.loads(descriptor.read_text(encoding="utf-8-sig"))
            saved_workspace = Path(previous["environment"]["PAPERBRIDGE_WORKSPACE"])
            if saved_workspace.is_absolute():
                workspace = saved_workspace
        except (ValueError, KeyError, TypeError):
            pass
    runtime = {
        "schema": 1, "projectRoot": str(root), "pythonPath": str(python),
        "backend": "local", "environment": {"PAPERBRIDGE_WORKSPACE": str(workspace)},
    }
    fd, filename = tempfile.mkstemp(prefix="runtime-", suffix=".tmp", dir=destination)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(runtime, stream, ensure_ascii=False, indent=2)
        temporary.replace(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    print("Runtime registered. Install the XPI from the package or zotero/dist.")


if __name__ == "__main__":
    main()
