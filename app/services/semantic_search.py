"""The Zotero library's local passage index and selectable embedding backend."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, PrivateAttr

MODEL = "multilingual-e5-small"
MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / MODEL
BATCH = 16


class EmbeddingSettings(BaseModel):
    backend: Literal["local", "api"] = "local"
    url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    _api_key: str = PrivateAttr(default="")

    def authorize(self, header: str) -> None:
        if header.startswith("Bearer "):
            self._api_key = header[7:]

    def endpoint(self) -> str:
        url = self.url.strip().rstrip("/")
        parts = urlsplit(url)
        if not self.model.strip() or not parts.hostname or parts.username is not None or parts.query or parts.fragment:
            raise ValueError("请填写嵌入接口地址和模型名")
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("嵌入 API 请使用 HTTPS，本机服务可用 HTTP")
        return url if parts.path.endswith("/embeddings") else url + "/embeddings"

    def space(self) -> str:
        identity = ("local", MODEL_REVISION) if self.backend == "local" else ("api", self.endpoint(), self.model.strip())
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class SearchScope(BaseModel):
    library: int = Field(gt=0)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)


class Passage(BaseModel):
    text: str = Field(max_length=1600)
    page: int = Field(default=0, ge=0)
    block: str = Field(default="", max_length=128)
    label: str = Field(default="正文", max_length=40)


class IndexPaper(SearchScope):
    paper: str = Field(pattern=r"^[A-Z0-9]{8}$")
    title: str = Field(max_length=5000)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf: str = Field(default="", pattern=r"^(?:[A-Z0-9]{8})?$")
    snapshot: str = Field(default="", pattern=r"^(?:[A-Z0-9]{8})?$")
    passages: list[Passage] = Field(max_length=12000)


class SearchQuery(SearchScope):
    query: str = Field(min_length=1, max_length=1500)


@lru_cache(maxsize=1)
def _local_tools(model_dir: Path):
    import onnxruntime
    from tokenizers import Tokenizer

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 4
    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
    tokenizer.enable_truncation(max_length=512)
    session = onnxruntime.InferenceSession(str(model_dir / "model.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
    return tokenizer, session


_model_lock = threading.Lock()


def embed(settings: EmbeddingSettings, texts: list[str], *, query: bool = False):
    import numpy as np

    if settings.backend == "local":
        if not all((MODEL_DIR / file).is_file() for file in ("model.onnx", "tokenizer.json")):
            raise ValueError("本地语义模型未安装，请安装当前版本的完整运行包")
        with _model_lock:
            tokenizer, session = _local_tools(MODEL_DIR)
            prefix = "query: " if query else "passage: "
            encoded = tokenizer.encode_batch([prefix + text for text in texts])
            ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
            inputs = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            hidden = session.run(["last_hidden_state"], {item.name: inputs[item.name] for item in session.get_inputs()})[0]
            vectors = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
    else:
        import httpx
        key = settings._api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            response = httpx.post(settings.endpoint(), headers=headers,
                                  json={"model": settings.model.strip(), "input": texts, "encoding_format": "float"},
                                  timeout=60)
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            if [item["index"] for item in data] != list(range(len(texts))):
                raise ValueError("invalid vector order")
            vectors = [item["embedding"] for item in data]
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"嵌入 API 返回 HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("嵌入 API 无法访问或返回格式无效") from exc
    vectors = np.asarray(vectors, dtype="<f4")
    if vectors.ndim != 2 or vectors.shape[0] != len(texts) or not 0 < vectors.shape[1] <= 8192:
        raise ValueError("嵌入模型返回的向量维度无效")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or not (norms > 0).all():
        raise ValueError("嵌入模型返回了无效向量")
    return vectors / norms


class SemanticIndex:
    def __init__(self, workspace: Path):
        self.path = workspace / "semantic-search.sqlite3"

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper (
                    space TEXT, library INTEGER, paper TEXT, title TEXT, revision TEXT, pdf TEXT, snapshot TEXT,
                    PRIMARY KEY (space, library, paper)
                );
                CREATE TABLE IF NOT EXISTS passage (
                    space TEXT, library INTEGER, paper TEXT, ordinal INTEGER,
                    text TEXT, page INTEGER, block TEXT, label TEXT, vector BLOB,
                    PRIMARY KEY (space, library, paper, ordinal),
                    FOREIGN KEY (space, library, paper) REFERENCES paper ON DELETE CASCADE
                );
            """)
            with db:
                yield db
        finally:
            db.close()

    def status(self, request: SearchScope) -> dict[str, str]:
        with self.connect() as db:
            return dict(db.execute("SELECT paper, revision FROM paper WHERE space=? AND library=?",
                                   (request.embedding.space(), request.library)))

    def index(self, request: IndexPaper) -> int:
        space, library, paper = request.embedding.space(), request.library, request.paper
        with self.connect() as db:
            prior = db.execute("SELECT revision FROM paper WHERE space=? AND library=? AND paper=?",
                               (space, library, paper)).fetchone()
            if prior == (request.revision,):
                return 0
        passages = [item for item in request.passages if item.text.strip()]
        rows = []
        for offset in range(0, len(passages), BATCH):
            batch = passages[offset:offset + BATCH]
            vectors = embed(request.embedding, [item.text for item in batch])
            rows.extend((space, library, paper, offset + i, item.text, item.page, item.block, item.label, vector.tobytes())
                        for i, (item, vector) in enumerate(zip(batch, vectors, strict=True)))
        with self.connect() as db:
            db.execute("DELETE FROM paper WHERE space=? AND library=? AND paper=?", (space, library, paper))
            db.execute("INSERT INTO paper VALUES (?,?,?,?,?,?,?)",
                       (space, library, paper, request.title, request.revision, request.pdf, request.snapshot))
            db.executemany("INSERT INTO passage VALUES (?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def search(self, request: SearchQuery) -> list[dict]:
        import numpy as np

        query = request.query.strip()
        if not query:
            raise ValueError("请输入你记得的论文内容")
        space = request.embedding.space()
        with self.connect() as db:
            rows = db.execute("""SELECT p.paper,p.title,p.pdf,p.snapshot,c.text,c.page,c.block,c.label,c.vector
                FROM passage c JOIN paper p USING (space,library,paper)
                WHERE c.space=? AND c.library=?""", (space, request.library)).fetchall()
        if not rows:
            return []
        vector = embed(request.embedding, [query], query=True)[0]
        if any(len(row[-1]) != vector.nbytes for row in rows):
            raise ValueError("接口输出维度已改，请使用新的模型标识重建索引")
        scores = np.frombuffer(b"".join(row[-1] for row in rows), dtype="<f4").reshape(len(rows), -1) @ vector
        title_labels = {"标题", "翻译标题"}
        with_excerpts = {row[0] for row in rows if row[7] not in title_labels}
        papers = {}
        for index in np.argsort(-scores):
            paper, title, pdf, snapshot, text, page, block, label, _ = rows[index]
            hit = papers.get(paper)
            if hit is None:
                if len(papers) >= 20:
                    continue
                hit = papers[paper] = {"paper": paper, "title": title, "pdf": pdf, "snapshot": snapshot, "passages": []}
            # Titles rank the paper and are already displayed in the heading.
            if label in title_labels and paper in with_excerpts:
                continue
            existing = next((part for part in hit["passages"] if part["text"] == text), None)
            if existing is not None:
                if page and not existing["page"]:
                    existing.update(page=page, block=block, label=label)
            elif len(hit["passages"]) < 2:
                hit["passages"].append({"text": text, "page": page, "block": block, "label": label})
        return list(papers.values())
