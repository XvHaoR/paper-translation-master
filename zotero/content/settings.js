"use strict";
window.addEventListener("DOMContentLoaded", async () => {
  const PL = window.arguments[0].wrappedJSObject.controller;
  window.document.title = `${PL.name} ${PL.version || ""} 设置`;
  const $ = id => window.document.getElementById(id);
  const status = text => { $("status").textContent = text; };
  const local = () => ({
    ...($("backend").value === "cloud" ? {cloudAutoOCR:$("autoOCR").checked} : {autoOCR:$("autoOCR").checked}), language: $("language").value.trim(), backend: $("backend").value,
    projectRoot: $("projectRoot").value.trim(), pythonPath: $("pythonPath").value.trim(), serviceURL: $("serviceURL").value.trim(),
  });
  const action = (id, fn) => $(id).addEventListener("click", async () => {
    $(id).disabled = true;
    try { await fn(); } catch (error) { status(error.message); }
    finally { if (!window.closed) $(id).disabled = false; }
  });
  $("autoOCR").checked = PL.pref("autoOCR", false);
  for (const [id, fallback] of Object.entries({ language: "zh", backend: PL.config.defaultBackend,
    projectRoot: PL.config.projectRoot, pythonPath: "", serviceURL: "http://127.0.0.1:8765" })) {
    $(id).value = PL.pref(id, fallback);
  }
  const detectEngine = async () => {
    const runtime = await PL.Runtime.resolve();
    if (!runtime) { $("engineStatus").textContent = "尚未发现本地引擎。运行安装器后，再点击检测即可。"; return; }
    $("projectRoot").value = runtime.projectRoot;
    $("pythonPath").value = runtime.pythonPath || "";
    $("backend").value = runtime.backend;
    $("engineStatus").textContent = `已发现引擎：${runtime.projectRoot}。点击「保存设置」即可使用。`;
  };
  action("downloadEngine", () => PL.Runtime.download());
  action("detectEngine", detectEngine);
  try { await detectEngine(); } catch (error) { $("engineStatus").textContent=error.message; }
  const updateAuto = () => {
    const cloud = $("backend").value === "cloud";
    $("autoOCRLabel").textContent = cloud ? "新文献添加后自动上传 PDF 至飞桨进行 OCR" : "新文献添加后自动 OCR";
    $("autoOCR").checked = PL.pref(cloud ? "cloudAutoOCR" : "autoOCR", false);
  };
  $("backend").addEventListener("change", updateAuto);
  if (PL.config.cloudPreview) {
    $("backend").value = "cloud"; $("backend").disabled = true;
    $("localEngineHint").textContent = "此版本使用内置轻量运行环境。解压测试包并运行 Install-Cloud.cmd 后，填写下方飞桨密钥即可。";
    for (const id of ["projectRoot","pythonPath","chooseRoot","downloadEngine"]) $(id).disabled = true;
  }
  updateAuto();
  const saveCloud = async () => {
    const path = PL.Service.cloudTokenPath(), token = $("cloudToken").value.trim();
    if ($("clearCloudToken").checked) await IOUtils.remove(path, {ignoreAbsent:true});
    else if (token) {
      if (/\s/.test(token) || token.length > 4096) throw new Error("Access Token 格式无效");
      await IOUtils.writeUTF8(path, token, {tmpPath:path + ".tmp"});
    }
    $("cloudToken").value = ""; $("clearCloudToken").checked = false;
    $("cloudToken").placeholder = await IOUtils.exists(path) ? "已保存密钥；留空保持不变" : "填写 AI Studio Access Token";
  };
  $("cloudToken").placeholder = await IOUtils.exists(PL.Service.cloudTokenPath()) ? "已保存密钥；留空保持不变" : "填写 AI Studio Access Token";
  const readAppearance = values => {
    for (const kind of ["ocr", "translation"]) {
      $(kind + "FontFamily").value = values[kind + "FontFamily"] || "";
      $(kind + "TextColor").value = values[kind + "TextColor"] || "#222222";
      $(kind + "ColorAuto").checked = !values[kind + "TextColor"];
      $(kind + "TextColor").disabled = $(kind + "ColorAuto").checked;
    }
  };
  readAppearance(PL.Appearance.read());
  for (const kind of ["ocr", "translation"]) $(kind + "ColorAuto").addEventListener("change", () => {
    $(kind + "TextColor").disabled = $(kind + "ColorAuto").checked;
  });
  action("saveAppearance", async () => {
    const values = {};
    for (const kind of ["ocr", "translation"]) {
      values[kind + "FontFamily"] = $(kind + "FontFamily").value;
      values[kind + "TextColor"] = $(kind + "ColorAuto").checked ? "" : $(kind + "TextColor").value;
    }
    await PL.Appearance.save(values); status("阅读外观已保存并立即生效。");
  });
  action("resetAppearance", async () => {
    await PL.Appearance.save(PL.Appearance.defaults); readAppearance(PL.Appearance.read()); status("已恢复系统字体与自动文字颜色。");
  });
  action("chooseRoot", async () => {
    const { FilePicker } = ChromeUtils.importESModule("chrome://zotero/content/modules/filePicker.mjs");
    const picker = new FilePicker();
    picker.init(window, "选择论文翻译大师引擎目录", picker.modeGetFolder);
    if ((await picker.show()) !== picker.returnCancel) $("projectRoot").value = picker.file;
  });
  action("saveLocal", async () => { await PL.UI.applyLocal(local()); await saveCloud(); status("已保存。自动 OCR 只作用于之后新添加的文献。"); });
  action("close", () => window.close());
  const apply = async () => { await PL.UI.applyLocal(local()); await saveCloud(); status("正在启动服务…"); await PL.Service.ensure(); };
  const read = config => {
    $("llm_base_url").value = config.llm_base_url || "";
    $("llm_model").value = config.llm_model || "";
    $("llm_api_key").placeholder = config.llm_api_key ? "已保存密钥；留空保持不变" : "无密钥本地模型可留空";
  };
  action("loadModel", async () => { await apply(); read(await PL.request("GET", "/api/settings")); status("已读取翻译配置。"); });
  const modelConfig = () => ({ llm_base_url: $("llm_base_url").value.trim(), llm_model: $("llm_model").value.trim(),
    lang_to: $("language").value.trim(), ...($("llm_api_key").value || $("clearKey").checked ? { llm_api_key: $("llm_api_key").value.trim() } : {}) });
  action("saveModel", async () => {
    await apply();
    read(await PL.request("PUT", "/api/settings", modelConfig()));
    $("llm_api_key").value = "";
    status("翻译配置已保存。不会自动翻译任何文献。");
  });
  action("testModel", async () => {
    await apply();
    await PL.request("POST", "/api/settings/test", modelConfig());
    status("翻译服务连接成功。");
  });
  if (await PL.Service.online()) {
    try { read(await PL.request("GET", "/api/settings")); } catch (_) {}
  }
});
