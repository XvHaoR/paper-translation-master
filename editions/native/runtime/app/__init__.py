"""Use the parser bundled with this runtime, independent of the older install."""
import sys
from pathlib import Path

_vendor = Path(__file__).resolve().parents[1] / "vendor"
if _vendor.is_dir():
    sys.path.insert(0, str(_vendor))
