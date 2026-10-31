# 发布维护

品牌、仓库和版本记录在 `release.json`。修改版本时，同时修改
`pyproject.toml` 与 `zotero/manifest.json`；打包器会拒绝版本不一致。
继续使用 `paperloom@paperloom.local` 插件 ID，保证旧安装可以升级。

## 本地构建

```powershell
python -m pip install -e ".[dev]" pyinstaller==6.21.0
python -m pytest tests/unit tests/integration -q
python packaging/build_release.py --installer --tag v0.3.1
```

Windows 安装器只能在 Windows x64 上构建。普通 `python zotero/build.py`
用于本地开发，会记录当前工程及 Python 路径；公开分发只使用
`python zotero/build.py --portable` 或 `packaging/build_release.py`。

`dist/release/` 生成以下文件：

- `.xpi`：可移植 Zotero 插件，没有开发机目录和 Python 路径。
- `-setup-*.exe`：内嵌引擎源码的 Windows 安装器，首次联网下载独立 Python、依赖和模型。
- `-runtime-*.zip`：安装器引擎源码，不包含用户论文、模型或 Python 环境。
- `-source-*.zip`：经过白名单筛选的公开源码，可在新目录运行测试和构建。
- `updates.json`：Zotero 自动更新清单，含插件 SHA-256 与适用 Zotero 版本。
- `SHA256SUMS`：本次发布文件校验值。

源码包和运行时包只读取打包脚本的文件白名单；不读取 Git 历史、
`workspace/`、`.env`、缓存、日志、OCR 结果或用户配置。
检测到开发机个人目录或常见密钥格式会终止构建，不会输出匹配值。

## GitHub Actions

普通提交与 PR 执行 Windows/Linux Python 测试和可移植打包。
推送与配置相符的 `vX.Y.Z` 标签后，Release 工作流执行测试、构建安装器，
并把文件上传到同名 GitHub Release。手动运行工作流只生成下载产物，不发布。
`release.json` 中 `prerelease: true` 会创建预览版。插件更新地址保留稳定版
`latest/download/updates.json` 通道；预览版手动安装，不自动推送给稳定版用户。

安装器目前没有 Windows 代码签名。不要将 PyInstaller 构建成功、
SHA-256 校验或通过测试描述为代码签名。OCR 首次真实安装验收仍需
符合要求的 NVIDIA 设备、驱动、网络与可用磁盘空间。

插件更新清单遵循 [Zotero 官方开发文档](https://www.zotero.org/support/dev/zotero_7_for_developers#updaterdf_updatesjson)。
