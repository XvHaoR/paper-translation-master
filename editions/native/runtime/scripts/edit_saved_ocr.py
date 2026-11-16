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
        from app.services.translation_layout import synchronize
        translations, warnings = [], []
        for index, entry in enumerate(request.get("translations", [])):
            try:
                synced = synchronize(Path(entry["saved"]), Path(request["saved"]), Path(request["output"]),
                                     Path(request["output"]).parent / f"translation-{index}")
                if synced["changed"]:
                    translations.append({**synced, "language": entry["language"], "attachmentID": entry["attachmentID"]})
            except Exception as error:
                warnings.append(f"{entry.get('language', '')} 译文类别同步未完成：{error}")
        Path(request["result"]).write_text(json.dumps({"artifact": artifact, "translations": translations,
            "warnings": warnings}, ensure_ascii=False), encoding="utf-8")
    except Exception as error:
        Path(request["result"]).write_text(json.dumps({"error": str(error)}, ensure_ascii=False), encoding="utf-8")
        sys.exit(1)
