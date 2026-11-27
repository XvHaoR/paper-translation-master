"use strict";
(() => {
  const PL = PaperLoom;
  PL.Service = {
    state: { phase: "stopped", message: "服务未启动" },
    configuredURL() {
      if (!PL.config?.unified) return PL.serviceURL();
      return this.localOCR ? PL.serviceURL(PL.pref("unifiedLocalURL", "http://127.0.0.1:8765"))
        : PL.serviceURL("http://127.0.0.1:8790");
    },
    url() { return this.readyURL || this.configuredURL(); },
    async online(url = this.url()) {
      try {
        const response = await Zotero.HTTP.request("GET", PL.serviceURL(url) + "/api/zotero/status", { timeout: 1500, responseType: "json" });
        const minimum = !this.localOCR && PL.config?.unified ? PL.config.minimumCoreSchema : 0;
        return response.response?.protocol === 2 && response.response?.export_schema >= Math.max(minimum || 0, 7, PL.config?.minimumExportSchema || 7)
          && !!response.response?.cloud_ocr === (this.backend() === "cloud")
          && (!response.response?.managed_owner || response.response.managed_owner === this.instance)
          && (!PL.config?.unified || this.localOCR || ["mineru","mineru-local"].every(mode => response.response?.ocr_modes?.includes(mode)))
          && (this.ocrMode() !== "rapidocr" || response.response?.ocr_modes?.includes("rapidocr"))
          && (!PL.config?.unified || this.localOCR !== Boolean(response.response?.features?.includes("unified-recognition")))
          && ["force-ocr", "cancel-ocr", "edited-source"].every(feature => response.response?.features?.includes(feature));
      }
      catch (_) { return false; }
    },
    update(phase, message) {
      this.state = { phase, message };
      PL.UI?.refreshService?.();
      if (this.localOCR) PL.UI?.refreshTasks();
    },
    async ensure() {
      if (this.shuttingDown || !PL.alive) throw new Error("插件正在关闭");
      if (this.stopping) { await this.stopping; return this.ensure(); }
      if (this.starting) return this.starting;
      this.starting = this.ensureReady(this.generation).finally(() => {
        this.starting = null;
        PL.UI?.refreshService?.();
      });
      return this.starting;
    },
    generation: 0,
    checkStart(generation) {
      if (generation !== this.generation || this.shuttingDown || !PL.alive) throw new Error("启动已取消");
    },
    async ensureReady(generation) {
      // The leased child is already watched by process.wait(). A transient
      // HTTP timeout between papers must not tear down its warm OCR model.
      if (this.state.phase === "ready" && this.readyURL) {
        if (this.owned && this.process && this.instance) return;
        if (Date.now() - (this.lastReadyAt || 0) < 15000) return;
        for (let attempt = 0; attempt < 3; attempt++) {
          const online = await this.online();
          this.checkStart(generation);
          if (online) { this.lastReadyAt = Date.now(); return; }
          if (attempt < 2) await Zotero.Promise.delay(300 * (attempt + 1));
        }
      }
      this.checkStart(generation);
      return this.start(generation);
    },
    async project() {
      const runtime = this.localOCR ? await PL.Runtime.localOCR() : (PL.Runtime ? await PL.Runtime.resolve() : null);
      this.runtime = runtime;
      const root = runtime?.projectRoot || PL.pref("projectRoot", PL.config.projectRoot);
      if (!root || !await IOUtils.exists(PathUtils.join(root, "scripts", "zotero_service.py"))) {
        throw new Error("尚未安装 OCR 引擎。请打开「论文翻译大师 → 设置 → 下载 OCR 安装器」，安装完成后点击「检测已安装引擎」即可");
      }
      return root;
    },
    async python(root) {
      const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
      if (this.runtime?.pinned) {
        if (await IOUtils.exists(this.runtime.pythonPath)) return this.runtime.pythonPath;
        throw new Error("探索版 Python 环境已移动，请恢复原环境后重试");
      }
      const candidates = [PL.pref("pythonPath", ""), this.runtime?.projectRoot === root ? this.runtime.pythonPath : "", PathUtils.join(root, ".venv", Zotero.isWin ? "Scripts" : "bin", Zotero.isWin ? "python.exe" : "python"),
        ...(PL.config.pythonCandidates || [])].filter(Boolean);
      for (const path of candidates) if (await IOUtils.exists(path)) return path;
      return Subprocess.pathSearch(Zotero.isWin ? "python.exe" : "python3");
    },
    mineruConfigPath() { return PathUtils.join(PL.Store.directory, "mineru-settings.json"); },
    openMineruTokenPage() { Zotero.launchURL("https://mineru.net/apiManage/token"); },
    async writeMineruConfig(config) {
      const path = this.mineruConfigPath();
      await IOUtils.writeJSON(path, config, {tmpPath:path + ".tmp"});
      if (!Zotero.isWin) await IOUtils.setPermissions(path, 0o600);
    },
    cloudTokenPath() { return PathUtils.join(PL.Store.directory, "cloud-ocr-token"); },
    ocrMode() {
      if (PL.config?.unified) {
        if (!this.localOCR) return "layout";
        return this.runtime?.ocrMode || (PL.pref("unifiedLocalEngine", "auto") === "cpu" ? "rapidocr" : "official");
      }
      return this.runtime?.ocrMode || PL.config?.ocrMode || "official";
    },
    backend() {
      if (PL.config?.unified) return this.localOCR ? this.runtime?.backend || "local" : "local";
      if (PL.config?.cloudPreview) return "cloud";
      if (this.ocrMode() === "rapidocr") return "local";
      return this.runtime?.pinned ? this.runtime.backend : PL.pref("backend", this.runtime?.backend || PL.config.defaultBackend || "local");
    },
    async heartbeat() {
      if (this.instance && PL.alive) {
        const path = this.leasePath;
        const instance = this.instance;
        this.heartbeatWrite = (this.heartbeatWrite || Promise.resolve()).catch(error => Zotero.logError(error)).then(() => {
          if (!this.releasing && this.instance === instance && PL.alive) {
            return IOUtils.writeJSON(path, { instance, time: Date.now() / 1000 }, { tmpPath: path + ".tmp" });
          }
        });
        await this.heartbeatWrite;
      }
    },
    async start(generation = this.generation) {
      this.update("checking", "检查本地服务…");
      try {
        if (!this.runtime) { try { await this.project(); } catch (_) { /* A configured external API remains usable. */ } }
        const configured = this.configuredURL();
        if (await this.online(configured)) {
          this.checkStart(generation);
          this.readyURL = configured; this.lastReadyAt = Date.now();
          this.update("ready", "服务已就绪"); return;
        }
        if (this.process) {
          await this.releaseLease();
          await this.process.wait();
          this.process = null; this.instance = null; this.owned = false;
        }
        const root = await this.project();
        const python = await this.python(root);
        this.checkStart(generation);
        this.instance = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
        this.releasing = false;
        // Separate leases prevent a delayed write/removal from an old plugin
        // instance taking ownership away from a newly installed version.
        await IOUtils.remove(PathUtils.join(PL.Store.directory, "service-lease.json"), { ignoreAbsent: true });
        this.leasePath = PathUtils.join(PL.Store.directory, `service-lease-${this.instance}.json`);
        this.statePath = PathUtils.join(PL.Store.directory, `service-state-${this.instance}.json`);
        await this.heartbeat();
        this.checkStart(generation);
        clearInterval(this.heartbeatTimer);
        this.heartbeatTimer = setInterval(() => this.heartbeat().catch(error => Zotero.logError(error)), 10000);
        const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
        this.owned = false;
        const arguments_ = ["-X", "utf8", PathUtils.join(root, "scripts", "zotero_service.py"), "--state-file", this.statePath,
          "--lease-file", this.leasePath, "--instance", this.instance, "--port", new URL(configured).port || "80",
          "--backend", this.backend(), "--parent-pid", String(Services.appinfo.processID)];
        if (Zotero.isMac) arguments_.unshift("-s");
        if (this.backend() === "cloud" || this.ocrMode() === "layout") arguments_.push("--cloud-token-file", this.cloudTokenPath());
        if (PL.config?.unified && !this.localOCR) arguments_.push("--mineru-config", this.mineruConfigPath());
        if (this.ocrMode() !== "official") arguments_.push("--ocr-mode", this.ocrMode());
        if (this.runtime?.descriptor) arguments_.push("--runtime-config",this.runtime.descriptor);
        if (this.runtime?.workspace) arguments_.push("--workspace", this.runtime.workspace);
        this.process = await Subprocess.call({ command: python, arguments: arguments_, workdir: root, stderr: "stdout" });
        const process = this.process;
        // Subprocess starts without a console window; drain the pipe throughout
        // model loading so a large startup log cannot block the child process.
        (async () => { try { while (await process.stdout.readString()) {} } catch (_) {} })();
        let exited = false;
        process.wait().then(() => {
          exited = true;
          if (PL.alive && this.process === process && this.state.phase === "ready" && this.owned) {
            this.update("stopped", "本地引擎已退出，点击重新启动");
            this.releaseLease().catch(error => Zotero.logError(error));
            this.instance = null;
            this.process = null;
            this.owned = false;
          }
        });
        const deadline = Date.now() + 15 * 60 * 1000;
        while (PL.alive && Date.now() < deadline) {
          this.checkStart(generation);
          let status;
          try { status = await IOUtils.readJSON(this.statePath); } catch (_) {}
          this.checkStart(generation);
          if (status?.instance === this.instance) {
            if (status.phase !== "ready") this.update(status.phase, status.message);
            if (status.phase === "failed") throw new Error(status.message);
            const serviceURL = status.service_url ? PL.serviceURL(status.service_url) : configured;
            if (status.phase === "ready" && await this.online(serviceURL)) {
              this.checkStart(generation);
              // Only remember the fallback after the new service has proved ready.
              if (!PL.config?.unified) PL.setPref("serviceURL", serviceURL);
              this.owned = !!status.owned;
              if (!this.owned) { await this.releaseLease(); this.instance = null; this.process = null; }
              this.readyURL = serviceURL; this.lastReadyAt = Date.now();
              this.update("ready", status.message);
              return;
            }
          }
          if (exited) throw new Error("本地引擎未能启动，请检查 Python 路径和项目依赖");
          await Zotero.Promise.delay(750);
        }
        throw new Error(PL.alive ? "OCR 服务启动超时，请重试" : "启动已取消");
      }
      catch (error) {
        if (generation === this.generation) this.update("failed", error.message);
        await this.releaseLease();
        if (this.process) await this.process.wait();
        this.owned = false;
        this.instance = null;
        this.process = null;
        throw error;
      }
    },
    async releaseLease() {
      this.readyURL = null;
      this.releasing = true;
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
      await this.heartbeatWrite;
      if (this.leasePath) await IOUtils.remove(this.leasePath, { ignoreAbsent: true });
    },
    async stop(options = {}) {
      if (this.stopping) return this.stopping;
      if (!options.force && (PL.Tasks.active || PL.Tasks.queue.length)) throw new Error("有文献正在处理，请等待队列完成后停止服务");
      this.generation++;
      this.stopping = this.stopInternal(options).finally(() => { this.stopping = null; });
      return this.stopping;
    },
    async stopInternal({ disconnect = false } = {}) {
      const starting = this.starting;
      this.update("stopping", "正在停止服务并释放模型…");
      if (starting) {
        await this.releaseLease();
        await starting.catch(() => {});
      }
      if (!this.instance) {
        if (!disconnect && !starting && await this.online()) {
          this.update("ready", "当前服务由其他启动器管理");
          throw new Error("当前服务由其他启动器启动，插件不会停止它");
        }
        await this.releaseLease();
        this.update("stopped", "服务已断开");
        return;
      }
      await this.releaseLease();
      if (this.process) await this.process.wait();
      const finalState = await IOUtils.readJSON(this.statePath).catch(()=>null);
      if (finalState?.instance === this.instance && finalState.phase === "failed") {
        this.update("failed", finalState.message);
        throw new Error(finalState.message);
      }
      this.instance = null;
      this.process = null;
      this.owned = false;
      this.update("stopped", "服务已停止");
    },
    async shutdown() {
      this.shuttingDown = true;
      this.generation++;
      await this.releaseLease();
      if (this.starting) await this.starting.catch(() => {});
      if (this.process) await this.process.wait();
    },
  };
  PL.Service.localOCR = false;
  PL.LocalOCR = { ...PL.Service, localOCR:true, state:{phase:"stopped", message:"本地 OCR 未启动"} };
})();
