# 论文翻译大师

在 Zotero 中选择论文，进行 OCR，生成可编辑的 Markdown，再按需使用大模型翻译。
识别结果和译文分别保存为 Zotero 附件，下次打开直接阅读，不需要重复识别或翻译。

[下载最新版本](https://github.com/XvHaoR/paper-translation-master/releases) · [安装说明](docs/INSTALLATION.md) · [插件使用说明](zotero/README.md)

## 安装

1. 从 Releases 下载 `.xpi`，在 Zotero 的「工具 → 插件／附加组件 → 齿轮 → 从文件安装」中选择该文件，重启 Zotero。
2. Windows 用户下载同一版本的 `paper-translation-master-setup-*.exe`，双击后点击「安装 OCR 引擎」。安装器自动准备独立 Python、OCR 依赖、模型，并执行真实识别自检；无需手装 Python、Git、WSL 或 Docker。
3. 安装完成后，打开 Zotero「工具 → 论文翻译大师 → 设置」，点击「检测已安装引擎」。正常情况下无需填写项目目录或 Python 路径。点击工具栏「启动 OCR」即可使用。
4. 需要翻译时，在设置中填写自己的 OpenAI 兼容接口地址、模型和 API 密钥。OCR 本身不需要翻译 API；翻译服务的费用取决于所选服务商。

首次安装需要联网下载数 GB 依赖和模型，时间取决于网络。之后重复安装会复用下载缓存；模型已下载后可本地 OCR。已保存结果无需引擎即可离线阅读。

## 功能

- 在弹窗里勾选论文，批量 OCR、批量翻译；可按标题、作者、年份筛选，只选未完成的论文。
- 在文献列表显示独立的 OCR、翻译状态；可选择新添加的文献自动 OCR，翻译由用户手动触发。
- 在原生 PDF 阅读器中并排查看 PDF 与 OCR／译文，双向定位段落；也可只查看 OCR 或译文。
- 逐段双语、仅译文两种模式，可调字号，以及 OCR／译文各自的字体与颜色。
- 复制公式 LaTeX、表格导出 Excel、导出 Markdown；保留图片、公式和表格。
- 直接编辑 OCR 的 Markdown、调整标题和内容类型、删除多余图片；翻译使用校正后的 OCR。
- 清除 OCR、删除当前语言译文，保留源 PDF；已有结果可继续使用。
- 批量任务在后台排队，完成后不会自动打开文章标签页。模型在同一会话中复用，关闭 Zotero 后清理由插件启动的进程。

OCR 基于 PaddleOCR-VL-1.6，翻译通过用户配置的大模型接口执行。识别与翻译结果仍需结合原文核对。

## 支持范围

| 部分 | 当前范围 |
| --- | --- |
| Zotero 插件 | 面向 Zotero 7–10；原生界面回归测试使用 Windows 上的 Zotero 10.0.7 |
| Windows 一键 OCR 安装器 | Windows 10／11 x64，NVIDIA GPU 至少 8 GB 显存、推荐 12 GB；CC 7.x–9.x，驱动至少 560.76 |
| RTX 50 系列、其他 GPU、CPU | 不在首版自动安装器的支持范围；可自行按 PaddleOCR 官方文档配置 |
| Linux／macOS | 可尝试安装插件、阅读已保存结果；没有一键 OCR 安装包，未做完整平台验收 |
| 已配置 WSL FastDeploy 的用户 | 保留高级设置中的 WSL 运行方式，可继续使用原有环境 |

首版一键安装器为预览功能。已验证打包、硬件检查、下载校验和失败重试逻辑；全新 Windows GPU 环境的完整安装仍需进一步验收。不承诺所有显卡、网络条件和论文均有相同性能。

## 从旧版本升级

本项目原名 PaperLoom。新名称为「论文翻译大师」，保留旧插件 ID、设置与附件标签，避免已有 OCR 和译文失联。在 Zotero 插件管理器中安装新版 XPI 替换旧版，再重启 Zotero；无需重新 OCR。

原私有仓库不随此公开版本发布。此仓库只包含经筛选的源码、自制测试文档、许可证与构建脚本，不包含用户论文、翻译缓存、模型权重或 API 密钥。

## 开发

需要 Python 3.11+ 和 Node.js（部分 JavaScript 回归测试使用）。

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e ".[dev]"
python -m pytest tests/unit tests/integration -q
python zotero/build.py --portable
python packaging/build_release.py
```

文档服务可用 `python -m app.cli serve` 启动。仅安装核心开发依赖不包含 OCR 模型运行库；OCR 环境请使用安装器或参考官方文档。

发布文件使用白名单打包，包含 XPI、独立引擎源码包、源码快照、Windows 安装器、自动更新清单与 SHA-256 校验值。详见 [发布维护](docs/RELEASING.md)。

## 许可证与数据

项目原创代码采用 [AGPL-3.0-or-later](LICENSE)，第三方组件保留各自许可证，详见 [第三方声明](THIRD_PARTY_NOTICES.md)。
OCR 在本地运行；使用远程翻译接口时，待翻译文本会发送到你配置的服务商。API 密钥保存在本机配置中，不随软件发布；保存的论文和译文由你的 Zotero 文献库管理。
