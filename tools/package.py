"""Build the standalone native source bundle without local runtime/user data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.gitignore', 'Install.cmd', 'install.py', 'README.md', 'CHANGELOG.md',
              'LICENSE', 'THIRD_PARTY_NOTICES.md'}
RUNTIME_FILES = {'runtime/requirements.txt', 'runtime/pyproject.toml', 'runtime/release.json'}
ALLOWED_DIRS = {'addon', 'docs', 'licenses', 'tests', 'tools'}
EXCLUDED = {'__pycache__', '.git', '.venv', 'vendor', 'python', 'node_modules',
            'workspace', 'documents', 'dist', 'log', '.pytest_cache'}
PRIVATE = [
    re.compile(rb'[A-Za-z]:[\\/]+Users[\\/]+(?!Public[\\/]|Default[\\/])[^\\/\s"\']+[\\/]', re.I),
    re.compile(rb'(?:sk-[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,})'),
]
TEXT = {'.py', '.js', '.mjs', '.cjs', '.css', '.html', '.xhtml', '.json', '.md',
        '.toml', '.txt', '.cmd', '.svg'}


def eligible(relative: Path) -> bool:
    name = relative.as_posix()
    if EXCLUDED.intersection(relative.parts) or relative.suffix in {'.pyc', '.pyo', '.pdf', '.zip', '.xpi', '.log', '.lock', '.pid'}:
        return False
    if relative.name in {'runtime.json', 'settings.json'} or relative.name.startswith(('.env', 'service-state-', 'service-lease-')):
        return False
    if name not in ROOT_FILES and relative.suffix not in TEXT | {'.png', '.woff', '.woff2'} and relative.name not in {'LICENSE', 'NOTICE'}:
        return False
    return (name in ROOT_FILES or name in RUNTIME_FILES or relative.parts[0] in ALLOWED_DIRS
            or name.startswith(('runtime/app/', 'runtime/scripts/')))


def audit(name: str, data: bytes) -> None:
    if Path(name).suffix in TEXT or Path(name).name in {'LICENSE', 'NOTICE'}:
        if any(pattern.search(data) for pattern in PRIVATE):
            raise ValueError(f'Private path or possible credential in {name}')


def source_files(root: Path) -> list[Path]:
    selected = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or not eligible(path.relative_to(root)):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Link outside source tree: {path.name}')
        audit(path.relative_to(root).as_posix(), path.read_bytes())
        selected.append(path)
    return selected


def entry(bundle, name, data):
    info = zipfile.ZipInfo(name, (2026, 11, 14, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    bundle.writestr(info, data)


def release_version(root: Path, tag: str | None = None) -> str:
    version = json.loads((root / 'runtime/release.json').read_text('utf-8'))['version']
    manifest_version = json.loads((root / 'addon/manifest.json').read_text('utf-8'))['version']
    project_version = tomllib.loads((root / 'runtime/pyproject.toml').read_text('utf-8'))['project']['version']
    if len({version, manifest_version, project_version}) != 1:
        raise ValueError('Runtime, add-on and Python package versions differ')
    if tag is not None and tag != f'v{version}':
        raise ValueError(f'Release tag must be v{version}, received {tag}')
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', help='Require this Git tag to match all release versions')
    args = parser.parse_args()
    version = release_version(ROOT, args.tag)
    subprocess.run([sys.executable, str(ROOT / 'tools/build.py')], check=True)
    output = ROOT / 'dist/release'
    output.mkdir(parents=True, exist_ok=True)
    xpi = output / f'paper-translation-master-{version}.xpi'
    shutil.copyfile(ROOT / 'dist' / xpi.name, xpi)
    with zipfile.ZipFile(xpi) as bundle:
        for name in bundle.namelist():
            audit(name, bundle.read(name))
    source = output / f'paper-translation-master-{version}-source.zip'
    prefix = f'paper-translation-master-{version}/'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in source_files(ROOT):
            entry(bundle, prefix + path.relative_to(ROOT).as_posix(), path.read_bytes())
        entry(bundle, prefix + 'dist/' + xpi.name, xpi.read_bytes())
    for file in (xpi, source):
        with zipfile.ZipFile(file) as bundle:
            if bundle.testzip() is not None:
                raise ValueError(f'Archive integrity check failed: {file.name}')
    (output / 'SHA256SUMS.txt').write_text(''.join(
        f'{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n' for file in (xpi, source)), 'ascii')
    print(f'Release artifacts: {output}')


if __name__ == '__main__':
    main()
