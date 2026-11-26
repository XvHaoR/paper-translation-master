"""Build a standalone arm64 macOS bundle and its Zotero XPI."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

INSTALL = """#!/bin/zsh
set -eu
if [[ "$(/usr/sbin/sysctl -in hw.optional.arm64)" != 1 ]]; then
    echo 'This package requires a Mac with Apple silicon.'
    exit 1
fi
cd -- "${0:A:h}"
status=0
./python/bin/python3 -I -X utf8 ./install.py || status=$?
if [[ $status != 0 ]]; then
    echo 'Runtime registration failed. See the error above.'
fi
if [[ "${1:-}" != --no-pause && -t 0 ]]; then
    read -r '?Press Return to close. ' || true
fi
exit "$status"
"""


def build(output: Path, python_archive: Path) -> Path:
    if (sys.platform, platform.machine()) != ("darwin", "arm64"):
        raise RuntimeError("Build this package on an Apple silicon Mac")
    spec = importlib.util.spec_from_file_location("release_builder", ROOT / "packaging/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    release = builder.BUILDER.load_release(ROOT)
    builder.validate_versions(ROOT, release)
    if not release.get("unified"):
        raise ValueError("The macOS bundle requires a unified release")

    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["/usr/bin/tar", "-xf", str(python_archive), "-C", str(output)], check=True)
    python = output / "python/bin/python3"
    purelib = subprocess.check_output([str(python), "-I", "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    if not Path(purelib).resolve().is_relative_to(output / "python"):
        raise RuntimeError("Bundled Python is not relocatable")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    dependencies = project["dependencies"] + project["optional-dependencies"]["layout"]
    subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-cache-dir", "--only-binary=:all:",
                    "--platform", "macosx_14_0_arm64", "--platform", "macosx_14_0_universal2",
                    "--target", purelib, *dependencies, "onnxruntime==1.28.0"], check=True)

    runtime = output / "runtime"
    for source in builder.files(ROOT):
        relative = source.relative_to(ROOT)
        builder.audit(relative.as_posix(), source.read_bytes())
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    subprocess.run([str(python), "-s", "-X", "utf8", "-c",
                    "import pymupdf; from app.server import create_app; "
                    "assert hasattr(pymupdf.Document, 'to_json')"], cwd=runtime, check=True)
    builder.BUILDER.build(portable=True, output=output / f"{release['slug']}-{release['version']}.xpi")
    shutil.copyfile(ROOT / "scripts/install_unified.py", output / "install.py")
    shutil.copyfile(ROOT / "docs/MACOS.md", output / "README.md")
    installer = output / "Install.command"
    installer.write_text(INSTALL, encoding="ascii", newline="\n")
    installer.chmod(0o755)
    archive = output.parent / (output.name + ".zip")
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(output), str(archive)], check=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.output.resolve(), args.python_archive.resolve()))
