"""DOM contracts protect independent PDF actions when presentation groups change."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", ["abstracts", "captions", "boundaries", "orphan-captions",
                                  "footnotes", "mapping-and-export", "editor-types"])
def test_paper_layout_and_editor_dom(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for saved-reader DOM contracts")
    result = subprocess.run([node, str(ROOT / "tests/dom/paper-layout.cjs"), case],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr + "\nInstall DOM dev dependencies with npm ci --prefix tests/dom"
    assert f"PASS {case}" in result.stdout
