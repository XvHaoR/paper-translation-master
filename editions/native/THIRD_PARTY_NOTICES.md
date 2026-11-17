# 许可证与第三方声明

## 本项目

论文翻译大师（Paper Translation Master）

Copyright (c) 2026 XvHaoR and contributors.

除下列第三方内容外，本项目原创代码依据 GNU Affero General Public License 第 3 版或之后版本（SPDX: `AGPL-3.0-or-later`）提供。完整条款见根目录 [LICENSE](LICENSE)。程序按现状提供，不作任何担保。对应源码与安装、构建脚本位于：

https://github.com/XvHaoR/paper-translation-master

第三方代码、字体、模型和运行库保留各自的原始许可，不因本项目采用 AGPL 而变更。用户导入的论文及其内容权利仍归原权利人；本声明不授予论文的再分发权，也不把用户文档本身重新许可。

## 随源码附带的 MathJax

`runtime/app/static/mathjax/` 包含 MathJax 3.2.2 的组合 JavaScript、TeX 扩展及 CommonHTML TeX 字体，用于本地公式显示。这些文件依据 Apache License 2.0 分发。上游的完整许可证保存在 [runtime/app/static/mathjax/LICENSE](runtime/app/static/mathjax/LICENSE)，本项目没有修改这些第三方程序文件。

MathJax 的相关版权归 The MathJax Consortium 与各文件贡献者所有。上游 3.2.2 源码中的版权声明包括 `Copyright (c) 2017-2022 The MathJax Consortium`；此概括不取代其他上游文件已有的版权声明。

- 发布文件：https://github.com/mathjax/MathJax/tree/3.2.2
- 对应源码：https://github.com/mathjax/MathJax-src/tree/3.2.2
- 原始许可证：https://github.com/mathjax/MathJax/blob/3.2.2/LICENSE

组合文件还包含：

| 组件 | 版本及版权 | 许可与上游 |
| --- | --- | --- |
| Speech Rule Engine | 4.0.6；Copyright 2014-21 Volker Sorge and contributors | Apache-2.0；https://github.com/Speech-Rule-Engine/speech-rule-engine/tree/v4.0.6 ；原文见 [licenses/Speech-Rule-Engine-4.0.6.txt](licenses/Speech-Rule-Engine-4.0.6.txt) |
| mhchemParser | 4.1.1；Copyright (c) 2015-2021 Martin Hensel | Apache-2.0；https://github.com/mhchem/mhchemParser ；保留在 `tex-chtml-full.js` 和相关扩展中的上游声明 |

同目录 [NOTICE](runtime/app/static/mathjax/NOTICE) 汇总上述声明。Apache-2.0 完整文本亦见 [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt)。复制这些资源到离线解析/译文阅读附件时，应同时保留该目录的 LICENSE 和 NOTICE。

## 0.4.2 本地解析依赖

本版本移除了 PaddleOCR、PaddlePaddle、PaddleX、云端识别客户端和旧 GPU/WSL 安装器。它使用 PyMuPDF、PyMuPDF Layout、PyMuPDF4LLM 2.1 分析 PDF 文字层与版面；版面分析所需的 ONNX 资源与运行库仍属于必要依赖。

准确 Python 包版本见 `runtime/requirements.txt`。Windows 完整包保留 `runtime/vendor`、`python/Lib/site-packages` 中各 distribution 的许可证、版权、第三方声明及元数据。Python 嵌入版保留其原始 LICENSE；本项目不修改这些第三方二进制。

本项目原创代码保留 Copyright (c) 2026 XvHaoR and contributors，按根目录 LICENSE 中的 AGPL-3.0-or-later 提供。本地解析、段落重建和发布脚本属于本次修改。原项目来源：https://github.com/XvHaoR/paper-translation-master 。

MathJax、CPython、PyMuPDF/MuPDF、ONNX Runtime 及其他依赖分别保留其上游许可。本项目许可不替代这些第三方条款。源码包与 Windows 完整包应一同发布，以提供本次修改的对应源码和构建/安装说明。
