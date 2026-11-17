"use strict";
window.addEventListener("DOMContentLoaded", async () => {
  const PL = window.arguments[0].wrappedJSObject.controller;
  window.document.title = `${PL.name} ${PL.version || ""} 设置`;
  const $ = id => window.document.getElementById(id);
  const status = text => { $("status").textContent = text; };
  const modelFields = ["llm_base_url", "llm_model", "llm_temperature", "llm_api_key", "clearKey", "loadModel", "saveModel", "testModel", "saveLocal"];
  for (const id of modelFields) $(id).disabled = true;
  const local = () => ({
    autoParseNewItems: $("autoOCR").checked, language: $("language").value.trim(), backend: "local",
    projectRoot: $("projectRoot").value.trim(), pythonPath: $("pythonPath").value.trim(), serviceURL: $("serviceURL").value.trim(),
  });
  const action = (id, fn) => $(id).addEventListener("click", async () => {
    $(id).disabled = true;
    try { await fn(); } catch (error) { status(error.message); }
    finally { if (!window.closed) $(id).disabled = false; }
  });
  $("autoOCR").checked = PL.pref("autoParseNewItems", false);
  for (const [id, fallback] of Object.entries({ language: "zh",
    projectRoot: PL.config.projectRoot, pythonPath: "", serviceURL: "http://127.0.0.1:8765" })) {
    $(id).value = PL.pref(id, fallback);
  }
  const detectEngine = async () => {
    const runtime = await PL.Runtime.resolve();
    if (!runtime) { $("engineStatus").textContent = "尚未发现本地引擎。运行安装器后，再点击检测即可。"; return; }
    $("projectRoot").value = runtime.projectRoot;
    $("pythonPath").value = runtime.pythonPath || "";
    $("engineStatus").textContent = `已发现引擎：${runtime.projectRoot}。点击「保存设置」即可使用。`;
  };
  action("detectEngine", detectEngine);
  try { await detectEngine(); } catch (error) { $("engineStatus").textContent=error.message; }
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
  action("close", () => window.close());
  const apply = async () => { await PL.UI.applyLocal(local()); status("正在启动服务…"); await PL.Service.ensure(); };
  const read = config => {
    $("llm_base_url").value = config.llm_base_url || "";
    $("llm_model").value = config.llm_model || "";
    $("llm_temperature").value = config.llm_temperature ?? 0.2;
    $("llm_api_key").value = config.llm_api_key || "";
    $("llm_api_key").placeholder = config.llm_api_key ? "已保存密钥；留空保持不变" : "无密钥本地模型可留空";
  };
  action("loadModel", async () => { await apply(); read(await PL.request("GET", "/api/settings")); status("已读取翻译配置。"); });
  const modelConfig = () => {
    const raw = $("llm_temperature").value.trim(), temperature = Number(raw);
    if (!raw || !Number.isFinite(temperature) || temperature < 0 || temperature > 2) {
      $("llm_temperature").focus();
      throw new Error("温度（Temperature）必须是 0 到 2 之间的数字");
    }
    return { llm_base_url: $("llm_base_url").value.trim(), llm_model: $("llm_model").value.trim(),
      llm_temperature: temperature, lang_to: $("language").value.trim(),
      ...($("clearKey").checked ? {llm_api_key: ""}
        : ($("llm_api_key").value.trim() ? {llm_api_key: $("llm_api_key").value.trim()} : {})) };
  };
  const saveTranslation = async () => {
    const config = modelConfig();
    await apply();
    const saved = await PL.request("PUT", "/api/settings", config);
    if (saved.llm_temperature === undefined) throw new Error("当前服务尚未加载温度设置，请停止并重新启动 解析服务后再保存。");
    read(saved);
    $("clearKey").checked = false;
    status("设置已保存，再次打开会自动回填。不会自动翻译任何文献。");
  };
  action("saveModel", saveTranslation);
  action("saveLocal", saveTranslation);
  action("testModel", async () => {
    const config = modelConfig();
    await apply();
    await PL.request("POST", "/api/settings/test", config);
    status("翻译服务连接成功。");
  });
  try {
    const saved = await PL.Runtime.translationSettings();
    if (saved) read(saved);
    if (await PL.Service.online()) read(await PL.request("GET", "/api/settings"));
  } catch (_) { status("未能完整读取翻译配置，可点击「启动并读取翻译配置」重试。"); }
  finally { for (const id of modelFields) $(id).disabled = false; }
});
