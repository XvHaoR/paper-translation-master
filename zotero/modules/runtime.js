"use strict";
(() => {
  const PL = PaperLoom;
  PL.Runtime = {
    async descriptor(path) {
      const value = await IOUtils.readJSON(path);
      if (value.schema !== 1 || typeof value.projectRoot !== "string" || typeof value.pythonPath !== "string"
        || !PathUtils.isAbsolute(value.projectRoot) || !PathUtils.isAbsolute(value.pythonPath)
        || !["local", "wsl"].includes(value.backend)) throw new Error("OCR 安装信息不完整，请重新运行安装器修复");
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
      // Local test builds pin a complete code snapshot without changing the
      // stable install descriptor or saved engine preferences.
      if (PL.config.localHTMLPreview && PL.config.localRuntimeDescriptor) {
        return {...await this.descriptor(PL.config.localRuntimeDescriptor),pinned:true};
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
    download() {
      Zotero.launchURL("https://github.com/XvHaoR/paper-translation-master/releases");
    },
  };
})();
