# 可能是最好的Zotero翻译插件

# 论文翻译大师

在 Zotero 中选择论文，进行 OCR，生成可编辑的 Markdown，再按需使用大模型翻译。
识别结果和译文分别保存为 Zotero 附件，下次打开直接阅读，不需要重复识别或翻译。

[下载最新版本](https://github.com/XvHaoR/paper-translation-master/releases) · [安装说明](docs/INSTALLATION.md) · [插件使用说明](zotero/README.md)

## 安装

1. 从 Releases 下载 `.xpi`，在 Zotero 的「工具 → 插件／附加组件 → 齿轮 → 从文件安装」中选择该文件，重启 Zotero。
2. Windows 用户下载同一版本的 `paper-translation-master-rapidocr-setup-*.exe`，双击后点击「安装 OCR 引擎」。安装器自动准备独立 Python、OCR、版面、公式与表格模型，并执行真实识别自检；无需手装 Python、Git、WSL 或 Docker。原 VL 版使用其历史发布的安装器。
3. 安装完成后，打开 Zotero「工具 → 论文翻译大师 → 设置」，点击「检测已安装引擎」。正常情况下无需填写项目目录或 Python 路径。点击工具栏「启动 OCR」即可使用。
4. 需要翻译时，在设置中填写自己的 OpenAI 兼容接口地址、模型和 API 密钥。OCR 本身不需要翻译 API；翻译服务的费用取决于所选服务商。

首次安装需要联网下载依赖和完整模型，预留至少 5 GB 磁盘空间，时间取决于网络。之后重复安装会复用下载缓存；模型已下载后可本地 OCR。已保存结果无需引擎即可离线阅读。

## 功能

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
| RapidOCR 0.4.0 一键安装器 | Windows 10／11 x64，CPU 即可，建议至少 8 GB 内存、5 GB 磁盘空间；无需 CUDA、WSL 或独立显卡 |
| PaddleOCR-VL 0.3.1 历史安装器 | Windows 10／11 x64，NVIDIA GPU 至少 8 GB 显存、推荐 12 GB；CC 7.x–9.x，驱动至少 560.76 |
| RTX 50 系列、其他 GPU | 可以使用 RapidOCR 版的 CPU 路径；不依赖 GPU 型号 |
| Linux／macOS | 可尝试安装插件、阅读已保存结果；没有一键 OCR 安装包，未做完整平台验收 |
| 已配置 WSL FastDeploy 的用户 | 保留高级设置中的 WSL 运行方式，可继续使用原有环境 |

一键安装器仍为预览功能，保留下载校验、失败重试与真实 PDF 自检。不同论文和设备的耗时与识别准确度不同；完整 CPU 识别不承诺总是比 GPU 版快。上述 RTX 3060 耗时是原 VL 版的用户实测。


## 从旧版本升级

0.4.0 增加 [RapidOCR 完整文档版](docs/RAPIDOCR_EDITION.md)，CPU 即可使用，保留公式、表格、编辑、翻译及原文对照。
下载名称含 `rapidocr` 的 XPI 与安装器；引擎使用独立目录，不覆盖原 VL 环境，已保存结果继续有效。
原 PaddleOCR-VL 版 [0.3.2](https://github.com/XvHaoR/paper-translation-master/releases/tag/v0.3.2) 修复了 OCR 阻塞翻译的队列问题：第一篇识别完成后，可以在第二篇继续 OCR 时翻译并保存。
0.3.1 用户只需更新原版 XPI，无需重装引擎；同一篇论文仍按提交顺序处理。CPU 版更容易配置，不承诺完整识别总是比 GPU 版快。

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

## 许可证与数据

项目原创代码采用 [AGPL-3.0-or-later](LICENSE)，第三方组件保留各自许可证，详见 [第三方声明](THIRD_PARTY_NOTICES.md)。
OCR 在本地运行；使用远程翻译接口时，待翻译文本会发送到你配置的服务商。API 密钥保存在本机配置中，不随软件发布；保存的论文和译文由你的 Zotero 文献库管理。
