# 可能是最好的Zotero翻译插件

# 论文翻译大师

在 Zotero 中选择论文，进行 OCR，生成可编辑的 Markdown，再按需使用大模型翻译。
识别结果和译文分别保存为 Zotero 附件，下次打开直接阅读，不需要重复识别或翻译。

[查看全部版本](https://github.com/XvHaoR/paper-translation-master/releases) · [安装说明](docs/INSTALLATION.md) · [插件使用说明](zotero/README.md)

## 选择版本与下载

| 版本 | 适合的环境 | 下载 |
| --- | --- | --- |
| **RapidOCR 0.4.1 · CPU 版** | Windows x64，CPU 即可，无需 NVIDIA、CUDA 或 WSL | [Zotero 插件 .xpi](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.4.1/paper-translation-master-rapidocr-0.4.1.xpi) · [一键引擎安装器 .exe](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.4.1/paper-translation-master-rapidocr-setup-0.4.1.exe) · [发行说明](https://github.com/XvHaoR/paper-translation-master/releases/tag/v0.4.1) |
| **PaddleOCR-VL 0.3.2 · GPU 版** | 已有 VL 环境，或具备下方支持的 NVIDIA 显卡 | [Zotero 插件 .xpi](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.3.2/paper-translation-master-0.3.2.xpi) · [一键引擎安装器 .exe](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.3.2/paper-translation-master-setup-0.3.2.exe) · [发行说明](https://github.com/XvHaoR/paper-translation-master/releases/tag/v0.3.2) |

两版均保留版面、公式、表格、Markdown 编辑、大模型翻译与 PDF 段落对照，识别效果和速度会随论文及设备变化。
两版共用插件身份，选择其中一版安装；引擎使用独立目录，已有 OCR 和译文可继续阅读。

**升级提示：0.4.0 → 0.4.1、0.3.1 → 0.3.2 只需更新对应版本的 XPI 并重启 Zotero，无需重装引擎。**
这两个修复版都允许一篇论文识别完成后，在其他论文继续 OCR 时开始翻译；同一篇论文仍按提交顺序处理。

## 首次安装

1. 在上表选择一种版本，下载 `.xpi`，在 Zotero 的「工具 → 插件／附加组件 → 齿轮 → 从文件安装」中选择该文件，重启 Zotero。
2. Windows 用户下载同一行的 `.exe`，双击后点击「安装 OCR 引擎」。安装器自动准备独立 Python、OCR 依赖和模型，并执行识别自检；无需手装 Python、Git、WSL 或 Docker。已经装好对应引擎的用户可跳过。
3. 安装完成后，打开 Zotero「工具 → 论文翻译大师 → 设置」，点击「检测已安装引擎」。正常情况下无需填写项目目录或 Python 路径。点击工具栏「启动 OCR」即可使用。
4. 需要翻译时，在设置中填写自己的 OpenAI 兼容接口地址、模型和 API 密钥。OCR 本身不需要翻译 API；翻译服务的费用取决于所选服务商。

首次安装需要联网下载依赖和完整模型，时间取决于网络。RapidOCR 版预留至少 5 GB 磁盘空间，VL 版至少 15 GB、建议 25 GB。之后重复安装会复用下载缓存；模型已下载后可本地 OCR。已保存结果无需引擎即可离线阅读。

## 功能

- 在弹窗中勾选论文，批量 OCR 或批量翻译；也可开启新文献自动 OCR，再手动选择是否翻译。处理完成后不会自动打开阅读标签页。
- 使用自己配置的大模型接口翻译，支持逐段双语和仅译文显示；OCR 与译文分别保存，可单独清除并重新处理。
- 在同一 PDF 标签页查看原文、左右对照或仅 OCR，支持段落与 PDF 原文双向定位、左右交换。
- 编辑识别后的 Markdown、补充标题、调整内容类型或删除多余图片；OCR 与译文的字号、字体和颜色可调整。
- 复制公式的 LaTeX、导出表格为 Excel、导出 Markdown；已保存结果下次直接打开，无需重复 OCR 或翻译。

### 识别论文为可编辑的 Markdown：RapidOCR 完整 CPU 版，或原 PaddleOCR-VL 版

<img width="2560" height="1380" alt="ad83ba8e7c8d211df8430f5a82c27d8d" src="https://github.com/user-attachments/assets/85ca4bdc-715e-4100-8b93-e84bb5876138" />

### 基于大模型翻译论文，支持对照阅读和纯翻译模式
<img width="2560" height="1380" alt="e216e48da1ee99805035cacb299e60e3" src="https://github.com/user-attachments/assets/2cc0ce8d-c6c4-408f-84d7-c8ed52437bef" />
<img width="2560" height="1380" alt="e57e25ff5c9cf0ff0d96e4d0c192daf1" src="https://github.com/user-attachments/assets/0c906b57-8a15-44ec-9c27-b1e6a11fc648" />

### 论文公式可导出为Latex格式
<img width="2560" height="1380" alt="950e7f080bd5b058fcd57cde56d83286" src="https://github.com/user-attachments/assets/7938297b-d578-4de4-abb2-378cd8f4b524" />

### 论文表格可导出为Excel表格
<img width="2560" height="1380" alt="b83cdb3d9511838fdd6410c2688b111f" src="https://github.com/user-attachments/assets/7e7b9c29-a745-4371-b6d6-d5c28edf7bfb" />

### 支持批量OCR和翻译
<img width="1325" height="959" alt="image" src="https://github.com/user-attachments/assets/06b80cbf-86fa-4310-9e54-be14c8114e0a" />

### 论文OCR结果与翻译文本持久留存，不需要重复识别和翻译。本地运行，实测3060 12G 30页论文OCR耗时<60s


## 支持范围

| 部分 | 当前范围 |
| --- | --- |
| Zotero 插件 | 面向 Zotero 7–10；原生界面回归测试使用 Windows 上的 Zotero 10.0.7 |
| RapidOCR 0.4.1 一键安装器 | Windows 10 2004+／Windows 11 x64，CPU 即可，建议至少 8 GB 内存、5 GB 磁盘空间；无需 CUDA、WSL 或独立显卡 |
| PaddleOCR-VL 0.3.2 一键安装器 | Windows 10 2004+／Windows 11 x64，NVIDIA GPU 至少 8 GB 显存、推荐 12 GB；CC 7.x–9.x，驱动至少 560.76；磁盘至少 15 GB、建议 25 GB |
| RTX 50 系列、其他 GPU | 可以使用 RapidOCR 版的 CPU 路径；不依赖 GPU 型号 |
| Linux／macOS | 可尝试安装插件、阅读已保存结果；没有一键 OCR 安装包，未做完整平台验收 |
| 已配置 WSL FastDeploy 的用户 | 保留高级设置中的 WSL 运行方式，可继续使用原有环境 |

一键安装器仍为预览功能，保留下载校验、失败重试与真实 PDF 自检。不同论文和设备的耗时与识别准确度不同；完整 CPU 识别不承诺总是比 GPU 版快。上述 RTX 3060 耗时是原 VL 版的用户实测。


## 从旧版本升级

### RapidOCR 0.4.0 → 0.4.1

安装上表中的 RapidOCR 0.4.1 XPI 并重启 Zotero。已安装的 CPU 引擎、完整公式和表格模型继续使用，无需重新下载。
本版拆开 OCR 和翻译的调度，一篇完成 OCR 后可在另一篇识别时翻译；取消单篇任务不会停止共享服务，工具栏同时显示两种任务进度。

### PaddleOCR-VL 0.3.1 → 0.3.2

安装上表中的 PaddleOCR-VL 0.3.2 XPI 并重启 Zotero，无需重装 GPU 引擎。本版包含相同的队列修复。
两版的更新均保留已保存结果、阅读对照及外观设置，无需重新 OCR 或翻译。

### 切换 OCR 引擎

从 VL 版切换到 [RapidOCR 完整文档版](docs/RAPIDOCR_EDITION.md) 时，安装名称含 `rapidocr` 的 XPI 和引擎安装器，再在插件设置中检测引擎。
CPU 引擎使用独立目录，不覆盖 VL 环境。两版使用不同的识别模型，CPU 版更容易配置，不承诺完整识别总是比 GPU 版快。

0.3.1 修复新旧插件与改名后 OCR 页面混用时的段落定位，并拆分阅读通信、布局、外观、坐标与高亮。
安装新版 XPI 并重启 Zotero 即可对照已有结果，无需重新 OCR 或翻译。
设计与回归要求见 [阅读器模块边界](docs/READER_ARCHITECTURE.md)。

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

### 源码分支与安装包

`main` 保存 RapidOCR 版的当前代码，`codex/031-parallel-translation` 保留原 PaddleOCR-VL 版的维护代码。
队列修复已同步到两边，分别打包为 0.4.1 和 0.3.2。GitHub 顶部的「Compare & pull request」只是合并分支的建议，不代表插件还没发布。
普通用户使用上方的下载链接即可，无需克隆仓库、切换分支或提交 PR。

## 许可证与数据

项目原创代码采用 [AGPL-3.0-or-later](LICENSE)，第三方组件保留各自许可证，详见 [第三方声明](THIRD_PARTY_NOTICES.md)。
OCR 在本地运行；使用远程翻译接口时，待翻译文本会发送到你配置的服务商。API 密钥保存在本机配置中，不随软件发布；保存的论文和译文由你的 Zotero 文献库管理。
