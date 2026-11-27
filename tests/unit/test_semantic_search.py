from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.server import create_app
from app.services import semantic_search


def test_embedding_index_partitions_libraries_and_models_and_reuses_unchanged_papers(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    requests = []

    def fake_post(url, *, headers, json, timeout):
        assert url == "https://embedding.example/v1/embeddings"
        assert headers["Authorization"] == "Bearer test-key"
        requests.append(json["input"])
        data = [{"index": index, "embedding": [1, 0] if "折痕" in text or "crease" in text else [0, 1]}
                for index, text in enumerate(json["input"])]
        return httpx.Response(200, json={"data": data[::-1]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    scope = {"library": 4, "embedding": {"backend": "api", "url": "https://embedding.example/v1", "model": "fixture"}}
    auth = {"Authorization": "Bearer test-key"}
    paper = {**scope, "paper": "AA234567", "title": "Paper folding", "revision": "1" * 64,
             "pdf": "BB234567", "snapshot": "CC234567",
             "passages": [{"text": "a crease guides the next fold", "page": 7, "block": "p007-b001"},
                          {"text": "paper size and thickness", "page": 2}]}
    prefix = "/api/zotero/semantic/"
    with TestClient(create_app(Settings(workspace=tmp_path))) as client:
        assert client.post(prefix + "index", json=paper, headers=auth).json() == {"passages": 2}
        assert client.post(prefix + "index", json=paper, headers=auth).json() == {"passages": 0}
        assert len(requests) == 1
        assert client.post(prefix + "status", json=scope).json()["papers"] == {"AA234567": "1" * 64}
        response = client.post(prefix + "search", json={**scope, "query": "纸张折痕"}, headers=auth)
        hit, = response.json()["hits"]
        assert hit["snapshot"] == "CC234567"
        assert hit["passages"][0]["page"] == 7
        assert client.post(prefix + "search", json={**scope, "query": "纸张折痕", "library": 5}, headers=auth).json() == {"hits": []}
        local_scope = {**scope, "embedding": {"backend": "local"}}
        assert client.post(prefix + "status", json=local_scope).json() == {"papers": {}}
        new = {**paper, "revision": "2" * 64, "passages": [{"text": "Revised crease guide", "page": 3}]}
        assert client.post(prefix + "index", json=new, headers=auth).json() == {"passages": 1}
        hit, = client.post(prefix + "search", json={**scope, "query": "纸张折痕"}, headers=auth).json()["hits"]
        assert [part["page"] for part in hit["passages"]] == [3]
        # A title can occur in metadata and again on the PDF's title page.
        copies = [{"text": "crease guide", "label": "标题"},
                  {"text": "crease guide", "label": "标题", "page": 1, "block": "title"}]
        for revision, passages, expected in [
            ("3", copies, ["title"]),
            ("4", copies + [{"text": "Fold the corners", "label": "正文", "page": 2, "block": "fold"}], ["fold"]),
        ]:
            assert client.post(prefix + "index", json={**paper, "revision": revision * 64, "passages": passages}, headers=auth).is_success
            hit, = client.post(prefix + "search", json={**scope, "query": "折痕"}, headers=auth).json()["hits"]
            assert [part["block"] for part in hit["passages"]] == expected
    assert b"test-key" not in (tmp_path / "semantic-search.sqlite3").read_bytes()
