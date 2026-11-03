# 论文翻译大师

在 Zotero 中选择论文进行 OCR，使用 HTML 阅读和校正识别结果，再按需交给大模型翻译。
OCR 和译文分别保存为 Zotero 附件，下次打开直接阅读，无需重复识别或翻译。支持导出 HTML、Markdown、公式 LaTeX 和 Excel 表格。

**当前推荐版本：0.5.0 正式版。** 本次是 HTML 阅读大版本更新，重点提升阅读体验和稳定性，让你更灵活地管理 OCR 后的论文和翻译。
**此前的 0.3.x、0.4.x 及本地 HTML 探索版仅作历史归档，不再推荐安装或继续作为日常版本。**

[下载 0.5.0](https://github.com/XvHaoR/paper-translation-master/releases/tag/v0.5.0) · [安装与升级](docs/INSTALLATION.md) · [插件使用说明](zotero/README.md)

## 0.5.0 更新了什么

- **更适合论文的 HTML 阅读界面。** 正文、摘要、图注、表注和脚注使用不同排版；图片和说明组合展示，完整图片可放大查看，减少普通 Markdown 展示论文时的混杂感。
- **可以自己校正识别类型。** 在段落旁点击「修改类型」，或进入「编辑 OCR」，把正文改为摘要、脚注改为图注等，保存后重新排版。连续摘要共用背景框，连续图注／表注自动归入相邻图表，同时保留每段独立的 PDF 定位。
- **更灵活地管理 OCR 和译文。** 编辑文字、公式和表格，删除多余图片，插入章节标题；校正结果另存新版本，原 PDF 和既有附件保留。可删除旧译文，再根据当前校正版本重新翻译。
- **改进稳定性。** 阅读通信、外观、内容编辑、PDF 坐标和高亮各自维护，增加交互与混合版本回归测试；修复正文遗漏、行内公式、引用乱码和跨栏定位等问题。
- **保留原有能力。** 批量 OCR 和翻译、独立任务队列、模型复用、PDF 段落对照、逐段双语／仅译文、字体字号和颜色设置、LaTeX 复制、Excel 导出、离线保存仍然可用。批量完成后不会自动打开大量阅读标签页。

## 下载与安装

| 文件 | 用途 |
| --- | --- |
| [0.5.0 Zotero 插件](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-0.5.0.xpi) | 所有用户安装同一 XPI，支持已安装的两种引擎 |
| [PaddleOCR-VL GPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-setup-0.5.0.exe) | Windows NVIDIA GPU，使用 PaddleOCR-VL-1.6 |
| [RapidOCR CPU 安装器](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.5.0/paper-translation-master-rapidocr-setup-0.5.0.exe) | Windows CPU，保留版面、表格和公式识别，无需 NVIDIA／CUDA |

1. 下载 XPI，在 Zotero「工具 → 插件／附加组件 → 齿轮 → 从文件安装」中安装，重启 Zotero。
2. 关闭 Zotero，运行适合自己硬件的 **0.5.0 OCR 安装器**。安装器准备独立 Python、依赖和模型，并执行真实识别自检；无需手装 Python、Git、WSL 或 Docker。
3. 重新打开 Zotero，在「工具 → 论文翻译大师 → 设置」中检测引擎并保存。此前填写过手动引擎路径时，请确认它指向已更新的代码。
4. 翻译时填写自己的 OpenAI 兼容接口地址、模型和 API 密钥。翻译由所配置的大模型完成，可使用本地模型或远程 API；费用取决于服务商。OCR 本身不需要翻译 API。

首次安装需要联网下载依赖和模型；重复安装会复用下载缓存。已保存结果可以离线阅读。
GPU 和 CPU 使用不同引擎目录；如果同时安装两者，默认发现 GPU 引擎，可在高级设置指定另一个已更新的项目目录。

## 从旧版本升级

**这次需要同时更新 XPI 和 OCR 引擎代码，不能只更新插件继续使用旧引擎。** 运行同一引擎的 0.5.0 安装器即可更新代码并复用安装位置、模型及下载缓存；不必删除旧结果或重建 Zotero 文献库。
使用源码／WSL 环境的用户更新到本仓库 0.5.0 源码，并保留原有 Python、模型和工作区配置，见[升级步骤](docs/INSTALLATION.md#从旧版本升级到-050)。

- 已保存的 OCR 和译文继续保留；仅查看、调整外观和分组展示不需要重新 OCR 或翻译。
- 旧 OCR 点击一次「编辑 OCR」可在本地更新编辑器；修改类型并保存后，按新类型组合展示，不会重新调用 OCR 模型。
- OCR 校正不会自动覆盖旧译文。需要同步新内容／分类时，删除旧译文后点击「翻译」，使用当前校正版本。
- 极早的附件如果缺少完整编辑数据，且本机对应源缓存已删除，仍可阅读，但需要重新 OCR 才能获得完整编辑能力。

插件 ID 与已有附件标签保持不变；无需先卸载插件或删除用户数据。

## 支持范围

| 部分 | 当前范围 |
| --- | --- |
| Zotero 插件 | 面向 Zotero 7–10；原生界面回归使用 Windows Zotero 10.0.7 |
| GPU 一键安装 | Windows 10 2004+／11 x64；NVIDIA 显存至少 8 GB、建议 12 GB；CC 7.x–9.x，驱动至少 560.76；磁盘至少 15 GB |
| CPU 一键安装 | Windows 10 2004+／11 x64；建议至少 8 GB 内存、磁盘至少 5 GB；无需独立显卡，仍识别版面、公式和表格 |
| WSL FastDeploy | 保留已有环境的高级配置方式；需同步更新本项目代码 |
| Linux／macOS | 可使用源码或阅读已保存结果；没有一键安装器，未做完整平台验收 |

硬件支持由安装器检查，实际耗时取决于引擎、论文和设备。Windows 安装器尚未代码签名，全新系统的完整安装覆盖仍在补充，详见[安装说明](docs/INSTALLATION.md)。

## 开发与验证

需要 Python 3.11+ 和 Node.js。DOM 回归使用固定版本的 jsdom，不会启动 OCR 模型。

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e ".[dev]"
npm ci --prefix tests/dom
python -m pytest tests/unit tests/integration -q
python zotero/build.py --portable
python packaging/build_release.py
```

核心开发依赖不包含 OCR 模型运行库。安装器、源码包、引擎包、更新清单和 SHA-256 校验值通过白名单构建，见[发布维护](docs/RELEASING.md)；模块职责与回归要求见[阅读器架构](docs/READER_ARCHITECTURE.md)。

## 许可证与数据

原创代码采用 [AGPL-3.0-or-later](LICENSE)，第三方组件保留各自许可证，见[第三方声明](THIRD_PARTY_NOTICES.md)。
OCR 在本地运行。使用远程翻译接口时，待翻译文本会发送到你配置的服务商。密钥保存在本机配置中，不随发布包分发；论文和译文由你的 Zotero 文献库管理。
