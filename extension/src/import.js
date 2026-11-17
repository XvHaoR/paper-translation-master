// PaperBridge 导入页：读取当前标签页 PDF（登录态场景）或选择本地文件并上传
const $ = (id) => document.getElementById(id);
const msg = $("msg");

async function currentTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && tab.url ? tab.url : "";
}

function showMsg(text, ok) {
  msg.textContent = text;
  msg.style.color = ok ? "#065f46" : "#991b1b";
}

function setProgress(visible, text, pct) {
  $("progress-box").hidden = !visible;
  if (visible) {
    $("progress-text").textContent = text || "";
    $("progress-fill").style.width = (pct ?? 0) + "%";
  }
}

async function ensureOriginPermission(url) {
  // 按需申请当前站点的访问权限（不安装时索取 <all_urls>）
  try {
    const origin = new URL(url).origin + "/*";
    const granted = await chrome.permissions.request({
      origins: [origin],
    });
    return granted;
  } catch (_) {
    return false;
  }
}

async function uploadAndParse(bytes, filename) {
  setProgress(true, "上传并解析…", 5);
  const res = await chrome.runtime.sendMessage({
    type: "upload-bytes",
    bytes,
    filename,
  });
  if (!res.ok) {
    showMsg(res.error || "解析失败", false);
    setProgress(false);
    return;
  }
  showMsg("已打开阅读页", true);
  setProgress(false);
  window.close();
}

$("btn-fetch").addEventListener("click", async () => {
  const url = await currentTabUrl();
  if (!url) return showMsg("无法获取当前标签页", false);
  $("current-url").textContent = url;
  setProgress(true, "申请访问权限…", 1);
  const granted = await ensureOriginPermission(url);
  if (!granted) {
    setProgress(false);
    return showMsg("未获得该网站的访问权限，请改用“选择本地 PDF”", false);
  }
  setProgress(true, "读取 PDF 字节…", 3);
  try {
    const resp = await fetch(url, { credentials: "include" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const ct = resp.headers.get("Content-Type") || "";
    const isPdf = ct.includes("pdf") || url.toLowerCase().endsWith(".pdf");
    if (!isPdf) throw new Error("响应不是 PDF（可能需要先让浏览器加载 PDF 视图）");
    const bytes = await resp.arrayBuffer();
    await uploadAndParse(bytes, url.split("/").pop() || "paper.pdf");
  } catch (e) {
    setProgress(false);
    showMsg(`读取失败：${e.message}。请改用“选择本地 PDF”。`, false);
  }
});

$("btn-file").addEventListener("click", () => {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "application/pdf,.pdf";
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const bytes = await file.arrayBuffer();
    await uploadAndParse(bytes, file.name);
  };
  input.click();
});

(async () => {
  $("current-url").textContent = (await currentTabUrl()) || "（无法获取）";
})();
