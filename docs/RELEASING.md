# 构建与发布

所有命令从仓库根目录执行。需要 Python 3.12+，CI 使用 Python 3.13 和 Node.js 22。

```sh
python -m pip install -r runtime/requirements.txt
python install.py --check
python -m unittest discover -s tests -v
node tests/test_tasks.cjs
node tests/test_attachment_groups.cjs
python tools/package.py
```

输出目录为 `dist/release/`：

- `paper-translation-master-<version>.xpi`：Zotero 插件，需要配套 Python 解析环境。
- `paper-translation-master-<version>-source.zip`：含源码、安装脚本、文档及 XPI；用户运行 `Install.cmd` 或 `python install.py` 安装依赖。
- `SHA256SUMS.txt`：上述两个文件的校验值。

自动构建不包含 Windows 嵌入式 Python、依赖二进制、OCR 模型、个人工作区、论文或密钥。不能把源码 ZIP 宣称为免安装的 Windows 完整包。若另行分发 Windows 完整包，必须保留 Python 与全部依赖的许可证和元数据，并同时提供对应源码。

## CI 与正式 Release

每个 PR 及 `main` 提交都会在 Windows/Linux 上执行测试并构建制品，可从 GitHub Actions 的运行页面下载。手动运行 `Release` 工作流只构建和上传 Actions 制品，不创建公开 Release。

正式发布前更新 `addon/manifest.json`、`runtime/release.json`、`runtime/pyproject.toml`、服务版本、README 和 CHANGELOG，并执行 `python tools/package.py --tag v<version>`。构建器要求插件、运行元数据及 Python 包版本一致，且标签必须匹配。

维护者决定发布后再创建并推送 `v<version>` 标签。标签触发测试和构建，成功后创建 GitHub Release 并上传上述三个文件。已有 Release 不会被覆盖，旧标签和旧发行附件继续保留。

插件目前采用手动更新。manifest 中的回环 `update_url` 仅满足 Zotero 的字段要求，不提供在线更新；发布 GitHub Release 本身不会让现有安装自动升级。
