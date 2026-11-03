# 发布维护

当前推荐发布为 0.5.0 HTML 正式版。品牌、版本、仓库和发行方式由 `release.json` 管理，与 `pyproject.toml`、`zotero/manifest.json` 保持一致。
插件 ID 保持 `paperloom@paperloom.local`，保存的附件、标签和外观偏好可以沿用。

## 构建与验证

```powershell
python -m pip install -e ".[dev]" pyinstaller==6.21.0
npm ci --prefix tests/dom
python -m pytest tests/unit tests/integration -q
python packaging/build_release.py --installer --tag v0.5.0
```

Windows x64 构建同时提供 GPU 和 RapidOCR CPU 安装器，使用相同、已审计的 runtime 源码包，分别嵌入对应的引擎类型和安装目录。CPU 安装器不会要求或下载 Paddle GPU 环境。
`tests/dom` 的 jsdom 回归不加载浏览器资源或 OCR 模型；原生阅读器回归使用独立 Zotero 配置和文献库，运行 `python tests/zotero/run_smoke.py`。

`dist/release` 包含：

- 通用 `.xpi`：无开发机目录、Python 路径或本地测试固定路径，按安装环境发现引擎。
- GPU 与 RapidOCR CPU 的 `-setup-*.exe`：内嵌相同引擎源码，首次下载各自依赖和模型，保留对应自检。
- `-runtime-*.zip`：无论文、模型、Python 或用户配置的引擎源码包。
- `-source-*.zip`：带测试、DOM 锁文件与构建脚本的公开源码快照。
- `updates.json`、`SHA256SUMS`、`RELEASE_NOTES.md`：插件更新清单、文件校验和发布说明。

打包仅使用白名单，不读取 Git 历史、`workspace`、`.env`、模型、缓存或识别结果。检查个人目录与常见密钥格式，命中时拒绝构建。
源码包应解压到新目录，重新安装开发依赖、执行 `npm ci --prefix tests/dom` 后测试，避免依赖原工程的未打包文件。

## 正式发布

主分支提交执行 Windows／Linux 测试和可移植构建。推送与 `release.json` 一致的版本标签，Release 工作流再次测试、构建并上传到同名 GitHub Release。
`prerelease: false` 发布正式版本，`latest/download/updates.json` 是正式更新通道；手动运行工作流只保存构建产物，不发布。
不要把用于本机探索的固定路径 XPI 或带用户设置的目录上传到 Releases。

0.5.0 不再推荐旧版本；旧标签和下载保留作历史记录，不覆盖、不删除旧发行文件。新版需要同步更新引擎代码，README 和发布说明必须提示，避免误用旧服务。
安装器尚无 Windows 代码签名，构建自检与 SHA-256 不等同于签名或全新系统安装验收。
