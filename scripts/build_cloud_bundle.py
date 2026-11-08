"""Build a Windows cloud bundle from a CPython embed ZIP and installed core wheels."""
import argparse
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
from zipfile import ZipFile, ZIP_DEFLATED

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def build(output, embed):
    spec = importlib.util.spec_from_file_location("release_builder", ROOT / "packaging/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    release = builder.BUILDER.load_release(ROOT)
    builder.validate_versions(ROOT, release)
    assert release["cloudPreview"]
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    for source in builder.files(ROOT):
        name = source.relative_to(ROOT)
        builder.audit(name.as_posix(), source.read_bytes())
        dest = runtime / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    python = output / "python"
    with ZipFile(embed) as archive:
        for entry in archive.infolist():
            assert Path(entry.filename).name == entry.filename and ":" not in entry.filename
        archive.extractall(python)
    (python / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n../runtime\n../runtime/scripts\n", encoding="ascii")
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    stack, used = list(map(Requirement, deps)), {}
    while stack:
        req = stack.pop()
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        dist = metadata.distribution(req.name)
        key = req.name.lower().replace("_", "-")
        if key in used:
            continue
        assert dist.version in req.specifier, (req, dist.version)
        used[key] = dist.version
        stack.extend(map(Requirement, dist.requires or []))
        for file in dist.files or []:
            if (".." in file.parts or file.is_absolute() or "__pycache__" in file.parts
                    or file.name in {"direct_url.json", "RECORD"}):
                continue
            target = python / "Lib/site-packages" / file
            source = dist.locate_file(file)
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    builder.BUILDER.build(portable=True, output=output / f"{release['slug']}-{release['version']}.xpi")
    (output / "Install-Cloud.cmd").write_text(
        '@echo off\r\n"%~dp0python\\python.exe" -X utf8 "%~dp0register-cloud.py"\r\n'
        'if errorlevel 1 echo Setup failed.\r\npause\r\n', encoding="ascii", newline="")
    (output / "register-cloud.py").write_text('''import json, os
from pathlib import Path
root = Path(__file__).resolve().parent
destination = Path(os.environ["LOCALAPPDATA"]) / "PaperTranslationMasterCloud"
destination.mkdir(parents=True, exist_ok=True)
config = {"schema":1, "projectRoot":str(root/"runtime"), "pythonPath":str(root/"python/python.exe"),
          "backend":"cloud", "environment":{"PAPERBRIDGE_WORKSPACE":str(destination/"workspace")}}
temporary = destination / "runtime.tmp"
temporary.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
temporary.replace(destination / "runtime.json")
print("Cloud runtime registered. Install the .xpi in Zotero and enter your AI Studio Access Token.")
''', encoding="utf-8")
    (output / "README.txt").write_text(f'''论文翻译大师 · 云端 OCR {release['version']}

1. 将整个压缩包解压到准备长期保留的位置，双击 Install-Cloud.cmd。
2. 在 Zotero → 工具 → 插件 → 从文件安装，选择包内的 XPI，然后重启 Zotero。
3. 在论文翻译大师设置中填写飞桨 AI Studio Access Token，点击「保存设置」。
4. 和本地版一样选择文献 OCR，或使用批量处理。自动 OCR 需在云端模式单独启用。

Windows 10/11 x64 运行包，内含轻量 Python 和阅读依赖，无需另装 Python、OCR 模型、CUDA、WSL 或显卡驱动。
识别需要联网，待识别的 PDF 会上传到飞桨 AI Studio，单次本地文件不超过 50 MB。
翻译仍使用原来配置的大模型接口；如需沿用旧翻译服务，在插件设置中填写该配置。
已有 Zotero 附件可继续查看。本包使用相同插件 ID，安装会替换旧版插件。可重新安装旧版 XPI 切回本地模型。
保留解压目录，移动后请重新运行 Install-Cloud.cmd。包内不包含密钥。源码与许可证见仓库。
''', encoding="utf-8")
    test = """
import importlib.util as u
from app.server import create_app
from app.config import Settings
from tempfile import TemporaryDirectory
with TemporaryDirectory() as workspace:
    assert create_app(Settings(workspace=workspace))
assert all(u.find_spec(name) is None for name in ("paddle", "paddleocr", "paddlex", "rapidocr", "onnxruntime"))
print("Embedded runtime verified without any local OCR models")
"""
    subprocess.run([str(python / "python.exe"), "-X", "utf8", "-c", test], cwd=runtime, check=True)
    subprocess.run([str(python / "python.exe"), "-X", "utf8", str(runtime / "scripts/zotero_service.py"), "--help"],
                   cwd=runtime, check=True, stdout=subprocess.DEVNULL)
    (output / "DEPENDENCIES.json").write_text(json.dumps(used, indent=2), encoding="utf-8")
    archive = output.parent / (output.name + ".zip")
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                bundle.write(path, output.name + "/" + path.relative_to(output).as_posix())
    print(archive, archive.stat().st_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embed", type=Path, required=True)
    args = parser.parse_args()
    build(args.output.resolve(), args.embed.resolve())
