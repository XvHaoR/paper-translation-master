"use strict";
(() => {
  const PL = PaperLoom;
  PL.Runtime = {
    unifiedDirectory: "PaperTranslationMasterUnified",
    dataDirectory(name) {
      const base = Zotero.isWin ? Services.env.get("LOCALAPPDATA")
        : Services.env.get("XDG_DATA_HOME") || PathUtils.join(Services.env.get("HOME"), ".local", "share");
      return PathUtils.join(base, name);
    },
    async named(name) {
      const path = PathUtils.join(this.dataDirectory(name), "runtime.json");
      return await IOUtils.exists(path) ? this.descriptor(path) : null;
    },
    async descriptor(path) {
      const value = await IOUtils.readJSON(path);
      if (value.schema !== 1 || typeof value.projectRoot !== "string" || typeof value.pythonPath !== "string"
        || !PathUtils.isAbsolute(value.projectRoot) || !PathUtils.isAbsolute(value.pythonPath)
        || !["local", "wsl", "cloud"].includes(value.backend)) throw new Error("OCR 安装信息不完整，请重新运行安装器修复");
      if (!await IOUtils.exists(PathUtils.join(value.projectRoot, "scripts", "zotero_service.py"))
        || !await IOUtils.exists(value.pythonPath)) throw new Error("OCR 引擎文件缺失，请重新运行安装器修复");
      return {...value, descriptor:path};
    },
    async installed(projectRoot = "") {
      if (!Zotero.isWin) return null;
      const base = Services.env.get("LOCALAPPDATA");
      if (!base) return null;
      const directories = [...new Set([PL.config.runtimeDirectory || "PaperTranslationMaster", "PaperTranslationMasterRapidOCR"])];
      let failure;
      for (const directory of directories) {
        const path = PathUtils.join(base,directory,"runtime.json");
        if (!await IOUtils.exists(path)) continue;
        try {
          const runtime = await this.descriptor(path);
          if (projectRoot && runtime.projectRoot !== projectRoot) continue;
          runtime.ocrMode ||= directory === "PaperTranslationMasterRapidOCR" ? "rapidocr" : "official";
          return runtime;
        } catch (error) { failure = error; }
      }
      if (failure) throw failure;
      return null;
    },
    async resolve() {
      if (PL.config.unified) {
        const runtime = await this.named(this.unifiedDirectory);
        if (!runtime || runtime.backend !== "local") throw new Error("请先运行融合版安装包中的 Install.cmd（源码安装使用 install.py）");
        return {...runtime, pinned:true};
      }
      // Local test builds pin a complete code snapshot without changing the
      // stable install descriptor or saved engine preferences.
      if (PL.config.localHTMLPreview && PL.config.localRuntimeDescriptor) {
        return {...await this.descriptor(PL.config.localRuntimeDescriptor),pinned:true};
      }
      if (PL.config.cloudPreview) {
        const runtime = await this.installed();
        if (!runtime || runtime.backend !== "cloud") throw new Error("请先运行包内的 Install-Cloud.cmd");
        return {...runtime,pinned:true};
      }
      if (PL.config.ocrMode === "rapidocr") {
        const installed = await this.installed();
        if (installed) return {...installed,backend:"local",ocrMode:"rapidocr"};
      }
      const explicit = PL.pref("projectRoot", "");
      if (explicit) {
        let installed;
        try { installed = await this.installed(explicit); } catch (_) { /* A manual checkout remains usable. */ }
        if (installed?.projectRoot === explicit && installed.ocrMode === "rapidocr") return {...installed,backend:"local"};
        return {...(installed?.projectRoot === explicit ? installed : {}),projectRoot:explicit,
          pythonPath:PL.pref("pythonPath", "") || (installed?.projectRoot === explicit ? installed.pythonPath : ""),
          backend:PL.pref("backend", installed?.projectRoot === explicit ? installed.backend : PL.config.defaultBackend || "local")};
      }
      // A development build declares its checkout explicitly. Public packages
      // omit machine paths and discover the installed, verified runtime.
      if (PL.config.projectRoot && await IOUtils.exists(PathUtils.join(PL.config.projectRoot,"scripts","zotero_service.py"))) {
        return {projectRoot:PL.config.projectRoot,pythonPath:PL.pref("pythonPath", ""),backend:PL.pref("backend",PL.config.defaultBackend || "local")};
      }
      const installed = await this.installed();
      if (installed?.ocrMode === "rapidocr") return {...installed,backend:"local"};
      if (installed) return {...installed,pythonPath:PL.pref("pythonPath", "") || installed.pythonPath,
        backend:PL.pref("backend", installed.backend)};
      return null;
    },
    async localEngine(runtime) {
      if (runtime.backend !== "wsl") return { ...runtime, pinned:true };
      const unified = await this.resolve();
      // Keep the registered model environment and run the compatible service from this release.
      return { ...runtime, projectRoot:unified.projectRoot,
        workspace:PathUtils.join(this.dataDirectory(this.unifiedDirectory), "local-wsl"), pinned:true };
    },
    async localOCR(selected = PL.pref("unifiedLocalEngine", "auto")) {
      if (selected === "custom") {
        const projectRoot = PL.pref("unifiedLocalRoot", "");
        const pythonPath = PL.pref("unifiedLocalPython", "");
        if (!projectRoot || !pythonPath || !PathUtils.isAbsolute(projectRoot) || !PathUtils.isAbsolute(pythonPath))
          throw new Error("请填写本地 OCR 项目目录与 Python 的绝对路径");
        return {projectRoot, pythonPath, backend:PL.pref("unifiedLocalBackend", "local"), pinned:true};
      }
      const variants = {gpu:"PaperTranslationMaster", cpu:"PaperTranslationMasterRapidOCR"};
      let failure;
      for (const key of selected === "auto" ? ["gpu", "cpu"] : Object.keys(variants).filter(key => key === selected)) {
        try {
          const runtime = await this.named(variants[key]);
          if (runtime && ["local", "wsl"].includes(runtime.backend)) {
            return await this.localEngine({...runtime, ocrMode:key === "cpu" ? "rapidocr" : "official"});
          }
        } catch (error) { failure = error; }
      }
      // Older manual installations keep their paths in the legacy preferences.
      if (selected === "auto") {
        const projectRoot = PL.pref("projectRoot", "");
        const pythonPath = PL.pref("pythonPath", "");
        if (projectRoot && pythonPath && await IOUtils.exists(PathUtils.join(projectRoot, "release.json"))) {
          const release = await IOUtils.readJSON(PathUtils.join(projectRoot, "release.json")).catch(() => null);
          const backend = PL.pref("backend", "local");
          if (release && !release.nativeOnly && !release.cloudPreview && ["wsl", "local"].includes(backend))
            return this.localEngine({projectRoot, pythonPath, backend});
        }
      }
      throw failure || new Error("未找到所选的本地 OCR 引擎。请安装 GPU／CPU 引擎，或在设置中指定已有环境。");
    },
    async translationSettings() {
      const runtime = await this.resolve();
      const workspace = runtime.environment?.PAPERBRIDGE_WORKSPACE || PathUtils.join(runtime.projectRoot, "workspace");
      const file = PathUtils.join(workspace, "settings.json");
      return await IOUtils.exists(file) ? IOUtils.readJSON(file) : null;
    },
    download() {
      Zotero.launchURL("https://github.com/XvHaoR/paper-translation-master/releases");
    },
  };
})();
