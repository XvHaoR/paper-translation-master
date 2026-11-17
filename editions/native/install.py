"""Register a portable runtime, or create a virtual environment for source installs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def verify(python: Path) -> None:
    script = (
        "import sys;sys.path.insert(0,sys.argv[1]);import app;"
        "assert sys.version_info >= (3,12),'Python 3.12 or newer is required';"
        "import pymupdf,fastapi,httpx,uvicorn;"
        "from app.server import create_app;"
        "assert hasattr(pymupdf.Document,'to_json'),'Layout engine missing';"
        "assert tuple(map(int,pymupdf.VersionBind.split('.')[:2])) >= (2,1),'Layout engine too old';"
        "print('Native layout runtime ready')"
    )
    subprocess.run([str(python), '-X', 'utf8', '-c', script, str(ROOT/'runtime')], check=True)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check dependencies without registering anything')
    args = parser.parse_args()
    portable = ROOT/'python/python.exe'
    env_python = ROOT/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if portable.is_file():
        python = portable
    elif args.check:
        python = env_python if env_python.is_file() else Path(sys.executable)
    else:
        if sys.version_info < (3, 12):
            raise SystemExit('Python 3.12 or newer is required.')
        if not env_python.is_file():
            subprocess.run([sys.executable, '-m', 'venv', str(ROOT/'.venv')], check=True)
        python = env_python
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT/'runtime/requirements.txt')], check=True)
    verify(python)
    if args.check:
        return
    # Git checkouts exclude dist/. Build the add-on before registering the runtime
    # so a source install always provides the XPI referenced by the instructions.
    subprocess.run([str(python), '-X', 'utf8', str(ROOT/'tools/build.py')], check=True)
    if os.name == 'nt':
        base = Path(os.environ['LOCALAPPDATA'])/'PaperTranslationMasterNative'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'PaperTranslationMasterNative'
    base.mkdir(parents=True, exist_ok=True)
    descriptor = {
        'schema': 1, 'projectRoot': str(ROOT/'runtime'), 'pythonPath': str(python),
        'backend': 'local', 'environment': {'PAPERBRIDGE_WORKSPACE': str(base/'workspace')},
    }
    target = base/'runtime.json'
    temporary = base/'runtime.json.tmp'
    temporary.write_text(json.dumps(descriptor, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    print('Runtime registered. Keep this folder in place.')
    print('Install dist/paper-translation-master-0.4.2.xpi in Zotero, then use Detect engine in settings.')

if __name__ == '__main__':
    main()
