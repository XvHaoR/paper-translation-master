# 在 Apple 芯片 Mac 上安装 0.8.2

适用于 Apple 芯片（M 系列）、macOS 14 及以上和 Zotero 7–10。完整包内有通用 XPI、Python、PDF 解析环境和离线多语检索模型。[下载 macOS arm64 完整包](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-macos-arm64-0.8.2.zip)

1. 退出 Zotero，将 ZIP 解压到准备长期保留的文件夹。
2. 在解压目录中运行 `Install.command`。也可以在终端输入 `/bin/zsh` 和一个空格，把该文件拖到终端后按回车。安装器会登记此目录；以后移动了它，请再运行一次。
3. 打开 Zotero，在「工具 → 插件 → 齿轮 → 从文件安装」中选择同目录的 XPI，然后重启。升级时同时更新运行环境和 XPI。
4. 从「工具 → 论文翻译大师 → 设置」选择默认识别方案。直接解析适合有文字层的 PDF。扫描件可选飞桨云端、MinerU 官方云端、自部署 MinerU，或已配置的兼容本地 OCR 引擎。

Mac 和 Windows 共用阅读器、标题翻译、批量任务、原文和译文批注、拖动排版及语义检索。查找论文时可使用包内的 CPU 模型。启用翻译时填写兼容接口、模型名称，以及该服务要求的密钥。

本地 OCR 模型需要另行部署。可在「本地 OCR 引擎」配置已有的兼容 Python 环境或本机服务地址。默认方案选「本地 OCR」后，插件会显示预热开关。外部自行启动的服务由该服务的启动程序管理。Windows 的 GPU／CPU 安装器适用于 Windows。[图文使用教程](https://github.com/XvHaoR/paper-translation-master/blob/v0.8.2/docs/TUTORIAL.md)
