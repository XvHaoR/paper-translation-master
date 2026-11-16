"use strict";
(() => {
  const PL = PaperLoom;
  PL.Runtime = {
    async descriptor(path) {
      const value = await IOUtils.readJSON(path);
      if (value.schema !== 1 || typeof value.projectRoot !== "string" || typeof value.pythonPath !== "string"
        || !PathUtils.isAbsolute(value.projectRoot) || !PathUtils.isAbsolute(value.pythonPath)
        || value.backend !== "local") throw new Error("解析安装信息不完整，请重新运行安装器修复");
      if (!await IOUtils.exists(PathUtils.join(value.projectRoot, "scripts", "zotero_service.py"))
        || !await IOUtils.exists(value.pythonPath)) throw new Error("解析引擎文件缺失，请重新运行安装器修复");
      return {...value, descriptor:path};
    },
    async installed() {
      if (!Zotero.isWin) return null;
      const base = Services.env.get("LOCALAPPDATA");
      if (!base) return null;
      const descriptor = PathUtils.join(base, PL.config.runtimeDirectory || "PaperTranslationMaster", "runtime.json");
      if (!await IOUtils.exists(descriptor)) return null;
      return this.descriptor(descriptor);
    },
    async resolve() {
      const installed = await this.installed();
      if (installed) return {...installed, pinned:true};
      const explicit = PL.pref("projectRoot", "");
      if (!explicit) return null;
      const releaseFile = PathUtils.join(explicit, "release.json");
      const release = await IOUtils.readJSON(releaseFile).catch(() => null);
      if (!release?.nativeOnly) throw new Error("请运行本版安装脚本并检测解析引擎，原有运行环境尚未升级");
      return {projectRoot:explicit, pythonPath:PL.pref("pythonPath", ""), backend:"local"};
    },
    async translationSettings() {
      // Read the same settings file as the service, even while it is stopped.
      const runtime = await this.resolve();
      if (!runtime) return null;
      const configured = runtime.environment?.PAPERBRIDGE_WORKSPACE || Services.env.get("PAPERBRIDGE_WORKSPACE");
      const workspace = configured
        ? (PathUtils.isAbsolute(configured) ? configured : PathUtils.join(runtime.projectRoot, configured))
        : PathUtils.join(runtime.projectRoot, "workspace");
      const path = PathUtils.join(workspace, "settings.json");
      return await IOUtils.exists(path) ? IOUtils.readJSON(path) : null;
    },
  };
})();
