# 许可证与第三方声明

## 本项目

论文翻译大师（Paper Translation Master）

Copyright (c) 2026 XvHaoR and contributors.

除下列第三方内容外，本项目原创代码依据 GNU Affero General Public License 第 3 版或之后版本（SPDX: `AGPL-3.0-or-later`）提供。完整条款见根目录 [LICENSE](LICENSE)。程序按现状提供，不作任何担保。对应源码与安装、构建脚本位于：

https://github.com/XvHaoR/paper-translation-master

第三方代码、字体、模型和运行库保留各自的原始许可，不因本项目采用 AGPL 而变更。用户导入的论文及其内容权利仍归原权利人；本声明不授予论文的再分发权，也不把用户文档本身重新许可。

## 随源码附带的 MathJax

`app/static/mathjax/` 包含 MathJax 3.2.2 的组合 JavaScript、TeX 扩展及 CommonHTML TeX 字体，用于本地公式显示。这些文件依据 Apache License 2.0 分发。上游的完整许可证保存在 [app/static/mathjax/LICENSE](app/static/mathjax/LICENSE)，本项目没有修改这些第三方程序文件。

MathJax 的相关版权归 The MathJax Consortium 与各文件贡献者所有。上游 3.2.2 源码中的版权声明包括 `Copyright (c) 2017-2022 The MathJax Consortium`；此概括不取代其他上游文件已有的版权声明。

- 发布文件：https://github.com/mathjax/MathJax/tree/3.2.2
- 对应源码：https://github.com/mathjax/MathJax-src/tree/3.2.2
- 原始许可证：https://github.com/mathjax/MathJax/blob/3.2.2/LICENSE

组合文件还包含：

| 组件 | 版本及版权 | 许可与上游 |
| --- | --- | --- |
| Speech Rule Engine | 4.0.6；Copyright 2014-21 Volker Sorge and contributors | Apache-2.0；https://github.com/Speech-Rule-Engine/speech-rule-engine/tree/v4.0.6 ；原文见 [licenses/Speech-Rule-Engine-4.0.6.txt](licenses/Speech-Rule-Engine-4.0.6.txt) |
| mhchemParser | 4.1.1；Copyright (c) 2015-2021 Martin Hensel | Apache-2.0；https://github.com/mhchem/mhchemParser ；保留在 `tex-chtml-full.js` 和相关扩展中的上游声明 |

同目录 [NOTICE](app/static/mathjax/NOTICE) 汇总上述声明。Apache-2.0 完整文本亦见 [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt)。复制这些资源到离线 OCR/译文阅读附件时，应同时保留该目录的 LICENSE 和 NOTICE。

## 安装时下载的 OCR、PDF 与模型组件

安装器从官方 Python 网站、PyPI、PaddlePaddle 官方软件包索引和模型源下载运行环境。发行源码及小型安装控制程序不附带 OCR 模型权重，也不附带整套已安装的 Python packages。下载后，各个 distribution 自带的许可证、版权和第三方声明必须保留。下表列出主要组件；准确版本以发行时的安装器与 `packaging/installer/requirements-runtime.txt` 为准。

| 组件 | 当前安装版本/用途 | 原始许可与官方来源 |
| --- | --- | --- |
| PyMuPDF / MuPDF | PyMuPDF 1.28.0；PDF 读取、页面与区域处理 | AGPL-3.0，另有商业许可；https://github.com/pymupdf/PyMuPDF/blob/main/COPYING ；https://github.com/pymupdf/PyMuPDF#licensing |
| PaddleOCR | 3.7.0；文档识别流水线 | Apache-2.0；https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE |
| PaddleX | 3.7.2；OCR 流水线实现 | Apache-2.0；https://github.com/PaddlePaddle/PaddleX/blob/release/3.7/LICENSE |
| PaddlePaddle GPU | 3.3.1；GPU 推理运行库 | Apache-2.0，所含第三方库另有声明；https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE |
| PaddleOCR-VL-1.6 | 视觉语言 OCR 模型 | Apache-2.0；https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6 |
| PP-DocLayoutV3 | 版面检测模型 | Apache-2.0；https://huggingface.co/PaddlePaddle/PP-DocLayoutV3 |
| FastDeploy | 可选的已有 WSL 加速环境；基础一键安装器不安装 | Apache-2.0；https://github.com/PaddlePaddle/FastDeploy/blob/develop/LICENSE |

PyMuPDF/MuPDF 的开源许可条件独立适用。本项目的 `AGPL-3.0-or-later` 声明只适用于本项目原创代码，不扩大第三方授予的许可范围。需要不符合 AGPL 条件的专有使用方式时，应向对应权利人取得相应许可。

NVIDIA 驱动、CUDA/cuDNN 等运行组件按 NVIDIA 及实际软件包的条款提供。本项目不授予 NVIDIA 产品许可；安装器不重新分发显卡驱动。通过官方软件包下载的第三方 notices 应保留在其安装位置。

FastAPI、Uvicorn、WebSockets、Typer、Rich、Jinja2、Python-Markdown、Pydantic、Pillow、Bleach、python-multipart、OpenAI Python SDK 及其依赖均由各自维护者提供，保留各 distribution 的 LICENSE、COPYING 和 notices。本文件不是删除或替代这些 notices 的依据。

## Python 与安装器构建组件

一键安装器下载 CPython 3.13.17 官方 Windows 嵌入版及 pip wheel。CPython 使用 Python Software Foundation License 和其保留的历史许可；副本见 [licenses/Python-3.13.txt](licenses/Python-3.13.txt)，上游：https://github.com/python/cpython/blob/v3.13.17/LICENSE 。下载的 Python 目录及 pip distribution 中原有的许可证应保留。

Windows Setup.exe 使用 PyInstaller 6.21.0 构建。PyInstaller 使用 GPL 2.0 并带有允许以应用自身许可分发所生成程序的 bootloader 例外。完整原文见 [licenses/PyInstaller-6.21.0.txt](licenses/PyInstaller-6.21.0.txt)，上游：https://github.com/pyinstaller/pyinstaller/blob/v6.21.0/COPYING.txt 。本项目安装器自身的源码仍按上述 AGPL 许可提供。

Setup.exe 打入的构建解释器、Tcl/Tk、OpenSSL、zlib 等库使用各自原许可；其 notices 随发行构建保留。更换构建解释器或新增捆绑依赖时，发行者应同步更新对应的版权与许可证副本。此声明不意味着所有这些组件都已静态捆绑在每一种发行物中。

### 0.3.0 Windows 安装器的实际 DLL

本地 0.3.0 Setup.exe 的归档已检查，下列版本来自实际归档的 PE 版本资源、二进制版本字符串，或与构建环境文件的 SHA-256 对照。它们属于安装控制程序的依赖，与安装完成后下载的 OCR Python 环境不同。CI 使用官方 Python 重新构建时，补丁版本可随构建解释器改变；对应的原始许可证仍适用，构建记录应保留实际清单。

| 实际 DLL / 组件 | 本地构建版本 | 版权及许可副本 |
| --- | --- | --- |
| `python313.dll` | CPython 3.13.5，Conda build | Python Software Foundation 及保留的历史权利人；[Python-build-3.13.5.txt](licenses/Python-build-3.13.5.txt) |
| `LIBBZ2.dll` | libbzip2 1.0.8 | Copyright (C) 1996-2019 Julian R Seward；bzip2 许可全文：[bzip2-1.0.8.txt](licenses/bzip2-1.0.8.txt) |
| `ffi.dll` | libffi 3.4.4 | Copyright (c) 1996-2022 Anthony Green, Red Hat, Inc and others；MIT：[libffi-3.4.4.txt](licenses/libffi-3.4.4.txt) |
| `liblzma.dll` | XZ Utils/liblzma 5.8.3 | Copyright (C) The XZ Utils authors and contributors；liblzma 为 0BSD：[liblzma-5.8.3-0BSD.txt](licenses/liblzma-5.8.3-0BSD.txt)，组件范围说明：[XZ-5.8.3-COPYING.txt](licenses/XZ-5.8.3-COPYING.txt) |
| `libmpdec-4.dll` | libmpdec 4.0.0 | Copyright (c) 2008-2024 Stefan Krah；BSD-2-Clause：[libmpdec-4.0.0.txt](licenses/libmpdec-4.0.0.txt) |
| `zlib.dll` | zlib 1.2.13 | Copyright (C) 1995-2022 Jean-loup Gailly and Mark Adler；zlib：[zlib-1.2.13.txt](licenses/zlib-1.2.13.txt) |
| `libcrypto-3-x64.dll`、`libssl-3-x64.dll` | OpenSSL 3.6.4 | Copyright 1998-2026 The OpenSSL Authors；Apache-2.0：[OpenSSL-3.6.4.txt](licenses/OpenSSL-3.6.4.txt) |
| `VCRUNTIME140.dll` | 14.42.34438.0 | Copyright Microsoft Corporation. All rights reserved；Microsoft 运行库许可，见下文 |
| `ucrtbase.dll`、`api-ms-win-*.dll` | UCRT 10.0.22621.3233 | Copyright Microsoft Corporation. All rights reserved；Microsoft Windows SDK/运行库分发条款，见下文 |

Microsoft 运行库不是本项目的 AGPL 代码。其接收方条款保存在 [Microsoft-Visual-C-Runtime-2015-2022.txt](licenses/Microsoft-Visual-C-Runtime-2015-2022.txt)。开发者的可分发代码权利来自其 Visual Studio / Build Tools / Windows SDK 许可及相应 redistributable 清单，而不是从本项目许可证获得。官方分发说明：https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files 。该说明与上游来源记录见 [Bundled-Windows-Runtime-Sources.txt](licenses/Bundled-Windows-Runtime-Sources.txt)。

## 升级与再分发

再分发源码、XPI、安装器或包含第三方程序的离线阅读包时，请保留适用的 LICENSE、版权与第三方声明。修改第三方文件时，另行标明修改。项目名称、作者及上游名称只用于来源说明，不表示 Zotero、PaddlePaddle、MathJax、Artifex、Python 或其他上游为本项目背书。
