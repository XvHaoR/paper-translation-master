# 本地文字解析方案

仓库内保留两套独立维护的实现：根目录的云端/本地 OCR 方案，以及 `editions/native/` 中的本地文字解析版。两者分别维护运行环境、版本和构建过程。

| 项目 | 本地文字解析版 | 云端/本地 OCR 版 |
| --- | --- | --- |
| 适用文献 | 有可用文字层的 PDF | 包含扫描页、图片文字或需要 OCR 识别的 PDF |
| 解析方法 | PyMuPDF 2.1 文字提取与版面分析 | 现有 PaddleOCR-VL、飞桨云端或 RapidOCR 流程 |
| 识别配置 | 无需 OCR Token、GPU 或 OCR 模型 | 按所选 OCR 方案配置 |
| 行号 | 根据页边/栏间坐标、数字序列和字体特征过滤 | 按现有 OCR 及后处理实现处理 |
| 公式 | 保留原 PDF 公式图像 | 可提供识别出的 LaTeX，具体取决于 OCR 结果 |
| 扫描页 | 保留原页图像并标记待核对，不自动识别 | 使用相应 OCR 后端 |
| 大模型翻译 | 需要独立配置翻译服务 | 需要独立配置翻译服务 |

行号过滤采用通用规则，无法保证覆盖所有投稿模板。它保留难以确认的数字，避免误删正文中的年份、引用与数据。复杂版式仍需对照原 PDF 核对。原始 PDF 不会被改写。

## 安装与切换

1. 克隆仓库后进入 `editions/native`，使用 Python 3.12+ 运行 `python install.py`，或在 Windows 双击该目录的 `Install.cmd`。
2. 安装器在该目录建立独立 `.venv`，安装固定依赖，构建 XPI 并注册运行环境。
3. 在 Zotero 的插件管理中，从文件安装 `editions/native/dist/paper-translation-master-0.4.1.xpi`。
4. 在插件设置中检测已安装引擎，按需填写自己的翻译服务。该方案的工作区默认位于用户数据目录 `PaperTranslationMasterNative/workspace`。

各方案共用 `paperloom@paperloom.local` 插件 ID，同一 Zotero 配置中安装一个方案会替换另一个方案。已有 PDF、解析和译文附件保留；新运行环境的翻译设置需要单独配置。回到云端/GPU/CPU 版时，按根目录 README 安装对应插件和环境。

本地解析版没有自动调用云端或本地 OCR 的备用步骤。扫描页需切换到相应 OCR 方案处理。解析在本机执行；主动翻译时，会将待翻译文本发送给用户配置的翻译服务。

## 开发与验证

两套环境中的 PyMuPDF 版本不同，请分别使用虚拟环境。在 `editions/native` 中执行：

```text
python -m pip install -r runtime/requirements.txt
python -m unittest discover -s tests -v
node tests/test_tasks.cjs
python tools/package.py
```

独立工作流 `.github/workflows/native.yml` 在 Windows/Linux、Python 3.13 上安装本版依赖，运行测试并构建产物。根目录原有测试、构建和发布工作流继续服务现有 OCR 方案。本地解析版完整的 Zotero Windows 实机验证和 Linux 依赖/单元测试是不同验证范围；不将 CI 通过等同于跨平台实机验收。

Git 跟踪源码、测试、许可证与 Markdown 文档。生成的 XPI、源码 ZIP、Windows 完整包作为构建产物或 Release 附件分发，不提交 Python 环境、API 密钥、用户文献或译文。维护者发布本版时可使用独立的 `native-v0.4.1` 标签；本次 PR 不改动根目录的自动更新通道，也不自动发布 Release。

## 维护边界

- `editions/native/addon/`：本版 Zotero 插件及本地解析版界面。
- `editions/native/runtime/`：本版服务、文字层解析、行号过滤、阅读顺序和翻译导出。
- `editions/native/tests/`：生成式 PDF、API、导出、缓存及人工编辑保护回归。
- `editions/native/tools/`：本版 XPI 与源码包构建。

本地版使用 `0.4.1` 这一独立版本号，并不表示其代码可直接覆盖根目录的 `0.6.0`。后续共享修复可逐项迁移，提交时说明涉及哪一套实现。
