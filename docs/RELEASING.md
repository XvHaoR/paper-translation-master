# 发布维护

0.6.0 仅发布云端 OCR 运行包及插件，保留旧本地引擎源码供已有用户维护，继续保留 0.5.0 GPU／CPU 一键安装器和完整部署文档。

版本号需在 release.json、pyproject.toml 和 zotero/manifest.json 一致。发布前执行单元／集成／DOM 测试和隔离 Zotero 回归。

通用 XPI、源码及运行代码由 packaging/build_release.py 白名单打包；Windows 完整包由 scripts/build_cloud_bundle.py 构建，使用校验过的 CPython 3.13 x64 嵌入式运行环境和核心依赖，不携带 OCR 模型、工作区或用户配置。

云端 XPI 使用独立 cloud-updates.json 通道，旧本地用户不会被自动切换运行方式。首次安装和更换云端版本应使用完整 ZIP，注册新目录后安装同版 XPI。

GitHub Release 工作流在 Windows 上测试、构建并生成校验值。发布正文说明 PDF 上传行为、官方 API 注册入口、配置差别及离线结果管理。任何用户密钥都不得进入提交或发行文件。
