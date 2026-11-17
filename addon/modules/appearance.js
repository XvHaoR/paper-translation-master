"use strict";
(() => {
  const PL = PaperLoom;
  const defaults = {ocrFontFamily:"",translationFontFamily:"",ocrTextColor:"",translationTextColor:""};
  PL.Appearance = {
    defaults,
    validate(values) {
      const result = {};
      for (const key of Object.keys(defaults)) {
        const value = String(values[key] ?? "").trim();
        if (key.endsWith("Color")) {
          if (value && !/^#[a-f0-9]{6}$/i.test(value)) throw new Error("文字颜色须为六位十六进制颜色");
        } else if (value.length > 100 || !/^[\p{L}\p{N} ._-]*$/u.test(value)) {
          throw new Error("字体名称只支持文字、数字、空格、点、下划线和连字符");
        }
        result[key] = value;
      }
      return result;
    },
    read() {
      const result = {};
      for (const [key, value] of Object.entries(defaults)) result[key] = PL.pref(key, value);
      try { return this.validate(result); } catch (_) { return {...defaults}; }
    },
    async save(values) {
      for (const [key, value] of Object.entries(this.validate(values))) PL.setPref(key, value);
      for (const state of PL.ReaderUI.states.values()) await PL.ReaderUI.applyOptions(state);
    },
  };
})();
