# 安装论文翻译大师

下载地址：[GitHub Releases](https://github.com/XvHaoR/paper-translation-master/releases)。
首版 `0.3.0` 是预览版，请在发布页下载，不要使用 GitHub 的“latest”地址寻找预览版。

## 1. 安装 Zotero 插件

下载 `paper-translation-master-0.3.0.xpi`，在 Zotero 中打开「工具 → 插件／附加组件」，
点击齿轮，选择「从文件安装」，选中 XPI，完成后重启 Zotero。

此前安装过 PaperLoom 的用户也按此步骤替换。内部 ID 和附件标签保留，
已有识别结果、译文、字号和外观设置继续使用；不要手动删除原插件的数据目录。

## 2. 安装 OCR 引擎

Windows 下载 `paper-translation-master-setup-0.3.0.exe`，双击，点击「安装 OCR 引擎」。
插件设置中的「下载 OCR 安装器」也会打开同一个发布页。

自动安装器支持：

- Windows 10 2004 或更新版本／Windows 11，x64；
- NVIDIA 显卡至少 8 GB 显存，建议 12 GB；CUDA Compute Capability 7.x–9.x；
- NVIDIA Windows 驱动 560.76 或更新；
- 安装盘至少 15 GB 可用空间，建议 25 GB；首次安装可以联网下载官方依赖和模型。

当前安装器不自动支持 RTX 50 系列、AMD／Intel GPU 或 CPU OCR；不在这些范围内会说明原因，
不会暗中退回低速模式。GPU 驱动需由用户安装，安装器不更换系统驱动。

安装器依次执行硬件检查、引擎解压、独立 Python、OCR 依赖安装、模型下载和真实 PDF 自检，
成功后注册给 Zotero 使用。无需手动安装 Python、Git、WSL、Docker，也不需要输入命令。
安装器不修改系统 PATH，不覆盖已有 Python 或 WSL 环境。

首次下载通常有数 GB，时间取决于网络。安装窗口显示当前步骤及下载／安装输出，
取消或失败后已下载缓存会保留，重新运行即可重试。下载完成的文件会检查校验值。
只有真实识别自检通过，才会标记为安装完成。

默认安装目录为 `%LOCALAPPDATA%/PaperTranslationMaster`，也可以在安装器中选择其他磁盘。
自定义目录仍会注册到固定发现位置，插件无需手填新路径。
重复运行安装器可以修复；修复前先关闭 Zotero，由程序检查引擎是否仍在使用，避免覆盖运行中的文件。

安装成功后打开 Zotero「工具 → 论文翻译大师 → 设置」，点击「检测已安装引擎」再保存，
然后点击工具栏「启动 OCR」。以后无需重复运行安装器。未填写手动路径时，插件也会在启动时自动发现引擎。
启动模型时会显示阶段进度，就绪后进度条消失。

## 3. 配置翻译

在插件设置中填写自己使用的大模型服务的 OpenAI 兼容接口地址、模型名和 API 密钥，
保存后测试连接。可以使用本地模型，也可以使用远程 API，费用由服务商决定。
远程翻译会将待翻译文本发送给所配置的服务商。OCR 本身不要求 API 密钥。

## 4. 处理论文

点击文献列表上方「批量 OCR」或「批量翻译」，在弹窗中勾选论文后点击开始。
已有结果会复用，缺少 OCR 的论文在翻译前先识别。任务在后台处理，完成后不自动打开论文。

打开 PDF 后，可在顶部切换原文、对照和仅 OCR；右侧可以查看保存的 OCR 或译文。
已保存结果可以离线阅读，不需要启动引擎；在其他电脑上阅读同步的结果也不需要重新识别。

## 遇到安装问题

- 硬件不支持：查看窗口列出的原因，参考 [PaddleOCR 官方文档](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html) 自行配置其他环境；目前没有其他平台的一键安装器。
- 下载失败：确认能访问 `python.org`、`files.pythonhosted.org`、`pypi.org`、`paddlepaddle.org.cn` 以及官方模型下载源，之后重新运行安装器。没有完整离线包。
- 模型自检失败：安装器不会注册失败环境。保留日志后重试，或提交 Issue；不要上传论文和 API 密钥。
- 找不到引擎：先确认安装器显示成功，再在插件设置中检测。如果以前填过手动项目目录，清空该路径并保存后再次检测，或选择正确的安装目录。
- 修复被阻止：先关闭 Zotero 和外部 OCR 启动器，等待后台进程退出后重试；不要直接覆盖运行中的引擎文件。

日志位于安装目录的 `install.log`、`install-state.json`，实际识别自检报告为 `warmup.json`。
分享日志前请检查其中的本机路径和服务信息。

## 验证范围与高级使用

已在 Windows NVIDIA RTX 3060 12 GB 环境完成本地 OCR 真实 PDF 识别自检；
插件界面使用 Zotero 10.0.7 做独立文献库回归测试。
固定依赖的新 Python／CUDA 安装组合尚未在全新 Windows 系统完成从下载到识别的整套验收，
因此本安装器作为预览提供。实际性能随显卡、文献和本地推理后端而变化，
不保证与 WSL FastDeploy 的吞吐量相同。

已经配置 WSL FastDeploy 的用户可继续在高级设置里选择 WSL，并填写现有项目目录和 Python。
自行搭建其他平台的 OCR 环境时，可以使用源代码；仅安装核心 Python 依赖不会自动具备 OCR 能力。
插件面向 Zotero 7–10，但 macOS／Linux 及旧版 Zotero 未做完整验收。

预览版通过下载 XPI 手动升级。`updates.json` 采用稳定版发布地址，未来正式版本可沿用该更新通道，
预览版不会被自动推送给稳定版用户。

本项目原创代码为 AGPL-3.0-or-later；Python、PaddleOCR、模型和其他第三方组件保留各自许可证。
安装器包含第三方声明，运行时和模型下载来源见 [安装器维护说明](../packaging/installer/README.md)。
