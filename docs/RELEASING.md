# 发布维护

0.7.1 发布融合预览版的 Windows 完整包、通用 XPI、源码和运行代码。0.5.0 Release 继续提供 GPU／CPU 安装器。

版本号需在 release.json、pyproject.toml 和 zotero/manifest.json 一致。发布前执行单元／集成／DOM 测试和隔离 Zotero 回归。

通用 XPI、源码及运行代码由 packaging/build_release.py 白名单打包；Windows 完整包由 scripts/build_cloud_bundle.py 构建，使用校验过的 CPython 3.13 x64 嵌入式运行环境和核心依赖，不携带 OCR 模型、工作区或用户配置。

融合预览版的 XPI 读取同版本的更新清单。用户通过完整 ZIP 手动升级运行环境和 XPI。旧版更新通道沿用各自的清单。

GitHub Release 工作流在 Windows 上测试、构建并生成校验值。推送与版本一致的标签可触发发布。维护者也可先上传已验收的文件，工作流会保留已有 Release 和附件。发布前核对 `SHA256SUMS`，确认包与预期提交一致，并检查包中没有用户密钥、工作区或个人论文。
