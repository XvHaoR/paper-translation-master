# 跨平台发布

`release.json`、`pyproject.toml` 和 `zotero/manifest.json` 使用同一版本。Windows x64 和 Apple 芯片 macOS 共用通用 XPI，平台 ZIP 分别包含自己的 Python 和依赖。升级时一起更新 XPI 和运行环境。

1. 运行 `python -m pip install -e ".[dev,layout,semantic]"`、`npm ci --prefix tests/dom` 和 `python -m pytest tests/unit tests/integration -q`。使用独立的 Zotero 测试文库验证阅读流程。
2. 可在 GitHub Actions 的 **Release** 工作流选择分支手动运行。Windows 和 macOS 在各自的平台构建完整包，校验 Python 和 E5 模型，使用包内环境计算英中文本的向量。Mac 还从解压后的 ZIP 执行安装脚本，通过本机服务解析 PDF、导出结果并验证服务退出。
3. 推送与版本号一致的 `vX.Y.Z` 标签。工作流待两端均通过后，检查 ZIP 完整性和 XPI 内的文件内容，统一发布两个平台包、单独 XPI、运行代码、源码和 `SHA256SUMS`。手动运行工作流可在发布前下载构建产物。

`packaging/build_release.py` 通过文件白名单打包。模型版本、下载文件及 SHA-256 在 `scripts/bundle_semantics.py`；平台依赖在 `pyproject.toml`。本地 OCR 的 GPU／CPU 引擎继续使用独立的部署方案。[引擎安装](INSTALLATION.md)
