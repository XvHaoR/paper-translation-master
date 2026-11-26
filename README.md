# 论文翻译大师

在 Zotero 中识别 PDF、对照原文阅读、编辑识别结果，并按需使用大模型翻译。识别结果和译文保存为 Zotero 附件，可离线打开。

**0.7.1 融合预览版**将直接解析 PDF、本地 OCR 和云端 OCR 合到一个插件。可在设置中选择默认方案，或在单篇阅读器和批量窗口临时切换。

[下载 Windows x64 完整包](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.7.1/paper-translation-master-windows-x64-0.7.1.zip) · [查看 Release 和校验值](https://github.com/XvHaoR/paper-translation-master/releases/tag/v0.7.1) · [完整安装说明](docs/UNIFIED_EDITION.md)

[下载 macOS 完整包](https://github.com/XvHaoR/paper-translation-master/releases/tag/macos-v0.7.1) · [macOS 安装说明](docs/MACOS.md)

macOS 版面向 Apple 芯片 Mac（macOS 14+），支持直接解析 PDF、飞桨云端 OCR 与翻译。包内附带运行环境。Windows 本地 OCR 安装器仍从原 Release 下载。

## 安装和升级

1. 关闭 Zotero，将完整 ZIP 解压到固定目录。Windows 运行 `Install.cmd`，macOS 运行 `Install.command`。
2. 在 Zotero「工具 → 插件 → 从文件安装」中选择解压目录里的 XPI，再重启 Zotero。
3. 在「论文翻译大师 → 设置」选择识别方案。已有本地 OCR 的用户可以点击「检测已安装引擎」，选择「本地 OCR」并保存。

融合版沿用旧插件 ID。已有 PDF、识别结果、译文和批注继续保留。首次启动会按来源整理可唯一匹配的旧独立结果，并留下原层级记录。初次切换融合版时，请安装完整包并核对翻译设置。[升级说明](docs/MIGRATION.md)

## 选择识别方案

| 方案 | 场景和要求 |
| --- | --- |
| **直接解析 PDF** | 默认方案，利用 PDF 的文字层和版面信息，无需 OCR 模型、独显或密钥。扫描页保留图像，换用 OCR 可识别图中的文字。 |
| **本地 PaddleOCR-VL** | PDF 在本机处理。Windows 10 2004+／11 x64 一键安装方案要求 NVIDIA 显存至少 8 GB，推荐 12 GB，计算能力 CC 7.x–9.x、驱动至少 560.76，磁盘至少 15 GB，推荐 25 GB。已有 WSL FastDeploy 的用户也可复用环境。 |
| **本地 RapidOCR** | Windows CPU 安装器无需 NVIDIA、CUDA 或 WSL。建议至少 8 GB 内存和 5 GB 可用磁盘。 |
| **云端 OCR** | 联网调用飞桨 PaddleOCR-VL-1.6，无需本机 OCR 模型和独显。仅在选择云端时上传所选 PDF，单篇上限 50 MB。自动上传须在云端模式单独启用。 |

已有 GPU 或 CPU 用户可以继续使用 [0.5.0 的 GPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-setup-0.5.0.exe) 或 [CPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-rapidocr-setup-0.5.0.exe) 部署引擎。融合版使用自己的 XPI。[完整本地部署](docs/INSTALLATION.md) · [RapidOCR 说明](docs/RAPIDOCR_EDITION.md)

云端 OCR 用户在 [百度飞桨 AI Studio](https://aistudio.baidu.com/) 注册或登录，到 [Access Token 页面](https://aistudio.baidu.com/account/accessToken) 领取令牌，再填入插件「飞桨云端 OCR」设置。服务额度以 [PaddleOCR 控制台](https://aistudio.baidu.com/paddleocr) 为准。

## 阅读、编辑与翻译

- 使用 HTML 对照 PDF 原文，支持双向滚动、点击定位和高亮。图表、摘要及脚注有独立排版。
- 可编辑识别文本和类别，删去多余图片，调整标题，或导出 HTML、Markdown 和 Excel。OCR 识别的公式可复制 LaTeX。
- 批量识别与翻译后台运行。翻译支持逐段双语或仅译文，绑定实际使用的识别快照。
- 在「翻译服务」中填写 OpenAI 兼容接口、模型和 API Key。Temperature 留空使用模型默认值。

## 开发

完整包支持 Windows x64 和 Apple 芯片 Mac。开发环境使用 Python 3.11+，测试包括 Python、Node.js 和隔离的 Zotero 配置。

```bash
python -m pip install -e ".[dev,layout]"
npm ci --prefix tests/dom
python -m pytest tests/unit tests/integration -q
python packaging/build_release.py
```

[构建和发布](docs/RELEASING.md) · [更新记录](CHANGELOG.md) · [历史 Releases](https://github.com/XvHaoR/paper-translation-master/releases)

原创代码采用 [AGPL-3.0-or-later](LICENSE)。第三方组件的许可证见[第三方声明](THIRD_PARTY_NOTICES.md)。远程翻译会将待翻译的文本发送到用户配置的服务商。
