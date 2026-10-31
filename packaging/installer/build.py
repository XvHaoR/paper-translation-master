"""Build an EXE without provisioning OCR or changing the build machine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"{release['slug']}-setup-{release['version']}"
    python_license = next((path for prefix in {Path(sys.prefix), Path(sys.base_prefix)}
                           for path in (prefix / "LICENSE.txt", prefix / "LICENSE_PYTHON.txt", prefix / "LICENSE")
                           if path.is_file()), None)
    if python_license is None:
        raise RuntimeError("Cannot find the build interpreter's Python license; refusing to bundle without its notice")
    with tempfile.TemporaryDirectory(prefix="ocr-setup-build-") as temporary:
        payload = Path(temporary) / "runtime.zip"
        shutil.copy2(args.runtime, payload)
        notice = Path(temporary) / "PYTHON-LICENSE.txt"
        shutil.copy2(python_license, notice)
        project_license = Path(temporary) / "PROJECT-AGPL.txt"
        shutil.copy2(root / "LICENSE", project_license)
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                        "--name", name, "--distpath", str(args.output.resolve()),
                        "--workpath", str(Path(temporary) / "work"), "--specpath", temporary,
                        "--paths", str(root / "packaging/installer"),
                        "--add-data", f"{payload};.",
                        "--add-data", f"{root / 'release.json'};.",
                        "--add-data", f"{notice};licenses",
                        "--add-data", f"{project_license};licenses",
                        "--add-data", f"{root / 'THIRD_PARTY_NOTICES.md'};licenses",
                        "--add-data", f"{root / 'licenses'};licenses",
                        str(root / "packaging/installer/setup.py")], check=True)
    result = args.output / f"{name}.exe"
    subprocess.run([str(result.resolve()), "--self-test"], check=True, timeout=90)
    return result


if __name__ == "__main__":
    main()
