# RapidOCR 完整文档版

0.4.0 是可替换安装的 CPU 版插件。保留 Zotero 插件身份、已保存 OCR/译文附件、编辑与阅读协议。
0.3.1 的 PaddleOCR-VL 版继续在历史发布中提供；两个版本使用各自的引擎目录。

## 识别功能

并非单独用 RapidOCR 提取文字。完整流水线使用 RapidAI 官方 RapidDoc 0.9.11：

| 内容 | 处理方式 |
| --- | --- |
| 标题、正文、摘要、图名、表名 | PP-DocLayoutV3 版面与阅读顺序，保留原始类别 |
| 文字 | RapidOCR 3.10.0；可利用 PDF 自带文字，仍执行公式和表格分析 |
| 行内与行间公式 | PP-FormulaNet_plus-M；保持公式识别开启，输出可复制的 LaTeX |
| 有线与无线表格 | 表格分类 + UNET / SLANet-plus；保留 HTML 单元格与 Excel 导出 |
| 组合图 | 官方图片区域 + 现有组合图导出，保留原始 PDF 区域 |

公式和表格未返回结构化结果时，任务会报出具体原因；不会用纯文字或截图冒充完整识别。
不同模型的准确率不会完全相同，切换后可以保留原结果对照、编辑和复核。
完整功能模式没有通过关闭行内公式、降低分辨率或裁掉表格来追求耗时数字。

## 安装

1. 从发布页下载 `paper-translation-master-rapidocr-setup-0.4.0.exe`，安装独立 OCR 引擎。
2. 在 Zotero 插件管理器安装 `paper-translation-master-rapidocr-0.4.0.xpi` 并重启。
3. 打开设置检测引擎并保存；按需配置自己的翻译服务。已有 OCR 和译文可直接查看。

安装器适用于 Windows 10 2004+／Windows 11 x64，CPU 即可，无需独立显卡、NVIDIA 驱动、CUDA、WSL 或系统 Python。
建议至少 8 GB 内存，安装盘至少 5 GB 空间；首次联网下载运行库与完整模型。
公式模型约 566 MB，版面模型约 124 MB，另有文字、表格模型与 Python 依赖；完整功能需要这些模型。
默认安装目录 `%LOCALAPPDATA%/PaperTranslationMasterRapidOCR`，与 VL 引擎隔离，不覆盖其 Python 或模型。

首次安装必须在扫描 PDF 上同时验证文字、LaTeX 公式、表格结构；只有全部通过才注册引擎。
模型在同一服务中复用，批量完成不打开阅读标签页，退出 Zotero 后仍按原租约机制清理进程。

## 源码运行

```powershell
python -m pip install -e ".[rapidocr]"
$env:PAPERBRIDGE_OCR_MODE = "rapidocr"
python -m app.cli serve --host 127.0.0.1 --port 8765
```

其他平台可以使用源码安装；自动安装器目前仅提供 Windows x64。

官方接口与能力来源：

- https://github.com/RapidAI/RapidDoc/tree/v0.9.11
- https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/install/

## 回归验证

后端适配不改动字体、阅读通信、PDF 坐标匹配、翻译或 Excel 模块。
测试覆盖模型能力开关、图注分类、公式/表格载荷、任意页码映射、缓存隔离、完整性失败、CPU 安装自检与旧 GPU 偏好。
原生 Zotero 测试可通过 `python tests/zotero/run_smoke.py --ocr-mode rapidocr` 运行。

0.4.0 已在 Windows 上的独立 CPython 3.13 环境完成真实安装与模型自检，并通过 310 项自动测试、151 项原生 Zotero 检查。
本机 AMD Ryzen 9 9900X 仅用 CPU，完整解析一份 22 页论文约 124 秒（包含行内公式，输出 13 个行间公式及图文内容）。
这是单个样例的实测，不是所有论文的速度承诺；GPU VL 版对某些论文仍可能更快。
