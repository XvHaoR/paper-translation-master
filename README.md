# 论文翻译大师

在 Zotero 中选择论文进行 OCR，使用 HTML 阅读、编辑识别结果，再按需使用大模型翻译。OCR 与译文分别保存为 Zotero 附件，下次打开无需重复识别或翻译。

**新增：0.6.0 云端版；本地 GPU／CPU 完整方案继续提供。云端版与本地 OCR 版的插件功能一致，主要区别是识别运行的位置与硬件要求。** 两版都保留批量处理、PDF 段落对照、内容编辑、逐段双语／仅译文、LaTeX 复制、Excel 表格导出和 HTML／Markdown 导出。

“相同”指功能和操作流程。云端版与本地 PaddleOCR-VL 版均使用 PaddleOCR-VL-1.6 系列，但速度和识别结果还受服务配置、网络、排队、论文内容及本机硬件影响，不承诺完全相同。旧 RapidOCR CPU 方案使用另一套模型，不能视为相同识别效果。

## 下载与安装

| 文件 | 用途 |
| --- | --- |
| [云端版 Windows x64 完整运行包](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.6.0/paper-translation-master-cloud-windows-x64-0.6.0.zip) | 首次安装推荐，含插件和轻量运行环境，无需手动安装 Python 或 OCR 模型 |
| [云端版 Zotero 插件 XPI](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.6.0/paper-translation-master-cloud-0.6.0.xpi) | 已有同版云端环境时使用；单独安装 XPI 不包含运行环境 |
| [发行文件与校验值](https://github.com/XvHaoR/paper-translation-master/releases/tag/v0.6.0) | 源码、运行代码、SHA-256 校验值 |

1. 下载完整 ZIP，**解压整个目录到准备长期保留的位置**，双击 `Install-Cloud.cmd` 注册运行环境。
2. 在 Zotero「工具 → 插件／附加组件 → 齿轮 → 从文件安装」中选择包内 XPI，重启 Zotero。
3. 在「工具 → 论文翻译大师 → 设置」中填写自己的飞桨 **Access Token**，点击「保存设置」。
4. 选中文献执行 OCR，或在批量窗口勾选多篇论文。无需配置显卡、CUDA、WSL 或本机识别模型。
5. 翻译时填写自己的大模型接口地址、模型名和翻译密钥。飞桨 OCR 密钥与翻译密钥分别配置。

完整包内置轻量 Python 和阅读依赖，不包含 OCR 模型。移动目录后重新运行 `Install-Cloud.cmd`。更换版本前先关闭 Zotero；安装包尚未进行 Windows 代码签名。

## 本地 OCR 完整部署方案（继续提供）

| 方案 | Zotero 插件 | 一键引擎安装器 | 配置 |
| --- | --- | --- | --- |
| 本地 PaddleOCR-VL GPU | [本地版 0.5.0 XPI](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-0.5.0.xpi) | [GPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-setup-0.5.0.exe) | NVIDIA 显存至少 8 GB、推荐 12 GB；CC 7.x–9.x，驱动至少 560.76；磁盘至少 15 GB、建议 25 GB |
| 本地 RapidOCR CPU | 同上，使用本地版 0.5.0 XPI | [CPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-rapidocr-setup-0.5.0.exe) | 无需独显、CUDA、WSL；建议至少 8 GB 内存、5 GB 磁盘；模型在本机运行 |

本地安装器支持 Windows 10 2004+／11 x64，自动准备独立 Python、依赖和完整模型。安装本地 XPI，关闭 Zotero 运行所选安装器；完成后在插件设置中「检测已安装引擎」并保存。模型下载齐备后可在本机识别，无需飞桨 OCR Token。

需要源码、已有 WSL FastDeploy 环境或自行构建安装器时，见[完整本地部署与升级说明](docs/INSTALLATION.md)和[RapidOCR 方案](docs/RAPIDOCR_EDITION.md)。GPU、CPU 安装与构建脚本继续保留，云端版不替代或删除本地方案。

## 在哪里注册云端 OCR API

1. 打开[百度飞桨 AI Studio](https://aistudio.baidu.com/)，注册或登录账号。
2. 进入 [AI Studio Access Token 页面](https://aistudio.baidu.com/account/accessToken)，按页面指引获取访问令牌。
3. 在 [PaddleOCR 服务页面](https://aistudio.baidu.com/paddleocr)查看服务与账户额度；如要求开通服务，按官方流程完成。
4. 将 Access Token 填入插件设置的「飞桨云端 OCR」，保存即可。不要把密钥发到公开 Issue、截图或仓库中。

插件接入[飞桨官方文档解析 API](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html)，使用 PaddleOCR-VL-1.6。服务可用性、额度和计费以官方控制台为准，本项目不提供共享密钥，也不承诺无限免费识别。

## 云端版与本地版的配置区别

| 项目 | 云端 OCR 版（当前推荐） | 本地 PaddleOCR-VL 版 |
| --- | --- | --- |
| 插件功能 | 与本地版一致 | 与云端版一致 |
| OCR 运行位置 | 飞桨服务器 | 自己的电脑 |
| 适合设备 | 普通办公电脑、无独显笔记本、低性能电脑 | 有兼容 NVIDIA 显卡、希望本地识别的电脑 |
| 系统／Zotero | Windows 10/11 **64 位**；面向 Zotero 7–10，原生实测为 10.0.8 | Windows 10 2004+／11 **64 位**；面向 Zotero 7–10 |
| 显卡 | 无需独立显卡、CUDA 或 WSL | NVIDIA GPU，**显存至少 8 GB，推荐 12 GB**；计算能力 CC 7.x–9.x，驱动至少 560.76；不代表支持所有 NVIDIA 型号 |
| 内存与磁盘 | 建议至少 4 GB 内存，预留 1 GB 磁盘用于程序；论文和结果另计 | 建议 16 GB 系统内存；磁盘**至少 15 GB，建议 25 GB**用于运行库和模型；论文和结果另计 |
| 网络 | 每次新 OCR 需要联网 | 模型下载完成后可在本机 OCR |
| 部署 | 不下载大模型，无需维护本机推理环境 | 需要配置模型及兼容的推理运行库 |
| 数据流向 | 所选 PDF 上传至飞桨 | OCR 在本机完成 |

**云端版的优势**是降低硬件和安装门槛，不占用本机显卡加载 OCR 模型，更适合不方便部署本地模型的用户。识别结果下载保存后，仍可离线阅读，再次打开不会重新调用接口。

云端单次本地文件上传不超过 **50 MB**。自动 OCR 需在云端模式单独开启，开启后新 PDF 会自动上传给飞桨；原来的本地自动 OCR 设置不会直接触发上传。取消本机等待不等同于撤销已提交的官方任务或返还额度。

目前完整发布包主要面向 Windows x64。macOS／Linux 没有完整运行包，未做完整平台验收。已保存结果的阅读与 OCR 部署是不同支持范围。

## 阅读与编辑功能

- HTML 阅读区区分正文、摘要、图注、表注和脚注，图表与说明组合展示，支持完整大图。
- 在 PDF 原文、对照和仅 OCR 之间切换，支持段落与 PDF 双向定位、交换左右位置。
- 编辑识别文本、手动修改类别、删除多余图片、补充标题；连续摘要、图注和表注可合并展示，每段保留定位。
- 只修改 OCR 类别时同步调整已有译文排版；修改正文后提示按需重新翻译，保留旧结果。
- 批量 OCR 与翻译使用独立任务队列，完成后不会自动打开大量标签页。
- 已保存 OCR 与译文可分别管理、清除和重新处理；支持字体、字号和颜色设置。
- 公式可复制 LaTeX，表格可导出 Excel，全文可导出 HTML 和 Markdown。

## 从本地版切换

关闭 Zotero，按上述步骤安装云端完整包和 XPI。两版使用相同插件 ID，因此云端版会替换原插件；已有 Zotero 文献、OCR 和译文附件保留。不要同时启用两个同源插件副本。

首次切换需手动安装完整包，不会通过旧更新通道强制将本地用户切到云端。旧翻译配置若保存在原引擎目录，请在云端版设置中重新填写。只更新 XPI 不足以完成升级。

本地 GPU 和 CPU 的完整安装包与部署方案均继续保留，仍使用本地版 0.5.0 插件。已有本地环境无需迁移到云端。云端包使用另一套运行环境，按需选择即可，详见[完整安装与部署说明](docs/INSTALLATION.md)。

## 开发与验证

核心服务需要 Python 3.11+，JavaScript 回归需要 Node.js。核心开发依赖不包含本机 OCR 模型。

```bash
python -m pip install -e ".[dev]"
npm ci --prefix tests/dom
python -m pytest tests/unit tests/integration -q
python packaging/build_release.py
```

Windows 完整包使用 CPython 3.13 x64 嵌入式环境，由 `scripts/build_cloud_bundle.py` 构建。测试使用隔离文献库；实际耗时及识别质量随论文和服务变化。

## 许可证与数据

原创代码采用 [AGPL-3.0-or-later](LICENSE)，第三方组件保留各自许可证，见[第三方声明](THIRD_PARTY_NOTICES.md)。

云端 OCR 将所选 PDF 发送至飞桨；远程翻译将待翻译文本发送至你配置的服务商。OCR 密钥只保存在本机 Zotero 配置目录，程序包和仓库不包含用户密钥；论文和译文由你的 Zotero 文献库管理。
