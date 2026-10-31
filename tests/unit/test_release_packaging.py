"""Distribution packages must be portable and contain no local documents."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_builder_under_test", ROOT / "packaging/build_release.py")
release_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_builder)


@pytest.fixture
def release_tree(tmp_path):
    for directory in ("app", "zotero"):
        shutil.copytree(ROOT / directory, tmp_path / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "dist"))
    release = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "zotero/manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = release["version"]
    (tmp_path / "zotero/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "release.json").write_text(json.dumps(release), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{release["version"]}"\n', encoding="utf-8")
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        (tmp_path / name).write_text("Test release", encoding="utf-8")
    (tmp_path / "app/rendering/templates").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app/rendering/templates/document.html").write_text("<p>{{ title }}</p>", encoding="utf-8")
    return tmp_path


def test_portable_xpi_preserves_upgrade_id_without_local_paths(release_tree):
    output = release_builder.BUILDER.build(portable=True, root=release_tree / "zotero")
    with ZipFile(output) as bundle:
        config = json.loads(bundle.read("config.json"))
        manifest = json.loads(bundle.read("manifest.json"))
        assert config["projectRoot"] == ""
        assert config["pythonCandidates"] == []
        assert config["runtimeDirectory"] == "PaperTranslationMaster"
        assert manifest["applications"]["zotero"]["id"] == "paperloom@paperloom.local"
        assert manifest["applications"]["zotero"]["update_url"].endswith("/releases/latest/download/updates.json")
        for name in bundle.namelist():
            release_builder.audit(name, bundle.read(name))


def test_release_allowlist_omits_documents_secrets_caches_and_builds(release_tree):
    for relative in ("workspace/paper.pdf", ".env", ".git/config", "app/__pycache__/cached.pyc",
                     "zotero/dist/old.xpi", "scripts/launcher.lock", "packaging/installer/cache/model.json"):
        path = release_tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")
    # Nested arbitrary installer JSON must not be included, even under an allowed directory.
    output = release_tree / "runtime.zip"
    release_builder.archive(release_tree, output)
    with ZipFile(output) as bundle:
        names = bundle.namelist()
        assert "app/server.py" in names
        assert "app/rendering/templates/document.html" in names
        assert "zotero/manifest.json" not in names
        assert not any(name.startswith(("workspace/", ".git/", "zotero/dist/")) for name in names)
        assert ".env" not in names
        assert not any("__pycache__" in name or "cache/model.json" in name for name in names)


def test_release_hashes_and_update_manifest(release_tree):
    # The source pack also includes the build script itself.
    (release_tree / "packaging").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "packaging/build_release.py", release_tree / "packaging/build_release.py")
    artifacts = release_builder.build_release(root=release_tree, tag="v0.3.0")
    output = artifacts[0].parent
    checksums = (output / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    for line in checksums:
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected
    updates = json.loads((output / "updates.json").read_text(encoding="utf-8"))
    update = updates["addons"]["paperloom@paperloom.local"]["updates"][0]
    assert update["version"] == "0.3.0"
    assert update["update_hash"] == "sha256:" + hashlib.sha256(artifacts[0].read_bytes()).hexdigest()
    assert update["applications"]["zotero"]["strict_max_version"]
    assert "/releases/download/v0.3.0/" in update["update_link"]


def test_tag_and_version_mismatch_fail_before_build(release_tree):
    release = release_builder.BUILDER.load_release(release_tree)
    with pytest.raises(ValueError, match="tag"):
        release_builder.validate_versions(release_tree, release, "v0.9.0")
    release["version"] = "0.9.0"
    with pytest.raises(ValueError, match="versions"):
        release_builder.validate_versions(release_tree, release)


def test_private_paths_fail_without_echoing_secret():
    private = bytes([67, 58, 92]) + b'Users' + bytes([92]) + b'somebody' + bytes([92]) + b'paper.pdf'
    with pytest.raises(ValueError) as error:
        release_builder.audit("config.json", private)
    assert "somebody" not in str(error.value)


def test_repeated_portable_build_is_reproducible(release_tree):
    one = release_builder.BUILDER.build(portable=True, root=release_tree / "zotero", output=release_tree / "one.xpi")
    two = release_builder.BUILDER.build(portable=True, root=release_tree / "zotero", output=release_tree / "two.xpi")
    assert one.read_bytes() == two.read_bytes()
