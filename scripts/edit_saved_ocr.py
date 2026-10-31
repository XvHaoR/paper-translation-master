"""Local OCR editor worker; never starts a server or loads OCR models."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ocr_edit import prepare_or_save

if __name__ == "__main__":
    try:
        request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        artifact = prepare_or_save(Path(request["saved"]), Path(request["output"]), Path(request["workspace"]), request)
        Path(request["result"]).write_text(json.dumps({"artifact": artifact}, ensure_ascii=False), encoding="utf-8")
    except Exception as error:
        Path(request["result"]).write_text(json.dumps({"error": str(error)}, ensure_ascii=False), encoding="utf-8")
        sys.exit(1)
