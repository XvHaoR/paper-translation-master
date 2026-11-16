"""Read only the supported runtime environment."""
import json
from pathlib import Path

def runtime_environment(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema") != 1 or not isinstance(data.get("environment", {}), dict):
        raise ValueError("解析安装信息格式无效，请重新运行安装脚本")
    return {key: value for key, value in data.get("environment", {}).items()
            if key == "PAPERBRIDGE_WORKSPACE" and isinstance(value, str) and "\0" not in value}
