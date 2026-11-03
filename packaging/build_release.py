"""Build audited, portable release files without reading local user data.

python packaging/build_release.py --installer  # Windows + PyInstaller
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zotero_xpi_builder", ROOT / "zotero/build.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

TEXT_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".css", ".html", ".xhtml", ".json", ".svg",
                   ".md", ".toml", ".txt", ".yml", ".yaml", ".ps1", ".cmd", ".sh"}
STATIC_EXTENSIONS = {".js", ".css", ".html", ".json", ".svg", ".png", ".woff", ".woff2"}
INSTALLER_EXTENSIONS = {".py", ".txt", ".md", ".ps1", ".cmd", ".json"}
RUNTIME_SCRIPTS = {"scripts/launcher.py", "scripts/zotero_service.py", "scripts/ocr_health.py",
                   "scripts/process_lifetime.py", "scripts/edit_saved_ocr.py", "scripts/runtime_config.py"}
ROOT_FILES = {"pyproject.toml", "release.json", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"}
SOURCE_ROOT_FILES = ROOT_FILES | {".gitignore"}
PUBLIC_DOCS = {"docs/INSTALLATION.md", "docs/RELEASING.md", "docs/READER_ARCHITECTURE.md", "docs/RAPIDOCR_EDITION.md"}
PRIVATE_PATTERNS = [
    re.compile(rb"[A-Za-z]:[\\/]+(?:Users[\\/]+(?!Public(?:[\\/]|$)|Default(?:[\\/]|$))[^\\/\s\"']+[\\/]+|claude_test[\\/]+)", re.I),
    re.compile(rb"(?:sk-[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,})"),
]


def eligible(relative: str, *, source: bool = False) -> bool:
    """Explicit directory/type allowlist; never traverse arbitrary tracked files."""
    path = Path(relative)
    if any(part in {"__pycache__", "node_modules", "dist", ".venv", ".git"} for part in path.parts):
        return False
    if relative in (SOURCE_ROOT_FILES if source else ROOT_FILES) or relative in PUBLIC_DOCS:
        return True
    if relative.startswith("app/"):
        return (path.suffix == ".py"
                or (relative.startswith("app/static/") and path.suffix in STATIC_EXTENSIONS)
                or (relative.startswith("app/rendering/templates/") and path.suffix in {".html", ".j2", ".jinja2"})
                or (relative.startswith("app/static/mathjax/") and path.name in {"LICENSE", "NOTICE"}))
    if relative.startswith("licenses/"):
        return path.suffix in {".txt", ".md"} or path.name in {"LICENSE", "NOTICE"}
    if relative in RUNTIME_SCRIPTS:
        return True
    if relative.startswith("packaging/installer/"):
        return len(path.parts) == 3 and path.suffix in INSTALLER_EXTENSIONS
    if not source:
        return False
    if relative == "packaging/build_release.py":
        return True
    if relative.startswith("zotero/"):
        return path.suffix in {".py", ".js", ".mjs", ".css", ".xhtml", ".json", ".md", ".svg", ".png"}
    if relative.startswith(".github/workflows/"):
        return path.suffix in {".yml", ".yaml"}
    if relative.startswith("extension/"):
        return path.suffix in {".js", ".mjs", ".css", ".html", ".json", ".md", ".svg", ".png"}
    if relative.startswith("tests/"):
        return path.suffix in {".py", ".js", ".cjs", ".json", ".md"} or relative in {
            "tests/fixtures/sample_native.pdf", "tests/fixtures/sample_scanned.pdf"}
    return False


def files(root: Path, *, source: bool = False) -> list[Path]:
    candidates: set[Path] = set()
    for relative in SOURCE_ROOT_FILES | RUNTIME_SCRIPTS | PUBLIC_DOCS:
        if (root / relative).is_file():
            candidates.add(root / relative)
    for directory in ("app", "packaging/installer", "licenses", "zotero", "tests", ".github/workflows", "extension"):
        if not source and directory in {"zotero", "tests", ".github/workflows", "extension"}:
            continue
        if (root / directory).is_dir():
            candidates.update(path for path in (root / directory).rglob("*") if path.is_file())
    if source:
        candidates.add(root / "packaging/build_release.py")
    result = sorted(path for path in candidates if eligible(path.relative_to(root).as_posix(), source=source))
    for path in result:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Release cannot include a link outside the source tree: {path}")
    return result


def audit(name: str, data: bytes) -> None:
    """Fail closed on developer home paths and common real credential formats."""
    if Path(name).suffix not in TEXT_EXTENSIONS and name != "LICENSE":
        return
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(data):
            # Avoid printing the matched value because it could be a credential.
            raise ValueError(f"Private path or possible credential in release file: {name}")


def archive(root: Path, output: Path, *, source: bool = False) -> None:
    with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
        for path in files(root, source=source):
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            audit(relative, data)
            BUILDER.write_entry(bundle, relative, data)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def update_manifest(release: dict, manifest: dict, xpi: Path) -> dict:
    app = manifest["applications"]["zotero"]
    return {"addons": {app["id"]: {"updates": [{
        "version": release["version"],
        "update_link": f"https://github.com/{release['repository']}/releases/download/v{release['version']}/{xpi.name}",
        "update_hash": "sha256:" + digest(xpi),
        "applications": {"zotero": {key: app[key] for key in ("strict_min_version", "strict_max_version")}},
    }]}}}


def validate_versions(root: Path, release: dict, tag: str | None = None) -> dict:
    manifest = json.loads((root / "zotero/manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if {release["version"], manifest["version"], project["project"]["version"]} != {release["version"]}:
        raise ValueError("release.json, pyproject.toml and Zotero manifest versions must match")
    if tag is not None and tag != "v" + release["version"]:
        raise ValueError("Release tag must equal v + release.json version")
    if manifest["applications"]["zotero"]["id"] != "paperloom@paperloom.local":
        raise ValueError("Do not change the existing Zotero add-on ID")
    return manifest


def build_release(*, root: Path = ROOT, output: Path | None = None,
                  installer: bool = False, tag: str | None = None) -> list[Path]:
    release = BUILDER.load_release(root)
    manifest = validate_versions(root, release, tag)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if not (root / name).is_file():
            raise ValueError(f"Missing required release notice: {name}")
    output = output or root / "dist/release"
    output.mkdir(parents=True, exist_ok=True)
    version, slug = release["version"], release["slug"]
    xpi = BUILDER.build(portable=True, root=root / "zotero", release=release,
                        output=output / f"{slug}-{version}.xpi")
    with ZipFile(xpi) as bundle:
        for name in bundle.namelist():
            audit(name, bundle.read(name))
    runtime = output / f"{slug}-runtime-{version}.zip"
    source = output / f"{slug}-source-{version}.zip"
    archive(root, runtime)
    archive(root, source, source=True)
    artifacts = [xpi, runtime, source]
    if installer:
        subprocess.run([sys.executable, str(root / "packaging/installer/build.py"),
                        "--runtime", str(runtime), "--output", str(output)], check=True, cwd=root)
        executable = output / f"{slug}-setup-{version}.exe"
        if not executable.is_file():
            raise ValueError("Installer build did not produce the expected executable")
        artifacts.append(executable)
        if release.get("cpuInstaller"):
            subprocess.run([sys.executable,str(root / "packaging/installer/build.py"),
                            "--runtime",str(runtime),"--output",str(output),"--edition","rapidocr"],check=True,cwd=root)
            cpu_executable=output / f"paper-translation-master-rapidocr-setup-{version}.exe"
            if not cpu_executable.is_file():
                raise ValueError("CPU installer build did not produce the expected executable")
            artifacts.append(cpu_executable)
    updates = output / "updates.json"
    updates.write_text(json.dumps(update_manifest(release, manifest, xpi), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts.append(updates)
    checksums = output / "SHA256SUMS"
    checksums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in artifacts), encoding="ascii")
    artifacts.append(checksums)
    notes = output / "RELEASE_NOTES.md"
    preview_note = "安装器预览版：预览版需手动升级，不向稳定版自动更新通道推送。\n\n" if release.get("prerelease") else ""
    notes.write_text(
        f"{release['name']} {version}\n\n" + preview_note +
        "".join(f"- {change}\n" for change in release.get("changes", [])) + "\n" +
        "- 下载 `.xpi`，在 Zotero → 工具 → 插件中选择从文件安装。\n"
        "- Windows GPU 用户下载 `paper-translation-master-setup-`，CPU 用户下载 `paper-translation-master-rapidocr-setup-`；两个安装器均自动准备独立 Python、依赖和模型，首次需要联网。\n"
        "- 从旧版升级需同时更新 XPI 和对应引擎代码；运行同类 0.5.0 安装器可复用原安装位置、模型与下载缓存。源码／WSL 用户同步更新本项目代码。\n"
        "- 在插件设置中填写自己的大模型 API 地址、模型名称及密钥，按需翻译。\n"
        "- 已保存的 OCR 和译文可离线查看；批量处理在后台完成。\n\n" +
        ("本版为 RapidOCR 完整文档版：Windows 10/11 x64，CPU 即可，建议至少 8 GB 内存，预留 5 GB 磁盘。无需 NVIDIA、CUDA 或 WSL。保留版面、行内/行间公式和表格模型。\n"
         if release.get("ocrMode") == "rapidocr" else
         "GPU 安装器支持 Windows 10/11 x64 + NVIDIA GPU（至少 8 GB 显存，推荐 12 GB；CC 7.x–9.x，驱动至少 560.76）。RapidOCR CPU 安装器无需显卡，建议至少 8 GB 内存及 5 GB 磁盘。其他平台需自行配置 OCR。\n") +
        "插件作为 0.5.0 正式版发布。Windows 安装器全新系统的完整安装覆盖仍在补充，实际性能取决于设备与论文，详见仓库安装说明。\n"
        "安装器暂未进行 Windows 代码签名，系统可能显示未知发布者。可用 SHA256SUMS 校验下载文件。\n"
        "首次模型下载和安装可能需要较长时间，安装窗口会显示进度及错误日志。\n",
        encoding="utf-8")
    artifacts.append(notes)
    print("\n".join(str(path) for path in artifacts))
    return artifacts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--installer", action="store_true", help="Build Windows installer with embedded runtime ZIP")
    parser.add_argument("--tag", help="Verify a vX.Y.Z release tag")
    args = parser.parse_args()
    build_release(output=args.output, installer=args.installer, tag=args.tag)
