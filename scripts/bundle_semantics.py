"""Install the pinned E5 language model in platform bundles and verify offline inference."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
UPSTREAM = f"https://huggingface.co/Xenova/multilingual-e5-small/resolve/{REVISION}/"
FILES = {
    "model.onnx": ("onnx/model_quantized.onnx", "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193"),
    "tokenizer.json": ("tokenizer.json", "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
}


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install_model(runtime):
    from tempfile import NamedTemporaryFile
    dest = runtime / "models" / "multilingual-e5-small"
    dest.mkdir(parents=True, exist_ok=True)
    for name, (source, digest) in FILES.items():
        target = dest / name
        if target.is_file() and checksum(target) == digest:
            continue
        with NamedTemporaryFile(dir=dest, delete=False) as temp:
            partial = Path(temp.name)
        try:
            with urllib.request.urlopen(UPSTREAM + source, timeout=180) as response, partial.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if checksum(partial) != digest:
                raise ValueError(f"Model checksum mismatch for {name}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    shutil.copyfile(ROOT / "licenses/multilingual-e5-small-LICENSE.txt", dest / "LICENSE")
    (dest / "MODEL.md").write_text(f"Multilingual E5 Small (MIT). Original: https://huggingface.co/intfloat/multilingual-e5-small\n"
                                 f"Quantized ONNX: https://huggingface.co/Xenova/multilingual-e5-small/tree/{REVISION}\n", encoding="utf-8")


def verify_model(python, runtime):
    code = """from app.services.semantic_search import embed, EmbeddingSettings
import numpy as np
v = embed(EmbeddingSettings(), ["paper folding", "折纸"], query=True)
assert np.shape(v) == (2, 384) and np.isfinite(v).all() and (np.linalg.norm(v, axis=1) > 0.99).all()
"""
    subprocess.run([str(python), "-s", "-X", "utf8", "-c", code], cwd=runtime, check=True)


if __name__ == "__main__":
    install_model(Path(sys.argv[1]).resolve())
