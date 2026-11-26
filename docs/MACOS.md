# 论文翻译大师 0.7.1 · macOS 安装

面向 Apple 芯片 Mac（M 系列），需要 macOS 14 或更新版本以及 Zotero 7–10。完整包自带 Python 和 PDF 版面分析环境。

1. 退出 Zotero，将完整 ZIP 解压到准备长期保留的位置。
2. 双击解压目录中的 `Install.command` 注册运行环境。也可打开「终端」，输入 `/bin/zsh` 和一个空格，把 `Install.command` 拖进终端，再按回车。
3. 打开 Zotero，在「工具 → 插件 → 从文件安装」中选择包内的 XPI，重启 Zotero。
4. 在「论文翻译大师 → 设置」中选择识别方式。选用飞桨云端 OCR 时需填写 AI Studio Access Token，选用大模型翻译时需配置翻译接口、模型和密钥。

直接解析在本机处理 PDF 的文字层。遇到扫描件可联网使用飞桨云端 OCR，所选 PDF 会上传到飞桨。阅读、编辑、批量处理及导出使用 0.7.1 的同一套 Zotero 插件。

本地 OCR 需要自行配置兼容的 Mac 引擎或已启动的本机服务。Windows NVIDIA／WSL／RapidOCR 安装器仅用于 Windows。

移动解压目录后，请重新运行其中的 `Install.command`。插件沿用原来的 ID，已保存的 Zotero 附件可继续打开。
