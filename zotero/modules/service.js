"use strict";
(() => {
  const PL = PaperLoom;
  PL.Service = {
    state: { phase: "stopped", message: "服务未启动" },
    async online(url = PL.serviceURL()) {
      try {
        const response = await Zotero.HTTP.request("GET", PL.serviceURL(url) + "/api/zotero/status", { timeout: 1500, responseType: "json" });
        return response.response?.protocol === 2 && response.response?.export_schema >= Math.max(7, PL.config?.minimumExportSchema || 7)
          && (this.ocrMode() !== "rapidocr" || response.response?.ocr_modes?.includes("rapidocr"))
          && ["force-ocr", "cancel-ocr", "edited-source"].every(feature => response.response?.features?.includes(feature));
      }
      catch (_) { return false; }
    },
    update(phase, message) {
      this.state = { phase, message };
      PL.UI?.refreshService();
    },
    async ensure() {
      if (this.stopping) await this.stopping;
      if (this.starting) return this.starting;
      // The leased child is already watched by process.wait(). A transient
      // HTTP timeout between papers must not tear down its warm OCR model.
      if (this.state.phase === "ready" && this.readyURL === PL.serviceURL()) {
        if (this.owned && this.process && this.instance) return;
        if (Date.now() - (this.lastReadyAt || 0) < 15000) return;
        for (let attempt = 0; attempt < 3; attempt++) {
          if (await this.online()) { this.lastReadyAt = Date.now(); return; }
          if (attempt < 2) await Zotero.Promise.delay(300 * (attempt + 1));
        }
      }
      this.starting = this.start().finally(() => { this.starting = null; });
      return this.starting;
    },
    async project() {
      const runtime = PL.Runtime ? await PL.Runtime.resolve() : null;
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
    ocrMode() { return this.runtime?.ocrMode || PL.config?.ocrMode || "official"; },
    backend() {
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
    async start() {
      this.update("checking", "检查本地服务…");
      try {
        if (!this.runtime) { try { await this.project(); } catch (_) { /* A configured external API remains usable. */ } }
        if (await this.online()) {
          this.readyURL = PL.serviceURL(); this.lastReadyAt = Date.now();
          this.update("ready", "服务已就绪"); return;
        }
        if (this.process) {
          await this.releaseLease();
          await this.process.wait();
          this.process = null; this.instance = null; this.owned = false;
        }
        const root = await this.project();
        const python = await this.python(root);
        if (!PL.alive) throw new Error("启动已取消");
        this.instance = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
        this.releasing = false;
        // Separate leases prevent a delayed write/removal from an old plugin
        // instance taking ownership away from a newly installed version.
        await IOUtils.remove(PathUtils.join(PL.Store.directory, "service-lease.json"), { ignoreAbsent: true });
        this.leasePath = PathUtils.join(PL.Store.directory, `service-lease-${this.instance}.json`);
        this.statePath = PathUtils.join(PL.Store.directory, "service-state.json");
        await this.heartbeat();
        clearInterval(this.heartbeatTimer);
        this.heartbeatTimer = setInterval(() => this.heartbeat().catch(error => Zotero.logError(error)), 10000);
        const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
        this.owned = false;
        const arguments_ = ["-X", "utf8", PathUtils.join(root, "scripts", "zotero_service.py"), "--state-file", this.statePath,
          "--lease-file", this.leasePath, "--instance", this.instance, "--port", new URL(PL.serviceURL()).port || "80",
          "--backend", this.backend(), "--parent-pid", String(Services.appinfo.processID)];
        if (this.ocrMode() === "rapidocr") arguments_.push("--ocr-mode", "rapidocr");
        if (this.runtime?.descriptor) arguments_.push("--runtime-config",this.runtime.descriptor);
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
          }
        });
        const deadline = Date.now() + 15 * 60 * 1000;
        while (PL.alive && Date.now() < deadline) {
          let status;
          try { status = await IOUtils.readJSON(this.statePath); } catch (_) {}
          if (status?.instance === this.instance) {
            this.update(status.phase, status.message);
            if (status.phase === "failed") throw new Error(status.message);
            const serviceURL = status.service_url ? PL.serviceURL(status.service_url) : PL.serviceURL();
            if (status.phase === "ready" && await this.online(serviceURL)) {
              // Only remember the fallback after the new service has proved ready.
              PL.setPref("serviceURL", serviceURL);
              this.owned = !!status.owned;
              if (!this.owned) { await this.releaseLease(); this.instance = null; this.process = null; }
              this.readyURL = serviceURL; this.lastReadyAt = Date.now();
              return;
            }
          }
          if (exited) throw new Error("本地引擎未能启动，请检查 Python 路径和项目依赖");
          await Zotero.Promise.delay(750);
        }
        throw new Error(PL.alive ? "OCR 服务启动超时，请重试" : "启动已取消");
      }
      catch (error) {
        this.update("failed", error.message);
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
      this.stopping = this.stopInternal(options).finally(() => { this.stopping = null; });
      return this.stopping;
    },
    async stopInternal({ force = false } = {}) {
      if (!this.instance) {
        if (await this.online()) throw new Error("当前服务由其他启动器启动，插件不会停止它");
        this.update("stopped", "服务未启动");
        return;
      }
      if (!force && (PL.Tasks.active || PL.Tasks.queue.length)) throw new Error("有文献正在处理，请等待队列完成后停止服务");
      this.update("stopping", "正在停止服务并释放模型…");
      await this.releaseLease();
      if (this.process) await this.process.wait();
      this.instance = null;
      this.process = null;
      this.owned = false;
      this.update("stopped", "服务已停止");
    },
    async shutdown() {
      await this.releaseLease();
      if (this.process) await this.process.wait();
    },
  };
})();
