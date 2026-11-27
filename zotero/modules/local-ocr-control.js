"use strict";
(() => {
  const PL = PaperLoom;
  PL.LocalOCRControl = {
    reservations: new Set(),
    used: false,
    generation: 0,
    idleMinutes() {
      const minutes = PL.pref("localOCRIdleMinutes", 5);
      return Number.isInteger(minutes) && minutes >= 1 && minutes <= 120 ? minutes : 5;
    },
    clearTimer() {
      clearTimeout(this.timer);
      this.timer = null;
      this.deadline = null;
    },
    reserve(task) {
      this.reservations.add(task);
      this.clearTimer();
    },
    async begin(task) {
      this.reserve(task);
      if (this.closing) await this.closing;
      PL.Tasks.check(task);
      this.used = true;
      await (this.warming || PL.LocalOCR.ensure());
    },
    finish(task) {
      if (this.reservations.delete(task) && !this.reservations.size) this.schedule();
    },
    schedule() {
      this.clearTimer();
      const service = PL.LocalOCR;
      if (!PL.alive || this.closed || this.closing || this.warming || !this.used || this.reservations.size ||
          !service.owned || service.state.phase !== "ready") return;
      const delay = this.idleMinutes() * 60000;
      this.deadline = Date.now() + delay;
      const timer = this.timer = setTimeout(() => {
        if (this.timer === timer && !this.reservations.size) this.stop().catch(error => Zotero.logError(error));
      }, delay);
      PL.UI.refreshService();
    },
    async prewarm(window) {
      if (this.closed) throw new Error("插件正在关闭");
      if (this.closing) { await this.closing; return this.prewarm(window); }
      if (this.warming) return this.warming;
      this.clearTimer();
      if (!this.reservations.size) this.used = false;
      const generation = this.generation;
      this.warming = this.warmModel(window, generation).catch(async error => {
        if (generation !== this.generation) return;
        if (PL.LocalOCR.owned) await PL.LocalOCR.stop({force:true, disconnect:true});
        PL.LocalOCR.update("failed", "本地 OCR 预热失败：" + error.message);
        throw error;
      }).finally(() => {
        this.warming = null;
        this.probeXHR = null;
        this.probeJob = null;
        this.schedule();
        PL.UI.refreshService();
      });
      PL.UI.refreshService();
      return this.warming;
    },
    checkWarm(generation) {
      if (this.closed || generation !== this.generation) throw new Error("预热已取消");
    },
    async warmModel(window, generation) {
      const service = PL.LocalOCR;
      await service.ensure();
      this.checkWarm(generation);
      const mode = service.ocrMode();
      const serviceURL = service.url();
      const options = {serviceURL, requestObserver: xhr => {
        this.probeXHR = xhr;
        if (this.generation !== generation) xhr.abort();
      }};
      try {
        await PL.request("POST", "/api/zotero/local-ocr/prewarm", {mode}, options);
      } catch (error) {
        this.checkWarm(generation);
        if (![404, 405].includes(error.status)) throw error;
        // Older engines only expose the regular OCR queue. A forced job warms
        // a newly started engine even when the same sample was tested before.
        const resource = await Zotero.File.getResourceAsync(PL.rootURI + "content/ocr-warmup.b64");
        const sample = Uint8Array.from(window.atob(resource), ch => ch.charCodeAt(0));
        this.checkWarm(generation);
        const form = new window.FormData();
        form.append("ocr_mode", mode);
        form.append("force", "true");
        form.append("upload", new window.Blob([sample], {type:"application/pdf"}), "ocr-warmup.pdf");
        const {job_id: id} = await PL.request("POST", "/api/jobs", undefined,
          {...options, body:form, headers:{"Content-Type":"multipart/form-data"}});
        this.probeJob = {id, serviceURL};
        const until = Date.now() + 15 * 60000;
        while (Date.now() < until) {
          this.checkWarm(generation);
          const job = await PL.request("GET", "/api/jobs/" + encodeURIComponent(id), undefined, options);
          if (["ready", "needs_review"].includes(job.state)) return;
          if (["failed", "cancelled"].includes(job.state)) throw new Error(job.errors?.join("；") || "测试页识别失败");
          await Zotero.Promise.delay(750);
        }
        throw new Error("测试页预热超时");
      }
    },
    async toggle(window) {
      const service = PL.LocalOCR;
      if (this.closing) return this.closing;
      if (!this.warming && !service.starting && !service.instance && service.state.phase !== "ready")
        return this.prewarm(window);
      if (this.reservations.size && !Services.prompt.confirm(window, PL.name,
        "立即关闭本地 OCR 并取消尚未完成的本地识别？已保存的结果会保留。")) return;
      return this.stop();
    },
    stop() {
      if (this.closing) return this.closing;
      this.clearTimer();
      this.used = false;
      this.generation++;
      const current = new Set(this.reservations);
      const warming = this.warming;
      this.probeXHR?.abort();
      const probe = this.probeJob;
      const pending = [];
      if (probe) pending.push(PL.request("POST", "/api/jobs/" + encodeURIComponent(probe.id) + "/cancel", undefined,
        {serviceURL:probe.serviceURL, timeout:3000}).catch(error => Zotero.logError(error)));
      if (current.size) pending.push(PL.Tasks.cancel(task => current.has(task), "本地 OCR 已手动停止，可以重试", true));
      if (warming) pending.push(warming.catch(() => {}));
      pending.push(PL.LocalOCR.stop({force:true, disconnect:true}));
      this.closing = Promise.allSettled(pending).then(results => {
        const failure = results.find(result => result.status === "rejected");
        if (failure) throw failure.reason;
      }).finally(() => {
        this.closing = null;
        PL.UI.refreshService();
      });
      PL.UI.refreshService();
      return this.closing;
    },
    status() {
      const service = PL.LocalOCR;
      const phase = service.state.phase;
      if (this.closing || phase === "stopping") return ["正在关闭本地 OCR", "正在释放本地 OCR 资源", true];
      if (phase === "ready" && !this.warming) {
        if (!service.owned) return ["断开本地 OCR", "已连接独立运行的服务，其模型由启动它的程序管理"];
        const idle = this.deadline && new Date(this.deadline).toLocaleTimeString();
        return ["关闭本地 OCR", idle ? "本地 OCR 空闲，将在 " + idle + " 释放" :
          this.reservations.size ? "正在处理本地识别任务" : "本地 OCR 已就绪，等待识别论文"];
      }
      if (this.warming || service.starting || service.instance && phase !== "failed")
        return ["关闭本地 OCR", phase === "ready" ? "正在预热识别模型…" : service.state.message];
      return ["预热本地 OCR", phase === "failed" ? service.state.message : "提前加载识别模型"];
    },
    shutdown() {
      this.closed = true;
      this.generation++;
      this.clearTimer();
      this.probeXHR?.abort();
    },
  };
})();
