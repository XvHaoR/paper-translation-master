"""Build a Zotero XPI; use --portable for distributable, path-free packages."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent


def load_release(root: Path) -> dict:
    data = json.loads((root / "release.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", data["slug"]):
        raise ValueError("Invalid release slug")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data["repository"]):
        raise ValueError("Invalid GitHub repository")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9.-]+)?", data["version"]):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", data["runtimeDirectory"]):
        raise ValueError("Invalid runtime directory name")
    if "prerelease" in data and not isinstance(data["prerelease"], bool):
        raise ValueError("prerelease must be a JSON boolean")
    return data


def write_entry(bundle: ZipFile, name: str, data: bytes | str) -> None:
    """Fixed timestamp and permissions make repeated builds reproducible."""
    info = ZipInfo(name, (2020, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    bundle.writestr(info, data)


def build(*, portable: bool = False, output: Path | None = None,
          root: Path = ROOT, release: dict | None = None, runtime_descriptor: Path | None = None) -> Path:
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if portable and runtime_descriptor:
        raise ValueError("A pinned local runtime cannot be a portable package")
    if portable:
        release = release or load_release(root.parent)
        if manifest["version"] != release["version"]:
            raise ValueError("manifest.json and release.json versions do not match")
        manifest["name"] = release["name"]
        manifest["applications"]["zotero"]["update_url"] = (
            f"https://github.com/{release['repository']}/releases/latest/download/updates.json")
        # Preserve the add-on ID, preferences and chrome namespace for upgrades.
        config = {"projectRoot": "", "pythonCandidates": [], "defaultBackend": "local",
                  "runtimeDirectory": release["runtimeDirectory"],
                  "ocrMode": release.get("ocrMode", "official"),
                  "releaseURL": f"https://github.com/{release['repository']}/releases",
                  "releaseVersion": release["version"], "displayName": release["name"]}
        config["minimumExportSchema"] = release.get("minimumExportSchema", 7)
        if release.get("cloudPreview"):
            config.update(cloudPreview=True, defaultBackend="cloud")
            manifest["applications"]["zotero"]["update_url"] = f"https://github.com/{release['repository']}/releases/latest/download/cloud-updates.json"
        if release.get("localOnly"):
            manifest["applications"]["zotero"]["update_url"] = "https://127.0.0.1/paperloom-html-preview-updates.json"
            config["localHTMLPreview"] = True
        filename = f"{release['slug']}-{manifest['version']}.xpi"
    else:
        candidates = [root.parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
                      Path.home() / ".codex" / "paddle-ocr-env" / "Scripts" / "python.exe", Path(sys.executable)]
        config = {"projectRoot": str(root.parent),
                  "pythonCandidates": [str(path) for path in candidates if path.is_file()],
                  "defaultBackend": "wsl" if os.name == "nt" else "local"}
        filename = f"paperloom-{manifest['version']}.xpi"
    if runtime_descriptor:
        release = release or load_release(root.parent)
        if not release.get("localOnly") or release["version"] != manifest["version"]:
            raise ValueError("Pinned runtimes require a matching local-only release")
        descriptor = Path(runtime_descriptor).resolve()
        runtime = json.loads(descriptor.read_text(encoding="utf-8-sig"))
        project, python = Path(runtime["projectRoot"]), Path(runtime["pythonPath"])
        if (runtime.get("schema") != 1 or runtime.get("backend") not in {"local", "wsl"}
                or not project.is_absolute() or not (project / "scripts/zotero_service.py").is_file()
                or not python.is_absolute() or not python.is_file()):
            raise ValueError("Invalid local preview runtime")
        config.update(projectRoot=str(project), pythonCandidates=[str(python)], defaultBackend=runtime["backend"],
                      localHTMLPreview=True, localRuntimeDescriptor=str(descriptor),
                      minimumExportSchema=release.get("minimumExportSchema", 7), releaseVersion=release["version"])
        manifest["applications"]["zotero"]["update_url"] = "https://127.0.0.1/paperloom-html-preview-updates.json"
        filename = f"{release['slug']}-{manifest['version']}.xpi"
    output = Path(output) if output else root / "dist" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
        write_entry(bundle, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        write_entry(bundle, "bootstrap.js", (root / "bootstrap.js").read_bytes())
        for directory in ("modules", "content"):
            for path in sorted((root / directory).rglob("*")):
                if path.is_file() and path.suffix in {".js", ".mjs", ".css", ".xhtml", ".svg", ".png", ".json"}:
                    if path.is_symlink():
                        raise ValueError(f"Symlinks cannot be packaged: {path}")
                    write_entry(bundle, path.relative_to(root).as_posix(), path.read_bytes())
        write_entry(bundle, "config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        write_entry(bundle, "content/table-excel.js", (root.parent / "app/static/table_excel.js").read_bytes())
        write_entry(bundle, "content/reader.css", (root.parent / "app/static/offline_reader.css").read_bytes()
                    + b"\n" + (root.parent / "app/static/paper_reader.css").read_bytes())
        write_entry(bundle, "content/paper-reader.css", (root.parent / "app/static/paper_reader.css").read_bytes())
        module = (root / "content/paper-layout.mjs").read_text(encoding="utf-8")
        runtime = module.replace("export { enhance, exportPage };", "window.PaperLoomHTML = { enhance, exportPage };\nenhance(document);")
        write_entry(bundle, "content/paper-reader.js", '(() => {"use strict";\n' + runtime + '\n})();')
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            if (root.parent / name).is_file():
                write_entry(bundle, name, (root.parent / name).read_bytes())
    print(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", action="store_true", help="Exclude developer paths and use GitHub updates")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runtime-descriptor", type=Path, help="Pin a local-only exploration build to a verified runtime")
    args = parser.parse_args()
    build(portable=args.portable, output=args.output, runtime_descriptor=args.runtime_descriptor)
