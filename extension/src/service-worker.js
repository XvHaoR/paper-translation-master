// PaperBridge 扩展后台服务：健康检查、配对、任务提交与轮询、打开阅读页
// 遵循最小权限：默认只有 activeTab/storage/contextMenus + 本地服务权限

const DEFAULT_SERVICE_URL = "http://127.0.0.1:8765";
const PROTOCOL_VERSION = "1";

// ---------------- 工具 ----------------

async function getConfig() {
  const cfg = await chrome.storage.local.get({
    serviceUrl: DEFAULT_SERVICE_URL,
    token: "",
    autoDetect: false,
  });
  return cfg;
}

async function api(path, options = {}) {
  const cfg = await getConfig();
  const headers = { ...(options.headers || {}) };
  if (cfg.token) headers["X-PaperBridge-Token"] = cfg.token;
  const resp = await fetch(cfg.serviceUrl + path, { ...options, headers });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {
      /* ignore */
    }
    throw new Error(`本地服务返回 ${resp.status}：${detail}`);
  }
  return resp.json();
}

function setBadge(text, color = "#2563eb") {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
  void chrome.action.setBadgeTextColor({ color: "#ffffff" });
}

// ---------------- 服务健康 ----------------

async function checkHealth() {
  try {
    const resp = await fetch(`${DEFAULT_SERVICE_URL}/api/browser/health`);
    if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
    const data = await resp.json();
    return { ok: data.ok && data.protocol_version === PROTOCOL_VERSION, data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// ---------------- 任务提交 ----------------

async function submitUrl(url) {
  const body = await api("/api/browser/import-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return body.job_id;
}

async function uploadPdf(bytes, filename = "paper.pdf") {
  const form = new FormData();
  form.append("upload", new Blob([bytes], { type: "application/pdf" }), filename);
  const body = await api("/api/browser/upload", { method: "POST", body: form });
  return body.job_id;
}

async function pollJob(jobId, onProgress, timeoutMs = 30 * 60 * 1000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const job = await api(`/api/browser/jobs/${jobId}`);
    onProgress(job);
    if (["ready", "needs_review", "failed"].includes(job.state)) return job;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error("任务超时");
}

async function openReadingPage(documentId) {
  const cfg = await getConfig();
  const url = `${cfg.serviceUrl}/documents/${documentId}/`;
  // 通过服务端确认文档存在后打开新标签
  await chrome.tabs.create({ url, active: true });
}

// ---------------- 上下文菜单 ----------------

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "paperbridge-parse",
    title: "用论文翻译大师解析此 PDF",
    contexts: ["page", "link"],
  });
  chrome.contextMenus.create({
    id: "paperbridge-parse-link",
    title: "用论文翻译大师解析此链接的 PDF",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    const target = info.linkUrl || (tab && tab.url);
    if (!target) throw new Error("无法确定当前 PDF 地址");
    await startParseFromUrl(target);
  } catch (e) {
    setBadge("!");
    await chrome.notifications?.create({
      type: "basic",
      iconUrl: "icons/icon48.png",
      title: "论文翻译大师",
      message: `解析失败：${e.message}`,
    });
  }
});

// ---------------- 主流程：从 URL 开始解析 ----------------

async function startParseFromUrl(url) {
  const health = await checkHealth();
  if (!health.ok) {
    throw new Error("本地论文翻译大师服务未启动，请先运行 `paperbridge serve`");
  }
  const cfg = await getConfig();
  if (!cfg.token) {
    setBadge("配对");
    await chrome.tabs.create({ url: "options.html", active: true });
    throw new Error("尚未与本地服务配对，请在扩展设置页完成配对");
  }
  setBadge("…");
  let jobId;
  try {
    jobId = await submitUrl(url);
  } catch (e) {
    // URL 下载失败（登录态/跨域）：回退为下载并选择文件
    setBadge("!");
    await chrome.notifications?.create({
      type: "basic",
      iconUrl: "icons/icon48.png",
      title: "论文翻译大师",
      message: "无法直接读取该 PDF（可能需要登录）。请改用“选择本地 PDF”或扩展导入页。",
    });
    throw e;
  }
  const job = await pollJob(jobId, () => {});
  if (job.state === "failed") {
    throw new Error((job.errors || []).join("；") || "解析任务失败");
  }
  await openReadingPage(job.document_id);
  setBadge(job.state === "needs_review" ? "⚠" : "");
  return job;
}

// ---------------- 消息路由（popup / import 页） ----------------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case "health":
        return { ok: true, health: await checkHealth() };
      case "parse-url":
        return { ok: true, job: await startParseFromUrl(msg.url) };
      case "upload-bytes":
        return {
          ok: true,
          job: await pollJob(await uploadPdf(msg.bytes, msg.filename), () => {}),
        };
      case "pair":
        return { ok: true, token: await pairWithCode(msg.code) };
      case "open-options":
        await chrome.runtime.openOptionsPage();
        return { ok: true };
      default:
        return { ok: false, error: `未知消息：${msg.type}` };
    }
  })()
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: e.message }));
  return true; // 异步响应
});

async function pairWithCode(code) {
  const resp = await fetch(`${DEFAULT_SERVICE_URL}/api/browser/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pair_code: String(code).trim() }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || "配对失败");
  }
  const data = await resp.json();
  await chrome.storage.local.set({ token: data.token });
  return data.token;
}

// ---------------- 可选：自动检测 PDF 标签页（webRequest 只读） ----------------

if (chrome.webRequest?.onHeadersReceived) {
  chrome.webRequest.onHeadersReceived.addListener(
    (details) => {
      if (details.type !== "main_frame") return;
      const isPdf = (details.responseHeaders || []).some(
        (h) => h.name.toLowerCase() === "content-type" && h.value.includes("pdf"),
      );
      if (isPdf) {
        chrome.storage.local.set({ lastPdfTab: { url: details.url, time: Date.now() } });
      }
    },
    { urls: ["http://*/*", "https://*/*"] },
    ["responseHeaders"],
  );
}
