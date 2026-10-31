import json

import pytest

from scripts.runtime_config import runtime_environment


def test_installer_environment_does_not_override_unrelated_process_settings(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"schema": 1, "environment": {
        "PAPERBRIDGE_WORKSPACE": str(tmp_path / "workspace"), "HF_HOME": str(tmp_path / "models"),
        "PYTHONPATH": "unexpected", "PATH": "unexpected", "PAPERBRIDGE_OCR_USE_QUEUES": "true",
        "PADDLE_PDX_CACHE_HOME": "bad\0value", "MODELSCOPE_CACHE": 42,
    }}))
    assert runtime_environment(path) == {
        "PAPERBRIDGE_WORKSPACE": str(tmp_path / "workspace"), "HF_HOME": str(tmp_path / "models"),
        "PAPERBRIDGE_OCR_USE_QUEUES": "true",
    }
    assert runtime_environment(None) == {}


@pytest.mark.parametrize("data", [{"schema": 2}, {"schema": 1, "environment": []}])
def test_invalid_runtime_descriptor_has_actionable_error(tmp_path, data):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="安装器"):
        runtime_environment(path)
