"use strict";
window.addEventListener("DOMContentLoaded", async () => {
  const PL = window.arguments[0].wrappedJSObject.controller;
  window.document.title = `${PL.name} ${PL.version || ""} 设置`;
  const $ = id => window.document.getElementById(id);
  const status = text => { $("status").textContent = text; };
  const unified = PL.config.unified;
  const local = () => unified ? ({
    recognitionMode:$("recognitionMode").value,
    [PL.Recognition.autoPref($("recognitionMode").value)]:$("autoOCR").checked,
    language:$("language").value.trim(), unifiedLocalEngine:$("localEngine").value,
    unifiedLocalRoot:$("projectRoot").value.trim(), unifiedLocalPython:$("pythonPath").value.trim(),
    unifiedLocalBackend:$("backend").value, unifiedLocalURL:$("serviceURL").value.trim(),
    localOCRIdleMinutes:Number($("localIdleMinutes").value),
  }) : ({
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
    if (unified) {
      const runtime = await PL.Runtime.localOCR($("localEngine").value);
      const engine = runtime.backend === "wsl" ? "WSL FastDeploy" : runtime.ocrMode === "rapidocr" ? "RapidOCR CPU"
        : PL.Runtime.isMac() ? "PaddleOCR" : "PaddleOCR GPU";
      $("engineStatus").textContent = `已发现 ${engine} 环境。默认选「本地 OCR」并保存后，可在 Zotero 工具栏预热或关闭模型。`;
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
      const destination = PL.Recognition.destination(mode);
      if (mode === "mineru" || mode === "mineru-local") $("mineruSettings").open = true;
      $("autoOCRLabel").textContent = destination ? `新文献添加后自动提交 PDF 至${destination}识别`
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
    PL.Recognition.populate($("recognitionMode"));
    $("localIdleSettings").hidden = false;
    $("localIdleMinutes").value = PL.LocalOCRControl.idleMinutes();
    $("localServiceHint").textContent = "选择「本地 OCR」作为默认方案并保存后，工具栏会显示模型开关。临时选择本地识别也会按需启动和释放。";
    $("recognitionMode").value = PL.Recognition.defaultMode();
    $("localEngine").value = PL.pref("unifiedLocalEngine", "auto");
    $("projectRoot").value = PL.pref("unifiedLocalRoot", "");
    $("pythonPath").value = PL.pref("unifiedLocalPython", "");
    $("serviceURL").value = PL.pref("unifiedLocalURL", "http://127.0.0.1:8765");
    $("backend").value = PL.pref("unifiedLocalBackend", "local");
    for (const option of $("backend").options) if (option.value === "cloud") option.hidden = true;
    $("localEngineHint").textContent = "直接解析和云端 OCR 使用融合版运行环境。本地 OCR 可连接已有 GPU／CPU 安装，也可指定自定义环境。";
    if (PL.Runtime.isMac()) {
        $("localEngineHint").textContent = "Mac 可直接解析 PDF、连接飞桨或 MinerU。做本地 OCR 时，请连接已部署的兼容引擎或本机服务。";
      for (const option of $("backend").options) if (option.value === "wsl") option.hidden = true;
      if ($("backend").value === "wsl") $("backend").value = "local";
      for (const option of $("localEngine").options) {
        if (option.value === "gpu") option.hidden = true;
        if (option.value === "auto") option.textContent = "自动检测";
      }
      if ($("localEngine").value === "gpu") $("localEngine").value = "auto";
      $("downloadEngine").hidden = true;
    }
    const toggleCustom = () => {
      for (const id of ["localBackendRow","localRootRow","localPythonRow"]) $(id).style.display = $("localEngine").value === "custom" ? "" : "none";
    };
    toggleCustom();
    $("localEngine").addEventListener("change", toggleCustom);
    $("recognitionMode").addEventListener("change", updateAuto);
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
  let saveMineru = async () => {};
  if (unified) {
    $("mineruSettings").hidden = false;
    action("getMineruToken", () => PL.Service.openMineruTokenPage());
    const path = PL.Service.mineruConfigPath();
    const readConfig = async () => {
      if (!await IOUtils.exists(path)) return {};
      const value = await IOUtils.readJSON(path);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("MinerU 配置格式无效");
      return value;
    };
    let saved;
    try { saved = await readConfig(); }
    catch (_) { status("MinerU 配置无法读取，请重新填写 MinerU 设置后保存。"); }

    const defaultURL = "http://127.0.0.1:8000";
    let initialURL = saved?.serverURL || defaultURL;
    $("mineruServerURL").value = initialURL;
    const tokens = [["mineruToken","clearMineruToken","cloudToken"], ["mineruServerToken","clearMineruServerToken","serverToken"]];
    const placeholders = config => {
      for (const [id,,key] of tokens) $(id).placeholder = config[key] ? "已保存，留空保留密钥" :
        key === "cloudToken" ? "填写 MinerU 官方 API Token" : "无鉴权服务可留空";
    };
    placeholders(saved || {});
    saveMineru = async () => {
      const inputURL = $("mineruServerURL").value.trim() || defaultURL;
      if (initialURL === inputURL && !tokens.some(([id,clear]) => $(id).value || $(clear).checked)) return;
      const config = saved ? await readConfig() : {};
      const url = new URL(inputURL);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
        throw new Error("MinerU 自部署地址须为 HTTP(S) 服务根地址");
      }
      const serverURL = url.href.replace(/\/+$/, "");
      if (config.serverURL && config.serverURL !== serverURL) delete config.serverToken;
      config.serverURL = serverURL;
      for (const [id, clear, key] of tokens) {
        const token = $(id).value.trim();
        if ($(clear).checked) delete config[key];
        else if (token) {
          if (/\s/.test(token) || token.length > 4096) throw new Error("MinerU 密钥格式无效");
          config[key] = token;
        }
      }
      await PL.Service.writeMineruConfig(config);
      saved = config;
      initialURL = serverURL;
      $("mineruServerURL").value = serverURL;
      for (const [id, clear] of tokens) { $(id).value = ""; $(clear).checked = false; }
      placeholders(config);
    };
  }
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
  const dirty = new Set();
  const modelFields = ["llm_base_url", "llm_model", "llm_temperature", "llm_concurrency", "llm_api_key", "clearKey"];
  for (const id of modelFields) {
    for (const event of ["input","change"]) $(id).addEventListener(event, () => dirty.add(id));
  }
  action("saveLocal", async () => {
    await PL.UI.applyLocal(local()); await saveMineru(); await saveCloud();
    if (dirty.size) await saveModel();
    status("设置已保存。");
  });
  action("close", () => window.close());
  const apply = async () => {
    if (!unified) { await PL.UI.applyLocal(local()); await saveCloud(); }
    status("正在启动服务…");
    await PL.Service.ensure();
  };
  const read = config => {
    if (!dirty.has("llm_base_url")) $("llm_base_url").value = config.llm_base_url || "";
    if (!dirty.has("llm_model")) $("llm_model").value = config.llm_model || "";
    if (!dirty.has("llm_temperature")) $("llm_temperature").value = config.llm_temperature ?? "";
    if (!dirty.has("llm_concurrency")) $("llm_concurrency").value = config.llm_concurrency || 3;
    $("llm_api_key").placeholder = config.llm_api_key ? "已保存密钥；留空保持不变" : "无密钥本地模型可留空";
  };
  action("loadModel", async () => { await apply(); read(await PL.request("GET", "/api/settings")); status("已读取翻译配置。"); });
  const modelConfig = () => {
    const body = {lang_to:$("language").value.trim()};
    for (const id of dirty) if (id !== "clearKey" && id !== "llm_api_key") body[id] = $(id).value.trim();
    if ($("clearKey").checked) body.llm_api_key = "";
    else if ($("llm_api_key").value.trim()) body.llm_api_key = $("llm_api_key").value.trim();
    return body;
  };
  const saveModel = async () => {
    await apply();
    const config = await PL.request("PUT", "/api/settings", modelConfig());
    dirty.clear(); read(config);
    $("llm_api_key").value = ""; $("clearKey").checked = false;
    PL.setPref("language", config.lang_to || "zh");
    status("翻译配置已保存。");
  };
  action("saveModel", saveModel);
  action("testModel", async () => {
    await apply();
    const probe = await PL.request("POST", "/api/settings/test", modelConfig());
    const detail = probe.mode === "single" ? "逐段翻译测试通过" : "多段翻译及公式占位符测试通过";
    status(detail + (dirty.size ? "。点击「保存设置」后用于论文翻译。" : "。"));
  });
  try {
    if (unified) {
      const saved = await PL.Runtime.translationSettings();
      if (saved) read(saved);
    }
    if (await PL.Service.online()) read(await PL.request("GET", "/api/settings"));
  } catch (_) { status("可点击「启动并读取翻译配置」重试。"); }
});
