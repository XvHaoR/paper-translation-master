# 本地 OCR 引擎与历史版本安装

0.8.2 用户请先安装对应平台的完整包。本页的 Windows GPU／CPU 安装器为可选的本地 OCR 引擎。插件使用 0.8.2 完整包内的 XPI。[当前安装与使用教程](TUTORIAL.md)

0.6.0 云端版：下载[Windows 完整包](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.6.0/paper-translation-master-cloud-windows-x64-0.6.0.zip)，解压并运行 Install-Cloud.cmd，安装包内 XPI，重启后填写 [AI Studio Access Token](https://aistudio.baidu.com/account/accessToken)。无需本机模型；PDF 会上传至飞桨。

## 安装插件与 OCR 引擎

1. 按照 [当前安装教程](TUTORIAL.md) 安装 Windows 完整包及里面的 XPI。CPU、GPU 用户使用同一插件。
2. 关闭 Zotero，按硬件选择下面带 `-r1` 的安装器。修订版修复进度第 4 步报错 `cannot import name 'wintypes' from 'ctypes'`。安装器会准备独立 Python、依赖和模型，真实识别自检通过后才注册引擎。
3. 重新打开 Zotero，在「工具 → 论文翻译大师 → 设置」中检测引擎并保存。已有手动目录时，确认它指向更新后的引擎；不要继续指向旧测试包。
4. 翻译需要自己的 OpenAI 兼容接口地址、模型名和 API 密钥，可使用本地模型或远程 API。OCR 无需翻译密钥；远程翻译会向所配置服务商发送论文文本。

| 引擎 | 安装器 | 要求 |
| --- | --- | --- |
| PaddleOCR-VL-1.6 GPU | [下载 GPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/local-ocr-v0.5.0-r1/paper-translation-master-setup-0.5.0-r1.exe) | Windows 10 2004+／11 x64；NVIDIA 显存 ≥8 GB、建议 12 GB，CC 7.x–9.x，驱动 ≥560.76；磁盘 ≥15 GB，建议 25 GB |
| RapidOCR CPU 完整文档 | [下载 CPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/local-ocr-v0.5.0-r1/paper-translation-master-rapidocr-setup-0.5.0-r1.exe) | Windows 10 2004+／11 x64；建议内存 ≥8 GB、磁盘 ≥5 GB；无需 NVIDIA、CUDA、WSL，仍识别版面、公式和表格 |

GPU 安装器暂不自动支持 RTX 50 系列、AMD／Intel GPU。CPU 版不使用 GPU 安装器，也不会要求 NVIDIA 驱动。
两个引擎分别安装在 `%LOCALAPPDATA%/PaperTranslationMaster` 和 `%LOCALAPPDATA%/PaperTranslationMasterRapidOCR`，可选择其他磁盘。
两者都安装时默认发现 GPU 环境；可在高级设置中指定已更新的 CPU 引擎项目目录。

首次需要联网下载数 GB 依赖及模型，模型齐备后可以本地识别。安装窗口显示进度和错误，失败后可重试并复用下载缓存；不修改系统 PATH 或覆盖系统 Python。

## 修复安装或从历史版本升级

### 通过安装器安装的用户

关闭 Zotero 和旧安装窗口，运行对应的 `-r1` 安装器，继续选择原安装目录以复用模型、下载缓存和工作区。完成后重新打开 Zotero，在设置中检测引擎并保存。已经安装 0.8.2 插件的用户可以直接使用现有 XPI。

### 已配置源码／WSL 环境的用户

更新或解压 0.5.0 源码／runtime 包到自己的引擎项目，保留 `workspace`、Python、模型缓存及环境配置。在高级设置中选择更新后的项目目录和原 Python，继续使用原有 local／WSL 后端。
不需要重新部署已正常运行的模型服务器；需要更新本项目的服务、HTML 渲染和编辑程序。源码项目使用 `python -m pip install -e .` 同步项目依赖；具体 OCR 依赖仍依所选环境配置。
旧本地探索版 XPI 中的固定测试目录不用于公开版分发；公开版通过安装描述文件或高级设置发现引擎。

### 已有论文和译文

- 已保存 OCR 和译文继续保留，可以离线阅读；HTML 分组、字体和颜色调整不重新 OCR 或翻译。
- 打开旧 OCR 后点击「编辑 OCR」，本地更新编辑器。选中段落修改「内容类型」并保存，连续摘要／图注／表注自动组合展示，各段 PDF 定位保留。
- 修改文本、类型、删除图片或插入标题后，会生成新的 OCR 附件，原 PDF 和既有版本保留。
- 旧译文不会被自动覆盖。希望译文同步校正内容时，删除旧译文，再点击「翻译」。
- 极早附件缺少完整编辑数据，且对应源缓存已删除时，仍可阅读，需要重新 OCR 才能获得完整编辑能力。

## 使用

点击文献列表上方「批量 OCR」／「批量翻译」，在窗口中勾选论文后开始。后台处理，完成后不自动打开阅读标签页。
不同论文的 OCR 和翻译有独立执行槽，已 OCR 的论文不必等待其他论文识别完才能翻译；同一篇按提交顺序处理，复用已加载模型。

PDF 顶部可切换原文、对照和仅 OCR；右侧可看 OCR、逐段双语或仅译文。点击段落或「对照 PDF」定位原文，支持高亮、左右交换和拖动宽度。
编辑器可修改 Markdown 内容和语义类型，HTML 阅读保留图表布局；公式可复制 LaTeX，表格可导出 Excel。导出的 HTML 需连同其资源目录保存。

## 安装问题与验证范围

- 下载失败：安装器需要访问 Python、PyPI、PaddleOCR／RapidAI 及其官方模型源，恢复网络后重试。没有包含全部模型的离线安装包。
- 自检失败：失败环境不会注册为可用，查看安装目录中的 `install.log`、`install-state.json`、`warmup.json`。分享前移除密钥、本机路径和论文信息。
- 提示旧服务：关闭旧外部启动器或等待插件拥有的进程退出，再启动新版服务。不会结束其他程序占用的进程。
- 已保存结果无需服务；OCR 模型与依赖仅在识别时需要。

插件面向 Zotero 7–10，Windows 界面回归使用 Zotero 10.0.7。Apple 芯片用户可参考 [macOS 完整包安装说明](MACOS.md)。Linux 尚未做完整平台验收。
Windows 安装器没有代码签名，全新系统从下载到识别的完整覆盖仍在补充。不同引擎、论文和设备的耗时及识别质量会不同。
发布包附 `SHA256SUMS`，可校验下载完整性。原创代码为 AGPL-3.0-or-later，第三方依赖保留各自许可证。
