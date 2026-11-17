# 论文翻译大师 · 本地解析版 0.4.2

Zotero 内的 PDF 解析、翻译与对照阅读插件。默认在本机读取 PDF 文字层并分析版面，不安装或调用 PaddleOCR，不使用 GPU，不需要识别服务密钥。

本目录是仓库内独立维护的本地解析方案，版本号与根目录的云端/OCR 版分别管理。方案对比见 [NATIVE_EDITION.md](../../docs/NATIVE_EDITION.md)，变更见 [CHANGELOG.md](CHANGELOG.md)。各版使用同一 Zotero 插件 ID，安装本版会替换当前启用的同源插件；已有文献和保存附件继续保留。

## 功能与边界

- 分析单栏、多栏和混合版面，按阅读顺序组织正文。
- 在版面分析前识别并过滤审稿稿件页边的连续行号，结合坐标、序列和字号/颜色判断；保留正文数据与引用。原始 PDF 不作修改，难以确认的数字仍予保留。
- 根据版面与续句证据接回跨页、跨栏及被图表隔开的段落；脚注独立显示，图表与对应注释归组。
- 保留翻译、原文/解析/译文对照、双向联动滚动、双向选句高亮及 HTML/Markdown 导出。
- 原 PDF、解析、译文和 Markdown 统一收在文献父条目下，可通过左侧箭头展开/收起；独立 PDF 会自动创建通用文档父条目。
- 公式保留原 PDF 图像；表格保留原始外观，并在可提取时提供表格数据。
- 扫描件、无可用文字层或异常文字区域保留原页/区域图像并标记待核对，**不识别其中的文字**。复杂版面仍可能需要核对。
- 翻译需要另外配置兼容 OpenAI 接口的服务。只有主动翻译/测试连接时才请求该服务。

本版删除 OCR 模型、云端识别、Token 设置、WSL/GPU 启动逻辑与旧安装器。保存格式及部分内部接口仍使用历史 `ocr` 名称，用于兼容已有解析附件；这些名称不代表存在识别引擎。

## Windows 安装

### 使用 Windows x64 完整包

以下步骤适用于维护者另行发布的 Windows 完整包；本次源码提交不包含 Python 二进制或预构建 ZIP。直接克隆仓库的用户请使用下面的源码安装方式。

1. 将 `paper-translation-master-0.4.2-windows-x64.zip` 完整解压到固定目录。
2. 双击 `Install.cmd`，注册自带的 Python 与本地解析环境；无需下载 OCR 模型。
3. 在 Zotero「工具 → 插件」中，从文件安装 `dist/paper-translation-master-0.4.2.xpi`。
4. 打开插件设置，点击「检测已安装引擎」。打开 PDF 后使用「对照」和「解析 / 重试」。翻译设置按自己的服务填写。

安装后保留整个目录。移动目录后重新运行 `Install.cmd`。工作区、论文缓存和翻译配置写入用户本机的 `PaperTranslationMasterNative` 数据目录，不写入发行源码。

### 使用源码包

先安装 Python 3.12 或更新版本（当前 Windows 验证使用 3.13），进入本目录后运行 `Install.cmd`（或 `python install.py`）。脚本创建本目录的 `.venv`，按 `runtime/requirements.txt` 安装固定版本依赖，构建 XPI，然后注册环境。首次安装需要网络；NumPy 固定版本要求 Python 3.12 或更新版本。

从仓库根目录使用命令行：

```powershell
cd editions/native
python install.py
```

完成后在 Zotero 中从文件安装本目录下的 `dist/paper-translation-master-0.4.2.xpi`。本版和根目录使用不同版本的 PyMuPDF，应分别创建虚拟环境。

XPI 是 Zotero 界面部分，单独安装 XPI 不能替代 Python 解析环境。现有附件和译文仍保存在 Zotero 中。新运行环境独立注册，翻译服务配置需要重新填写。

macOS/Linux 可运行 `python3 install.py` 安装环境；随后在插件高级运行设置中指定本目录的 `runtime` 和 `.venv/bin/python`。这些平台未在本次 Windows 发布验证中测试。

## 上传与构建

- **上传源码仓库：** 使用源码包中的文件；`.gitignore` 排除运行环境、缓存、文档和本机配置。
- **供 Windows 用户下载：** 将完整包作为 Release 附件上传。
- `python tools/build.py` 重新构建 XPI，输出到 `dist/`。
- `python tools/package.py` 生成经过路径/敏感文件检查的源码 ZIP、XPI 和 SHA-256 校验值，输出到 `dist/release/`；不打包本机 Python、缓存或文献。
- 在本目录的虚拟环境中运行 `python -m unittest discover -s tests -v`，执行本地解析和接口测试。
- `node tests/test_tasks.cjs` 检查解析升级后的译文缓存与人工编辑保护。
- `node tests/test_attachment_groups.cjs` 检查父子层级、旧结果整理、事务失败和权限边界。
- `python install.py --check` 仅检查环境，不注册或覆盖本机配置。

源码包不包含用户论文、译文、密钥、账户信息、测试截图、绝对本机路径或旧安装包。完整包另外包含干净的 Windows Python、依赖和版面分析资源，保留其许可证与元数据。

## 已知情况

顶部「原文 / 对照 / 解析」在部分 Zotero 场景中首次点击可能不切换，当前可再次点击。此项实机排查已暂缓，本版保持此前按钮实现。

本版没有独立「重新解析」按钮；普通「解析 / 重试」会升级旧版自动解析结果，人工编辑的结果继续保留。

更新后，对曾混入行号的论文再次点击「翻译」，会先升级自动解析，再生成新译文；仅打开已保存译文不会重写其内容。人工编辑的解析结果不会被自动替换。

0.4.2 启动时会整理本插件此前保存的顶层结果，只处理能通过标签确定原 PDF 的附件。已有父条目的结果保持原位置；独立 PDF 的新父条目仅填写标题，其他文献信息可按需补充。只读库或来源缺失的结果会跳过。整理前的关系记录保存在 Zotero 配置目录的 `paperloom/attachment-groups-*.json`，用户论文、批注和附件文件不作改写。

本项目基于原项目修改，保留上游版权与 AGPL-3.0-or-later 许可证。第三方内容见 `THIRD_PARTY_NOTICES.md`。

本发行版采用手动更新。Zotero 要求 manifest 包含 update_url，因此保留本机回环地址占位；它不指向外部更新服务。发布者建立自己的更新源后可替换此字段。
