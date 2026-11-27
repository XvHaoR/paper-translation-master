"use strict";
window.addEventListener("DOMContentLoaded", async () => {
  const PL = window.arguments[0].wrappedJSObject.controller;
  window.document.title = `${PL.name} ${PL.version || ""} 设置`;
  const $ = id => window.document.getElementById(id);
  const status = (text, tone = "info") => {
    $("status").textContent = text;
    $("status").dataset.tone = tone;
  };
  $("version").textContent = PL.version || "";
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const saveButtons = [$("saveModel"), $("saveLocal"), $("saveAppearance")];
  function selectTab(index, focus = false) {
    for (const [i, tab] of tabs.entries()) {
      const active = i === index;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      $(tab.getAttribute("aria-controls")).hidden = !active;
      saveButtons[i].hidden = !active;
    }
    document.querySelector(".dialog-body").scrollTop = 0;
    status("");
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(index));
    tab.addEventListener("keydown", event => {
      const target = {ArrowLeft: (index + tabs.length - 1) % tabs.length,
        ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
      if (target === undefined) return;
      event.preventDefault();
      selectTab(target, true);
    });
  });
  const unified = PL.config.unified;
  const local = () => unified ? ({
    recognitionMode:$("recognitionMode").value,
    [PL.Recognition.autoPref($("recognitionMode").value)]:$("autoOCR").checked,
    language:$("language").value.trim(), unifiedLocalEngine:$("localEngine").value,
    unifiedLocalRoot:$("projectRoot").value.trim(), unifiedLocalPython:$("pythonPath").value.trim(),
    unifiedLocalBackend:$("backend").value, unifiedLocalURL:$("serviceURL").value.trim(),
  }) : ({
    ...($("backend").value === "cloud" ? {cloudAutoOCR:$("autoOCR").checked} : {autoOCR:$("autoOCR").checked}), language: $("language").value.trim(), backend: $("backend").value,
    projectRoot: $("projectRoot").value.trim(), pythonPath: $("pythonPath").value.trim(), serviceURL: $("serviceURL").value.trim(),
  });
  const action = (id, fn) => $(id).addEventListener("click", async () => {
    $(id).disabled = true;
    try { await fn(); } catch (error) { status(error.message, "error"); }
    finally { if (!window.closed) $(id).disabled = false; }
  });
  $("autoOCR").checked = PL.pref("autoOCR", false);
  for (const [id, fallback] of Object.entries({ language: "zh", backend: PL.config.defaultBackend,
    projectRoot: PL.config.projectRoot, pythonPath: "", serviceURL: "http://127.0.0.1:8765" })) {
    $(id).value = PL.pref(id, fallback);
  }
  const detectEngine = async () => {
    if (unified) {
      const runtime = await PL.Runtime.localOCR($("localEngine").value);
      const engine = runtime.backend === "wsl" ? "WSL FastDeploy" : runtime.ocrMode === "rapidocr" ? "RapidOCR CPU" : "PaddleOCR GPU";
      $("engineStatus").textContent = `已发现 ${engine} 环境。默认识别方案选「本地 OCR」后，保存设置并从 Zotero 工具栏启动。`;
      return;
    }
    const runtime = await PL.Runtime.resolve();
    if (!runtime) { $("engineStatus").textContent = "尚未发现本地引擎。运行安装器后，再点击检测即可。"; return; }
    $("projectRoot").value = runtime.projectRoot;
    $("pythonPath").value = runtime.pythonPath || "";
    $("backend").value = runtime.backend;
    $("engineStatus").textContent = `已发现引擎：${runtime.projectRoot}。点击「保存设置」即可使用。`;
  };
  action("downloadEngine", () => PL.Runtime.download());
  action("detectEngine", detectEngine);
  if (!unified) try { await detectEngine(); } catch (error) { $("engineStatus").textContent=error.message; }
  const updateAuto = () => {
    if (unified) {
      const mode = $("recognitionMode").value;
      $("autoOCRLabel").textContent = mode === "cloud" ? "新文献添加后自动上传 PDF 至飞桨进行 OCR"
        : `新文献添加后自动处理（${PL.Recognition.label(mode)}）`;
      $("autoOCR").checked = PL.pref(PL.Recognition.autoPref(mode), false);
      return;
    }
    const cloud = $("backend").value === "cloud";
    $("autoOCRLabel").textContent = cloud ? "新文献添加后自动上传 PDF 至飞桨进行 OCR" : "新文献添加后自动 OCR";
    $("autoOCR").checked = PL.pref(cloud ? "cloudAutoOCR" : "autoOCR", false);
  };
  $("backend").addEventListener("change", updateAuto);
  $("recognitionRow").style.display = unified ? "" : "none";
  $("localEngineRow").style.display = unified ? "" : "none";
  if (unified) {
    $("recognitionMode").value = PL.Recognition.defaultMode();
    $("localEngine").value = PL.pref("unifiedLocalEngine", "auto");
    $("projectRoot").value = PL.pref("unifiedLocalRoot", "");
    $("pythonPath").value = PL.pref("unifiedLocalPython", "");
    $("serviceURL").value = PL.pref("unifiedLocalURL", "http://127.0.0.1:8765");
    $("backend").value = PL.pref("unifiedLocalBackend", "local");
    for (const option of $("backend").options) if (option.value === "cloud") option.hidden = true;
    $("localEngineHint").textContent = "直接解析和云端 OCR 使用融合版运行环境。本地 OCR 可连接已有 GPU／CPU 安装，也可指定自定义环境。";
    const toggleCustom = () => {
      for (const id of ["localBackendRow","localRootRow","localPythonRow"]) $(id).style.display = $("localEngine").value === "custom" ? "" : "none";
    };
    toggleCustom();
    $("localEngine").addEventListener("change", toggleCustom);
    $("recognitionMode").addEventListener("change", updateAuto);
    $("recognitionMode").addEventListener("change", () => {
      const section = {local: "localOptions", cloud: "cloudOptions"}[$("recognitionMode").value];
      if (section) $(section).open = true;
    });
    detectEngine().catch(error => { $("engineStatus").textContent = error.message; });
  }
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
    await PL.Appearance.save(values); status("阅读外观已应用到打开的论文。", "success");
  });
  action("resetAppearance", async () => {
    await PL.Appearance.save(PL.Appearance.defaults); readAppearance(PL.Appearance.read()); status("已恢复系统字体和主题颜色。", "success");
  });
  action("chooseRoot", async () => {
    const { FilePicker } = ChromeUtils.importESModule("chrome://zotero/content/modules/filePicker.mjs");
    const picker = new FilePicker();
    picker.init(window, "选择论文翻译大师引擎目录", picker.modeGetFolder);
    if ((await picker.show()) !== picker.returnCancel) $("projectRoot").value = picker.file;
  });
  action("saveLocal", async () => { await PL.UI.applyLocal(local()); await saveCloud(); status("文献处理设置已保存。", "success"); });
  action("close", () => window.close());
  const apply = async () => {
    if (!unified) { await PL.UI.applyLocal(local()); await saveCloud(); }
    status("正在连接本地服务…");
    await PL.Service.ensure();
  };
  const read = config => {
    $("llm_base_url").value = config.llm_base_url || "";
    $("llm_model").value = config.llm_model || "";
    $("llm_temperature").value = config.llm_temperature ?? "";
    $("llm_api_key").placeholder = config.llm_api_key ? "已保存密钥；留空保持不变" : "无密钥本地模型可留空";
  };
  let modelEdited = false;
  $("panel-model").addEventListener("input", () => { modelEdited = true; });
  action("loadModel", async () => { await apply(); read(await PL.request("GET", "/api/settings")); status("已读取翻译配置。", "success"); });
  const modelConfig = () => ({ llm_base_url: $("llm_base_url").value.trim(), llm_model: $("llm_model").value.trim(),
    llm_temperature: $("llm_temperature").value.trim(),
    lang_to: $("language").value.trim(), ...($("llm_api_key").value || $("clearKey").checked ? { llm_api_key: $("llm_api_key").value.trim() } : {}) });
  action("saveModel", async () => {
    await apply();
    read(await PL.request("PUT", "/api/settings", modelConfig()));
    $("llm_api_key").value = "";
    $("clearKey").checked = false;
    status("翻译配置已保存。", "success");
  });
  action("testModel", async () => {
    await apply();
    await PL.request("POST", "/api/settings/test", modelConfig());
    status("翻译服务连接成功。", "success");
  });
  try {
    if (unified) {
      const saved = await PL.Runtime.translationSettings();
      if (saved && !modelEdited) read(saved);
    }
    if (await PL.Service.online()) {
      const saved = await PL.request("GET", "/api/settings");
      if (!modelEdited) read(saved);
    }
  } catch (_) { if (!modelEdited) status("可点击「重新读取配置」重试。"); }
});
