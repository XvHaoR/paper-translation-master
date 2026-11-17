"""Bootstrap the isolated runtime even with an embedded Python _pth file."""
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
runpy.run_module("app.cli", run_name="__main__")
