"""pytest 共享 fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"

sys.path.insert(0, str(PROJECT_ROOT))

from app.config import reset_settings  # noqa: E402


@pytest.fixture()
def workspace(tmp_path):
    """独立的临时工作区。"""
    return tmp_path / "workspace"


@pytest.fixture(autouse=True)
def _reset_settings(workspace, monkeypatch):
    reset_settings()
    monkeypatch.setenv("PAPERBRIDGE_WORKSPACE", str(workspace))
    yield
    reset_settings()


@pytest.fixture()
def native_pdf() -> Path:
    p = FIXTURES / "sample_native.pdf"
    if not p.is_file():
        pytest.skip("fixture 未生成，请先运行 python tests/make_fixtures.py")
    return p


@pytest.fixture()
def scanned_pdf() -> Path:
    p = FIXTURES / "sample_scanned.pdf"
    if not p.is_file():
        pytest.skip("fixture 未生成，请先运行 python tests/make_fixtures.py")
    return p
