// PaperBridge 弹窗逻辑
const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const btnParse = $("btn-parse");
const btnFile = $("btn-file");
const pairBox = $("pair-box");
const progressBox = $("progress-box");
const progressFill = $("progress-fill");
const progressText = $("progress-text");

function send(msg) {
  return chrome.runtime.sendMessage(msg);
}

function setStatus(kind, text) {
  statusEl.className = "status status-" + kind;
  statusEl.textContent = text;
}

function setProgress(visible, text, pct) {
  progressBox.hidden = !visible;
  if (visible) {
    progressText.textContent = text || "";
    progressFill.style.width = (pct ?? 0) + "%";
  }
}

function stageToPct(stage) {
  const order = [
    "queued", "preflight", "rendering_pages", "layout_analysis",
    "recognizing", "normalizing", "rendering_html", "ready", "needs_review", "failed",
  ];
  const i = order.indexOf(stage);
  return i < 0 ? 5 : Math.min(95, Math.round((i / order.length) * 100));
}

function stageLabel(stage) {
  const map = {
    queued: "排队中",
    preflight: "预检 PDF",
    rendering_pages: "渲染页面",
    layout_analysis: "版面分析",
    recognizing: "识别内容",
    normalizing: "规范化",
    rendering_html: "生成阅读页",
    ready: "完成",
    needs_review: "完成（部分需核对）",
    failed: "失败",
  };
  return map[stage] || stage;
}

// ---------------- 初始化 ----------------

(async function init() {
  const res = await send({ type: "health" });
  if (res.ok && res.health.ok) {
    setStatus("ok", "本地服务运行中 ✓");
    btnParse.disabled = false;
    btnFile.disabled = false;
  } else {
    setStatus("err", "本地服务未启动。请先运行 `paperbridge serve`");
    btnParse.disabled = true;
    btnFile.disabled = true;
  }
  const cfg = await chrome.storage.local.get({ token: "" });
  if (!cfg.token) {
    pairBox.open = true;
  }
})();

// ---------------- 解析当前 PDF ----------------

btnParse.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    setStatus("err", "无法获取当前标签页");
    return;
  }
  setProgress(true, "提交任务…", 2);
  setStatus("unknown", "正在解析…");
  const res = await send({ type: "parse-url", url: tab.url });
  if (!res.ok) {
    setStatus("err", res.error || "解析失败");
    setProgress(false);
    return;
  }
  // 轮询由 service worker 完成；这里只展示最终结果
  setStatus("ok", "已打开阅读页");
  setProgress(false);
  window.close();
});

// ---------------- 选择本地 PDF ----------------

btnFile.addEventListener("click", async () => {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "application/pdf,.pdf";
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const bytes = await file.arrayBuffer();
    setProgress(true, "上传并解析…", 5);
    const res = await send({ type: "upload-bytes", bytes, filename: file.name });
    if (!res.ok) {
      setStatus("err", res.error || "解析失败");
      setProgress(false);
      return;
    }
    setStatus("ok", "已打开阅读页");
    setProgress(false);
    window.close();
  };
  input.click();
});

// ---------------- 配对 ----------------

$("btn-pair").addEventListener("click", async () => {
  const code = $("pair-code").value.trim();
  $("pair-msg").textContent = "";
  if (!/^\d{6}$/.test(code)) {
    $("pair-msg").textContent = "请输入 6 位数字配对码";
    return;
  }
  const res = await send({ type: "pair", code });
  if (res.ok) {
    $("pair-msg").style.color = "#065f46";
    $("pair-msg").textContent = "配对成功！";
    pairBox.open = false;
    btnParse.disabled = false;
    btnFile.disabled = false;
  } else {
    $("pair-msg").textContent = res.error || "配对失败";
  }
});

$("btn-options").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
