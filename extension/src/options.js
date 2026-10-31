// PaperBridge 设置页逻辑
const $ = (id) => document.getElementById(id);

async function refresh() {
  const cfg = await chrome.storage.local.get({
    serviceUrl: "http://127.0.0.1:8765",
    token: "",
    autoDetect: false,
  });
  $("service-url").value = cfg.serviceUrl;
  $("auto-detect").checked = !!cfg.autoDetect;
  $("pair-state").textContent = cfg.token
    ? "已配对（令牌已保存于 chrome.storage.local）"
    : "未配对 —— 在 Web 界面获取配对码后点击插件弹窗完成配对";
}

$("save").addEventListener("click", async () => {
  const url = $("service-url").value.trim();
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) {
    $("msg").textContent = "服务地址必须是 http://127.0.0.1:端口";
    $("msg").className = "err";
    return;
  }
  const autoDetect = $("auto-detect").checked;
  if (autoDetect && !chrome.permissions.contains({ permissions: ["webRequest"] })) {
    try {
      await chrome.permissions.request({ permissions: ["webRequest"] });
    } catch (_) {
      /* 用户拒绝则保持关闭 */
    }
  }
  await chrome.storage.local.set({ serviceUrl: url, autoDetect });
  $("msg").textContent = "已保存";
  $("msg").className = "ok";
});

$("revoke").addEventListener("click", async () => {
  await chrome.storage.local.set({ token: "" });
  $("msg").textContent = "已解除配对";
  $("msg").className = "ok";
  refresh();
});

$("health").addEventListener("click", async () => {
  const res = await chrome.runtime.sendMessage({ type: "health" });
  $("msg").textContent = res.ok && res.health.ok
    ? `服务正常（协议 v${res.health.data?.protocol_version}）`
    : `服务不可用：${res.health.error || "未知错误"}`;
  $("msg").className = res.ok && res.health.ok ? "ok" : "err";
});

refresh();
