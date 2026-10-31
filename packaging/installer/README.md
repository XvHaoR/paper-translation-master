# Windows OCR 安装器

面向 Windows 10/11 x64 + NVIDIA GPU 的图形安装器。下载 Release 中的
`paper-translation-master-setup-0.3.0.exe`，双击，点击「安装 OCR 引擎」。
用户不需要安装 Python、Git、WSL、Docker 或输入终端命令。

当前自动安装范围保守限制为至少 8 GB 显存、CC 7.x–9.x 和支持 CUDA 12.6
的 NVIDIA Windows 驱动（560.76 或更新）；推荐 12 GB 显存。RTX 50 系列、
其他厂商 GPU、CPU 和 macOS 不在这个自动安装包的承诺范围内，可参考 PaddleOCR
官方文档手动配置。已保存的 OCR/翻译无需 OCR 引擎即可继续阅读。

安装目录默认为 `%LOCALAPPDATA%/PaperTranslationMaster`，可在界面更改到其他磁盘。
需至少 15 GB 空闲空间，建议 25 GB；实际下载大小依赖 Paddle/CUDA 依赖和模型缓存。
程序不改 PATH，不安装全局 Python，不动已有 WSL 或 OCR 环境。

进度依次为硬件检查、解压引擎、私有 Python、OCR 依赖、模型下载和真实 PDF 自检、
Zotero 自动发现注册。模型下载阶段显示官方下载字节进度；不伪造剩余时间。
只有完整 PDF 自检识别出预期文本并确认使用 GPU 后，才写入 `runtime.json`。
失败与取消保留下载缓存，重新打开安装器可重试；Pip 也复用自己的下载缓存。
首次安装仍须联网，成功后模型可离线复用。安装后仍需在 Zotero 中安装 XPI 并重启。

日志在安装目录的 `install.log` 和 `install-state.json`，实际自检结果在 `warmup.json`。
自检和安装器退出会清理自己拥有的子进程，不终止其他 Python、WSL 或 Zotero 实例。

## 维护与构建

`release.json` 是品牌、版本和目录名的单一来源；安装器不依赖开发机用户名/路径。
固定 Python 3.13.17、pip 26.2.2、Paddle GPU 3.3.1（官方 cu126 源）、
PaddleOCR 3.7.0、PaddleX 3.7.2，其他直接依赖见 `requirements-runtime.txt`。
CPython 和 pip 启动文件采用固定 HTTPS 下载及 SHA-256 校验，Pip 使用 HTTPS 包索引，
直接依赖固定版本、传递依赖由 Pip 解决；这不是完整的离线依赖锁文件。

由发布脚本打包 runtime ZIP（根目录直接包含 app/、scripts/、packaging/installer/），
再使用已安装 PyInstaller 的 Python 执行：

```powershell
python packaging/installer/build.py --runtime dist/paper-translation-master-runtime-0.3.0.zip --output dist
```

构建不会安装 OCR 或下载模型，只将文档引擎源码内嵌到 Setup.exe。
构建后自动执行 `Setup.exe --self-test`，验证必备资源及 ZIP 完整性。
这些构建/控制逻辑测试不能替代在干净 Windows GPU 机器上的安装验收。

自动发现文件固定在 `%LOCALAPPDATA%/PaperTranslationMaster/runtime.json`，schema 1：
`projectRoot`, `pythonPath`, `backend`（local）, `wslDistro`（null）, `environment`。
`environment` 仅含 `PAPERBRIDGE_WORKSPACE`、`PADDLE_PDX_CACHE_HOME`、`HF_HOME`、
`MODELSCOPE_CACHE`、`PAPERBRIDGE_OCR_PAGE_BATCH_SIZE`、`PAPERBRIDGE_OCR_USE_QUEUES`。
自定义磁盘安装后会在固定发现位置复制同一描述符。使用本地 backend 时应清除进程继承的
`PAPERBRIDGE_OCR_VL_BACKEND`/`PAPERBRIDGE_OCR_VL_SERVER_URL` 等远端环境开关。

## 官方依据（2026-10-31 核对）

- [PaddleOCR-VL 官方使用文档](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html)：
  PaddlePaddle 本地推理、doc-parser 安装、首次模型下载；FastDeploy/vLLM 不原生运行于 Windows。
- [Paddle 官方 cu126 索引](https://www.paddlepaddle.org.cn/packages/stable/cu126/paddlepaddle-gpu/)：
  包含 3.3.1 的 CPython 3.13 win_amd64 轮子。
- [CPython 3.13.17](https://www.python.org/downloads/release/python-31317/)：
  官方 Windows embeddable 64 位下载及 SHA-256；嵌入式运行时由本应用管理依赖。
- [Python Windows 嵌入式发行包说明](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)：
  发行包面向应用内私有部署，不将它加入用户 PATH，也不作为通用 Python 环境。
- [FastDeploy 官方安装](https://paddlepaddle.github.io/FastDeploy/zh/get_started/installation/nvidia_gpu/)：
  本项目已有 WSL 加速路径的官方版本来源；此安装器不自动部署 FastDeploy。
