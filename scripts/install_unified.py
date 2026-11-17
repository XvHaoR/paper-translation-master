"""Install or register the unified PDF runtime for Zotero."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    package = Path(__file__).resolve().parent
    portable = (package / "python" / "python.exe").is_file()
    if portable:
        root = package / "runtime"
        python = package / "python" / "python.exe"
    else:
        root = package.parent
        env = root / ".venv"
        python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", ".[layout]"], cwd=root, check=True)
        subprocess.run([str(python), "zotero/build.py", "--portable"], cwd=root, check=True)

    subprocess.run([str(python), "-X", "utf8", "-c",
                    "import pymupdf; from app.server import create_app; "
                    "assert hasattr(pymupdf.Document, 'to_json'), 'PyMuPDF layout support is missing'"],
                   cwd=root, check=True)
    data = Path(os.environ["LOCALAPPDATA"]) if os.name == "nt" else Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    destination = data / "PaperTranslationMasterUnified"
    destination.mkdir(parents=True, exist_ok=True)
    runtime = {
        "schema": 1, "projectRoot": str(root), "pythonPath": str(python),
        "backend": "local", "environment": {"PAPERBRIDGE_WORKSPACE": str(destination / "workspace")},
    }
    temporary = destination / "runtime.json.tmp"
    temporary.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination / "runtime.json")
    print("Runtime registered. Install the XPI from the package or zotero/dist.")


if __name__ == "__main__":
    main()
