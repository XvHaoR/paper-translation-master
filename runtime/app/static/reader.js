/* PaperLoom 阅读页交互
 * 目录 / 搜索 / 主题 / 字号 / 图片 / 折叠文献 / 原图核对
 * 增强:阅读进度条、目录当前章节高亮、键盘导航、图片灯箱、块复制
 */

(function () {
  "use strict";

  var blocksData = [];
  try {
    blocksData = JSON.parse(document.getElementById("blocks-data").textContent);
  } catch (e) {
    blocksData = [];
  }

  // ---- 工具函数 ----
  function $(sel) { return document.querySelector(sel); }
  function all(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

  // ---- 主题 ----
  var themeBtn = $("#tb-theme");
  function applyTheme(t) {
    document.documentElement.classList.toggle("dark", t === "dark");
    localStorage.setItem("pb-theme", t);
  }
  themeBtn.addEventListener("click", function () {
    var isDark = document.documentElement.classList.contains("dark");
    applyTheme(isDark ? "light" : "dark");
  });

  // ---- 字号(点击 Aa 弹出调节器:减 / 输入 / 增,范围 10-36px) ----
  var fontBtn = $("#tb-font");
  var fontPop = document.createElement("div");
  fontPop.className = "font-pop";
  fontPop.hidden = true;
  fontPop.innerHTML =
    '<button id="font-dec" type="button" title="减小字号">−</button>' +
    '<input id="font-val" type="number" min="10" max="36" step="1" title="字号(px)" aria-label="字号">' +
    '<button id="font-inc" type="button" title="增大字号">+</button>';
  document.body.appendChild(fontPop);
  function applyFont(size) {
    size = Math.max(10, Math.min(36, Math.round(size) || 17));
    document.body.style.fontSize = size + "px";
    var inp = $("#font-val");
    if (inp) inp.value = size;
    try { localStorage.setItem("pb-font", String(size)); } catch (e) { /* 忽略 */ }
  }
  applyFont(parseInt(localStorage.getItem("pb-font") || "17", 10) || 17);
  fontBtn.addEventListener("click", function () {
    fontPop.hidden = !fontPop.hidden;
    if (!fontPop.hidden) {
      var r = fontBtn.getBoundingClientRect();
      fontPop.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 120)) + "px";
      fontPop.style.top = (r.bottom + 6) + "px";
      $("#font-val").focus(); $("#font-val").select();
    }
  });
  $("#font-dec").addEventListener("click", function () { applyFont(parseInt($("#font-val").value, 10) - 1); });
  $("#font-inc").addEventListener("click", function () { applyFont(parseInt($("#font-val").value, 10) + 1); });
  $("#font-val").addEventListener("change", function () { applyFont(parseInt(this.value, 10) || 17); });
  $("#font-val").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { applyFont(parseInt(this.value, 10) || 17); fontPop.hidden = true; }
    e.stopPropagation();
  });

  // ---- 点击空白处关闭侧边面板(目录/搜索/翻译/字号/表格) ----
  document.addEventListener("click", function (e) {
    if (!e.target || !e.target.closest) return;
    if (e.target.closest(".toc-panel") || e.target.closest(".find-panel") ||
        e.target.closest(".translate-panel") || e.target.closest(".font-pop") ||
        e.target.closest(".tables-panel") || e.target.closest(".pb-annotate-bar") ||
        e.target.closest("#tb-toc") || e.target.closest("#tb-find") ||
        e.target.closest("#tb-translate") || e.target.closest("#tb-font") ||
        e.target.closest("#tb-tables")) return;
    tocPanel.hidden = true;
    findPanel.hidden = true;
    translatePanel.hidden = true;
    fontPop.hidden = true;
    mathCopyBtn.hidden = true;
    var tspanel = $("#tables-panel");
    if (tspanel) tspanel.hidden = true;
    var st = $("#tr-settings");
    if (st) st.hidden = true;
    annotBar.hidden = true;
    exitNoteMode();
  });

  // ---- 阅读进度条 ----
  var progressBar = document.createElement("div");
  progressBar.id = "reading-progress";
  document.body.appendChild(progressBar);
  function updateProgress() {
    var doc = document.documentElement;
    var total = doc.scrollHeight - doc.clientHeight;
    var pct = total > 0 ? (doc.scrollTop / total) * 100 : 0;
    progressBar.style.width = pct.toFixed(1) + "%";
  }
  window.addEventListener("scroll", updateProgress, { passive: true });
  updateProgress();

  // ---- 目录 ----
  var tocBtn = $("#tb-toc"), tocPanel = $("#toc-panel");
  tocBtn.addEventListener("click", function () {
    tocPanel.hidden = !tocPanel.hidden;
    $("#find-panel").hidden = true;
  });

  // 目录层级缩进(按目标标题的 h2/h3/h4 级别)
  var tocLinks = all("#toc-panel a[data-target]");
  tocLinks.forEach(function (a) {
    var t = document.getElementById(a.getAttribute("data-target"));
    if (t) {
      var h = t.matches && t.matches("h2, h3, h4, h5, h6")
        ? t : t.querySelector("h2, h3, h4, h5, h6");
      if (h) a.classList.add("level-" + h.tagName.charAt(1));
    }
    a.addEventListener("click", function () { tocPanel.hidden = true; });
  });

  // ---- 目录当前章节高亮(滚动监听) ----
  var headingEls = all(".block-heading");
  function highlightCurrentHeading() {
    var mid = window.scrollY + window.innerHeight * 0.3;
    var current = null;
    for (var i = 0; i < headingEls.length; i++) {
      if (headingEls[i].offsetTop <= mid) current = headingEls[i];
      else break;
    }
    var id = current ? current.getAttribute("id") : null;
    tocLinks.forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-target") === id);
    });
  }
  window.addEventListener("scroll", highlightCurrentHeading, { passive: true });
  highlightCurrentHeading();

  // ---- 搜索 ----
  var findBtn = $("#tb-find"), findPanel = $("#find-panel"),
      findInput = $("#find-input"), findResults = $("#find-results");
  findBtn.addEventListener("click", function () {
    findPanel.hidden = !findPanel.hidden;
    tocPanel.hidden = true;
    if (!findPanel.hidden) findInput.focus();
  });
  findInput.addEventListener("input", function () {
    var q = findInput.value.trim().toLowerCase();
    findResults.innerHTML = "";
    if (!q) return;
    var hits = blocksData.filter(function (b) {
      return (b.text || "").toLowerCase().includes(q) ||
             (b.latex || "").toLowerCase().includes(q);
    }).slice(0, 50);
    hits.forEach(function (b) {
      var li = document.createElement("li");
      var text = (b.text || b.latex || "").slice(0, 90);
      var i = text.toLowerCase().indexOf(q);
      if (i >= 0) {
        text = text.slice(0, i) + "<mark>" + text.slice(i, i + q.length) + "</mark>" + text.slice(i + q.length);
      }
      li.innerHTML = text + ' <span class="hit-page">p.' + b.page + "</span>";
      li.addEventListener("click", function () {
        var el = document.getElementById(b.id);
        if (el) { el.scrollIntoView({ behavior: "smooth" }); el.classList.add("flash"); setTimeout(function () { el.classList.remove("flash"); }, 1400); }
      });
      findResults.appendChild(li);
    });
  });

  // ---- 隐藏图片 / 折叠参考文献 ----
  var figuresHidden = false;
  $("#tb-figures").addEventListener("click", function () {
    figuresHidden = !figuresHidden;
    all(".block-figure, .block-caption").forEach(function (el) {
      el.style.display = figuresHidden ? "none" : "";
    });
  });
  var refsHidden = false;
  $("#tb-refs").addEventListener("click", function () {
    refsHidden = !refsHidden;
    all(".block-reference").forEach(function (el) {
      el.style.display = refsHidden ? "none" : "";
    });
  });

  // ---- 图片灯箱(点击正文图片放大,方向键切换同页图片) ----
  var lightboxImages = []; // {img: Element, page: Number}
  all(".figure-block img").forEach(function (img) {
    var pageEl = img.closest(".paper-page");
    lightboxImages.push({ img: img, page: pageEl ? parseInt(pageEl.getAttribute("data-page"), 10) : 0 });
  });
  var lightboxIdx = -1;
  var modal = $("#crop-modal"), modalBody = $("#crop-body"), modalTitle = $("#crop-title");
  function showImage(item, title) {
    modalTitle.textContent = title;
    modalBody.innerHTML = "";
    var img = document.createElement("img");
    img.src = item.img.getAttribute("src");
    img.alt = title;
    modalBody.appendChild(img);
    modal.hidden = false;
  }
  function openLightbox(item) {
    lightboxIdx = lightboxImages.indexOf(item);
    showImage(item, "图 " + item.page + "（点击遮罩或按 Esc 关闭，← → 切换本页图片）");
  }
  function cycleLightbox(dir) {
    if (lightboxIdx < 0) return;
    var page = lightboxImages[lightboxIdx].page;
    var samePage = [];
    lightboxImages.forEach(function (it, idx) { if (it.page === page) samePage.push(idx); });
    var pos = samePage.indexOf(lightboxIdx);
    var next = samePage[(pos + dir + samePage.length) % samePage.length];
    lightboxIdx = next;
    showImage(lightboxImages[next], "图 " + page + "（← → 切换本页图片）");
  }
  lightboxImages.forEach(function (item) {
    item.img.addEventListener("click", function () { openLightbox(item); });
  });

  // ---- 原图核对弹层 ----
  function showCrop(src, title) {
    lightboxIdx = -1;
    modalTitle.textContent = title;
    modalBody.innerHTML = "";
    var img = document.createElement("img");
    img.src = src;
    img.alt = title;
    modalBody.appendChild(img);
    modal.hidden = false;
  }
  $("#crop-close").addEventListener("click", function () { modal.hidden = true; });
  modal.addEventListener("click", function (e) {
    if (e.target === modal) modal.hidden = true;
  });

  all(".crop-link").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      var crop = a.getAttribute("data-crop");
      if (crop) showCrop(crop, "原始区域（PDF 页面截图）");
    });
  });
  all(".figure-open").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      showCrop(a.getAttribute("href"), "原始图片");
    });
  });

  // ---- 块复制：悬浮按钮挂在 body，而不改写原文段落 ----
  // Translation extensions track paragraph children.  Adding a button inside
  // a paragraph on hover can make the extension hide/rebuild its translation,
  // so this control is intentionally outside every source block.
  function blockCopyText(b) {
    var meta = blocksData.filter(function (x) { return x.id === b.getAttribute("id"); })[0];
    if (!meta) return "";
    if (b.classList.contains("block-equation_block") && meta.latex) return meta.latex;
    if (b.classList.contains("block-equation_inline")) return meta.latex || "";
    return meta.text || meta.latex || "";
  }
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* 忽略 */ }
    document.body.removeChild(ta);
  }
  var blockCopyBtn = document.createElement("button");
  blockCopyBtn.type = "button";
  blockCopyBtn.className = "pb-block-copy";
  blockCopyBtn.hidden = true;
  document.body.appendChild(blockCopyBtn);
  var blockCopyTarget = null, blockCopyTimer = null;
  function hideBlockCopyLater() {
    clearTimeout(blockCopyTimer);
    blockCopyTimer = setTimeout(function () {
      blockCopyBtn.hidden = true;
      blockCopyTarget = null;
    }, 180);
  }
  all(".block.block-paragraph, .block.block-equation_block, .block.block-equation_inline").forEach(function (b) {
    b.addEventListener("mouseenter", function () {
      clearTimeout(blockCopyTimer);
      blockCopyTarget = b;
      var isEq = b.classList.contains("block-equation_block") || b.classList.contains("block-equation_inline");
      blockCopyBtn.textContent = isEq ? "复制 LaTeX" : "复制";
      blockCopyBtn.title = isEq ? "复制 LaTeX 代码" : "复制本段文本";
      var rect = b.getBoundingClientRect();
      blockCopyBtn.style.left = Math.max(4, Math.min(rect.right - 64, window.innerWidth - 70)) + "px";
      blockCopyBtn.style.top = Math.max(4, rect.top + 4) + "px";
      blockCopyBtn.hidden = false;
    });
    b.addEventListener("mouseleave", hideBlockCopyLater);
  });
  blockCopyBtn.addEventListener("mouseenter", function () { clearTimeout(blockCopyTimer); });
  blockCopyBtn.addEventListener("mouseleave", hideBlockCopyLater);
  blockCopyBtn.addEventListener("click", function () {
    if (!blockCopyTarget) return;
    var text = blockCopyText(blockCopyTarget);
    if (!text) return;
    var done = function () {
      blockCopyBtn.textContent = "已复制";
      setTimeout(function () {
        if (blockCopyTarget) {
          var isEq = blockCopyTarget.classList.contains("block-equation_block") ||
            blockCopyTarget.classList.contains("block-equation_inline");
          blockCopyBtn.textContent = isEq ? "复制 LaTeX" : "复制";
        }
      }, 1000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  });

  // ---- 键盘导航 ----
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      modal.hidden = true; tocPanel.hidden = true; findPanel.hidden = true;
      return;
    }
    // 输入框内不触发页面级快捷键
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;

    if (e.key === "/") {
      e.preventDefault();
      findPanel.hidden = false;
      tocPanel.hidden = true;
      findInput.focus();
    } else if (e.key === "t" || e.key === "T") {
      tocPanel.hidden = !tocPanel.hidden;
      $("#find-panel").hidden = true;
    } else if (e.key === "g" || e.key === "G") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else if (e.key === "y" || e.key === "Y") {
      // 翻译面板(Shift+Y 直接展开 API 设置)
      translatePanel.hidden = !translatePanel.hidden;
      $("#find-panel").hidden = true;
      tocPanel.hidden = true;
      if (e.shiftKey && !translatePanel.hidden) {
        $("#tr-settings").hidden = false;
        trLoadSettings();
      } else if (translatePanel.hidden) {
        $("#tr-settings").hidden = true;
      }
    } else if (e.key === "[" || e.key === "]") {
      var pages = all(".paper-page");
      if (!pages.length) return;
      var mid = window.scrollY + window.innerHeight * 0.3;
      var cur = 0;
      for (var i = 0; i < pages.length; i++) {
        if (pages[i].offsetTop <= mid) cur = i;
        else break;
      }
      var next = e.key === "]" ? Math.min(cur + 1, pages.length - 1) : Math.max(cur - 1, 0);
      pages[next].scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (e.key === "ArrowLeft" && !modal.hidden) {
      cycleLightbox(-1);
    } else if (e.key === "ArrowRight" && !modal.hidden) {
      cycleLightbox(1);
    }
  });

  // ---- 阅读位置记忆 ----
  var docId = (location.pathname.match(/\/documents\/([^/]+)\//) || [])[1] || "doc";
  var scrollKey = "pb-scroll-" + docId;
  var scrollTimer = null;
  window.addEventListener("scroll", function () {
    if (scrollTimer) return;
    scrollTimer = setTimeout(function () {
      scrollTimer = null;
      try { localStorage.setItem(scrollKey, String(window.scrollY)); } catch (e) { /* 忽略 */ }
    }, 300);
  }, { passive: true });
  function restoreScroll() {
    try {
      var y = parseInt(localStorage.getItem(scrollKey) || "0", 10);
      if (y > 0) window.scrollTo(0, y);
    } catch (e) { /* 忽略 */ }
  }
  var mjPromise = (typeof MathJax !== "undefined" && MathJax.startup && MathJax.startup.promise) ? MathJax.startup.promise : null;
  if (mjPromise) mjPromise.then(function () { setTimeout(restoreScroll, 60); });
  else setTimeout(restoreScroll, 1500);

  // ---- 引用跳转 ----
  function isSkippedNode(node) {
    for (var el = node; el && el !== document.body; el = el.parentElement) {
      if (!el.classList) continue;
      if (el.classList.contains("math-inline") || el.classList.contains("notranslate") ||
          el.classList.contains("page-marker") || el.classList.contains("review-badge") ||
          el.classList.contains("block-copy") || el.classList.contains("cite-link")) return true;
      if (el.tagName === "A" || el.tagName === "BUTTON") return true;
    }
    return false;
  }
  var refMap = {};
  all(".block-reference").forEach(function (r) {
    var p = r.querySelector("p") || r;  // 跳过块内 page-marker 前缀
    var m = (p.textContent || "").match(/^\s*\[?(\d{1,3})\]?[.\s]/);
    if (m) refMap[parseInt(m[1], 10)] = r.getAttribute("id");
  });
  function linkCitations() {
    if (!Object.keys(refMap).length) return;
    all(".block-paragraph").forEach(function (p) {
      var walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
      var tns = [];
      while (walker.nextNode()) tns.push(walker.currentNode);
      tns.forEach(function (tn) {
        if (isSkippedNode(tn.parentElement)) return;
        var text = tn.nodeValue;
        if (!/\[\d/.test(text)) return;
        var re = /\[(\d{1,3})\]/g, m, last = 0, changed = false;
        var frag = document.createDocumentFragment();
        while ((m = re.exec(text))) {
          var num = parseInt(m[1], 10);
          if (!refMap[num]) continue;
          frag.appendChild(document.createTextNode(text.slice(last, m.index)));
          (function (targetId) {
            var a = document.createElement("a");
            a.className = "cite-link";
            a.href = "#" + targetId;
            a.textContent = m[0];
            a.addEventListener("click", function (e) {
              e.preventDefault();
              var el = document.getElementById(targetId);
              if (el) { el.scrollIntoView({ behavior: "smooth" }); el.classList.add("flash"); setTimeout(function () { el.classList.remove("flash"); }, 1400); }
            });
            frag.appendChild(a);
          })(refMap[num]);
          last = m.index + m[0].length;
          changed = true;
        }
        if (changed) {
          frag.appendChild(document.createTextNode(text.slice(last)));
          tn.parentNode.replaceChild(frag, tn);
        }
      });
    });
  }
  linkCitations();

  // ---- 文献/脚注悬停预览 ----
  var tip = document.createElement("div");
  tip.className = "pb-tooltip";
  tip.hidden = true;
  document.body.appendChild(tip);
  var tipTimer = null;
  function hideTipLater() {
    clearTimeout(tipTimer);
    tipTimer = setTimeout(function () { tip.hidden = true; }, 260);
  }
  function copyText(text) {
    var done = function () { /* 调用方处理反馈 */ };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  }
  all(".block-reference, .block-footnote").forEach(function (b) {
    b.addEventListener("mouseenter", function (e) {
      clearTimeout(tipTimer);
      var p = b.querySelector("p") || b;
      var text = (p.textContent || "").replace(/\s+/g, " ").trim();
      tip.innerHTML = "";
      tip.appendChild(document.createTextNode(text));
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pb-tip-copy";
      btn.textContent = "复制";
      btn.addEventListener("click", function () {
        copyText(text);
        btn.textContent = "已复制";
        setTimeout(function () { btn.textContent = "复制"; }, 1000);
      });
      tip.appendChild(btn);
      tip.hidden = false;
      var rect = tip.getBoundingClientRect();
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - rect.width - 10) + "px";
      tip.style.top = Math.min(e.clientY + 14, window.innerHeight - rect.height - 10) + "px";
    });
    b.addEventListener("mouseleave", hideTipLater);
  });
  tip.addEventListener("mouseenter", function () { clearTimeout(tipTimer); });
  tip.addEventListener("mouseleave", hideTipLater);

  // ---- 批注与高亮 ----
  var annotKey = "pb-annotations-" + docId;
  var ANNOT_TYPES = ["paragraph", "heading", "caption", "reference", "footnote", "abstract", "document_title"];
  function loadAnnotations() {
    try { return JSON.parse(localStorage.getItem(annotKey) || "[]"); } catch (e) { return []; }
  }
  function saveAnnotations(list) {
    try { localStorage.setItem(annotKey, JSON.stringify(list)); } catch (e) { /* 忽略 */ }
  }
  // 块内"纯文本"节点序列(跳过数学/标记节点),返回 [{node, start, end}]
  function plainTextSegments(blockEl) {
    var segs = [], pos = 0;
    var walker = document.createTreeWalker(blockEl, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      var tn = walker.currentNode;
      if (isSkippedNode(tn.parentElement)) continue;
      segs.push({ node: tn, start: pos, end: pos + tn.nodeValue.length });
      pos += tn.nodeValue.length;
    }
    return segs;
  }
  function blockPlainText(blockEl) {
    return plainTextSegments(blockEl).map(function (s) { return s.node.nodeValue; }).join("");
  }
  // 选区 → 块内纯文本偏移
  function selectionOffsetInBlock(blockEl, node, offset) {
    var segs = plainTextSegments(blockEl);
    var nodeIndex = -1;
    for (var i = 0; i < segs.length; i++) { if (segs[i].node === node) { nodeIndex = i; break; } }
    if (nodeIndex < 0) {
      // 边界落在非文本元素上:取该位置前最近位置
      var before = 0;
      for (var j = 0; j < segs.length; j++) {
        var r = document.createRange();
        r.selectNodeContents(segs[j].node);
        if (r.comparePoint(node, offset) <= 0) before = segs[j].end;
        else break;
      }
      return before;
    }
    return segs[nodeIndex].start + Math.max(0, Math.min(offset, segs[nodeIndex].node.nodeValue.length));
  }
  function annotatableBlock(el) {
    while (el && el !== document.body) {
      if (el.classList && el.classList.contains("block") && el.getAttribute("data-block-id")) {
        var t = el.getAttribute("data-block-type");
        if (ANNOT_TYPES.indexOf(t) >= 0) return el;
        return null;
      }
      el = el.parentElement;
    }
    return null;
  }
  // 恢复高亮(先清除旧 mark,避免重复包裹)
  function renderAnnotations() {
    var list = loadAnnotations();
    // 清除旧 mark 但保留其中文本:直接 remove() 会把 mark 内的文本节点
    // 一起从 DOM 删掉,重建后正文文本就永久丢失了
    all(".pb-hl").forEach(function (m) {
      while (m.firstChild) m.parentNode.insertBefore(m.firstChild, m);
      m.parentNode.removeChild(m);
    });
    if (!list.length) return;
    var byBlock = {};
    list.forEach(function (a) { (byBlock[a.blockId] = byBlock[a.blockId] || []).push(a); });
    Object.keys(byBlock).forEach(function (bid) {
      var el = document.getElementById(bid);
      if (!el) return;
      // 从后往前包裹,偏移不互相影响;每次重新遍历文本节点
      // (前一条包裹会替换文本节点,再在旧节点上建 Range 会抛异常)
      byBlock[bid].sort(function (a, b) { return b.start - a.start; });
      byBlock[bid].forEach(function (a) {
        try {
          var segs = plainTextSegments(el);
          var s = Math.max(0, a.start), e = Math.min(blockPlainText(el).length, a.end);
          if (e <= s) return;
          segs.forEach(function (seg) {
            if (seg.node.isConnected === false) return; // 已被上一条标注替换
            var os = Math.max(s, seg.start), oe = Math.min(e, seg.end);
            if (oe <= os) return;
            var mark = document.createElement("mark");
            mark.className = "pb-hl" + (a.note ? " has-note" : "");
            mark.textContent = seg.node.nodeValue.slice(os - seg.start, oe - seg.start);
            var rng = document.createRange();
            rng.setStart(seg.node, os - seg.start);
            rng.setEnd(seg.node, oe - seg.start);
            rng.deleteContents();
            rng.insertNode(mark);
            mark.addEventListener("click", function () {
              showNoteCard(mark, a);
            });
            if (a.note) {
              // 带笔记的高亮:追加 📝 标识,悬停直接预览笔记
              var nm = document.createElement("span");
              nm.className = "pb-note-mark";
              nm.textContent = "📝";
              nm.title = "点击/悬停查看笔记";
              mark.appendChild(nm);
              mark.addEventListener("mouseenter", function () { showNoteCard(mark, a); });
              mark.addEventListener("mouseleave", hideTipLater);
            }
          });
        } catch (err) { /* 单条标注渲染失败不阻断其余 */ }
      });
    });
  }
  // 笔记卡片(复用 tooltip)
  function showNoteCard(mark, a) {
    tip.innerHTML = "";
    if (a.note) tip.appendChild(document.createTextNode(a.note));
    else tip.appendChild(document.createTextNode("(无笔记)"));
    if (!a.note) {
      // 无笔记的高亮:提供「添加笔记」入口
      var addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "pb-tip-copy";
      addBtn.textContent = "添加笔记";
      addBtn.addEventListener("click", function () {
        tip.hidden = true;
        var blockEl = document.getElementById(a.blockId);
        if (!blockEl) return;
        // 复用工具条的笔记输入模式,保存时更新该标注
        pendingNote = { block: blockEl, s: a.start, e: a.end, text: a.text || "" };
        var r = mark.getBoundingClientRect();
        annotBar.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 240)) + "px";
        annotBar.style.top = Math.max(4, r.top - 40) + "px";
        hlBtn.hidden = true;
        noteBtn.hidden = true;
        annotClose.hidden = true;
        noteInput.hidden = false;
        noteSave.hidden = false;
        noteCancel.hidden = false;
        noteInput.value = "";
        noteInput.focus();
      });
      tip.appendChild(addBtn);
    }
    var del = document.createElement("button");
    del.type = "button";
    del.className = "pb-tip-copy";
    del.textContent = "删除";
    del.addEventListener("click", function () {
      var list = loadAnnotations().filter(function (x) { return !(x.blockId === a.blockId && x.start === a.start); });
      saveAnnotations(list);
      tip.hidden = true;
      var el = document.getElementById(a.blockId);
      if (el) renderAnnotations();
    });
    tip.appendChild(del);
    var rect = mark.getBoundingClientRect();
    tip.hidden = false;
    var tr = tip.getBoundingClientRect();
    tip.style.left = Math.min(rect.left, window.innerWidth - tr.width - 10) + "px";
    tip.style.top = (rect.bottom + 6) + "px";
  }
  // 选择工具条
  var annotBar = document.createElement("div");
  annotBar.className = "pb-annotate-bar";
  annotBar.hidden = true;
  document.body.appendChild(annotBar);
  // 工具条显示时抓取的选区快照(按钮点击时实时选区可能已被浏览器清空)
  var pendingSel = null;
  var hlBtn = document.createElement("button");
  hlBtn.type = "button";
  hlBtn.textContent = "🖍 高亮";
  hlBtn.addEventListener("click", function () {
    var selInfo = pendingSel;
    if (!selInfo) {
      var sel = window.getSelection();
      var blk = annotatableBlock(sel.anchorNode.parentElement);
      if (!blk) return;
      var s0 = selectionOffsetInBlock(blk, sel.anchorNode, sel.anchorOffset);
      var e0 = selectionOffsetInBlock(blk, sel.focusNode, sel.focusOffset);
      if (s0 > e0) { var t0 = s0; s0 = e0; e0 = t0; }
      if (e0 - s0 < 1) return;
      selInfo = { block: blk, s: s0, e: e0, text: blockPlainText(blk).slice(s0, e0).replace(/\s+/g, " ").trim() };
    }
    var list = loadAnnotations();
    list.push({ blockId: selInfo.block.getAttribute("id"), start: selInfo.s, end: selInfo.e, text: selInfo.text, note: "", created: Date.now() });
    saveAnnotations(list);
    annotBar.hidden = true;
    pendingSel = null;
    var sel2 = window.getSelection();
    if (sel2) sel2.removeAllRanges();
    renderAnnotations();
  });
  var noteBtn = document.createElement("button");
  noteBtn.type = "button";
  noteBtn.textContent = "📝 笔记";
  // 笔记输入模式:内联输入框(替代 window.prompt,避免对话框被拦截)
  var noteInput = document.createElement("input");
  noteInput.type = "text";
  noteInput.placeholder = "输入笔记内容,回车保存";
  noteInput.hidden = true;
  var noteSave = document.createElement("button");
  noteSave.type = "button";
  noteSave.textContent = "保存";
  noteSave.hidden = true;
  var noteCancel = document.createElement("button");
  noteCancel.type = "button";
  noteCancel.textContent = "取消";
  noteCancel.hidden = true;
  var pendingNote = null;
  function exitNoteMode() {
    hlBtn.hidden = false;
    noteBtn.hidden = false;
    annotClose.hidden = false;
    noteInput.hidden = true;
    noteSave.hidden = true;
    noteCancel.hidden = true;
    pendingNote = null;
  }
  noteBtn.addEventListener("click", function () {
    var selInfo = pendingSel;
    if (!selInfo) {
      var sel = window.getSelection();
      var blk = annotatableBlock(sel.anchorNode.parentElement);
      if (!blk) return;
      var s0 = selectionOffsetInBlock(blk, sel.anchorNode, sel.anchorOffset);
      var e0 = selectionOffsetInBlock(blk, sel.focusNode, sel.focusOffset);
      if (s0 > e0) { var t0 = s0; s0 = e0; e0 = t0; }
      if (e0 - s0 < 1) return;
      selInfo = { block: blk, s: s0, e: e0, text: blockPlainText(blk).slice(s0, e0).replace(/\s+/g, " ").trim() };
    }
    pendingNote = { block: selInfo.block, s: selInfo.s, e: selInfo.e, text: selInfo.text };
    hlBtn.hidden = true;
    noteBtn.hidden = true;
    annotClose.hidden = true;
    noteInput.hidden = false;
    noteSave.hidden = false;
    noteCancel.hidden = false;
    noteInput.value = "";
    noteInput.focus();
    var sel2 = window.getSelection();
    if (sel2) sel2.removeAllRanges();
  });
  function savePendingNote() {
    if (!pendingNote) return;
    var note = noteInput.value.trim();
    var list = loadAnnotations();
    var bid = pendingNote.block.getAttribute("id");
    // 已存在同一位置的高亮 → 更新其笔记;否则新建标注
    var found = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i].blockId === bid && list[i].start === pendingNote.s) { found = list[i]; break; }
    }
    if (found) {
      found.note = note;
    } else {
      list.push({
        blockId: bid,
        start: pendingNote.s,
        end: pendingNote.e,
        text: pendingNote.text,
        note: note,
        created: Date.now(),
      });
    }
    saveAnnotations(list);
    exitNoteMode();
    pendingSel = null;
    annotBar.hidden = true;
    renderAnnotations();
  }
  noteSave.addEventListener("click", savePendingNote);
  noteCancel.addEventListener("click", exitNoteMode);
  noteInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); savePendingNote(); }
    else if (e.key === "Escape") { exitNoteMode(); }
    e.stopPropagation();
  });
  var annotClose = document.createElement("button");
  annotClose.type = "button";
  annotClose.textContent = "✕";
  annotClose.addEventListener("click", function () {
    exitNoteMode();
    annotBar.hidden = true;
    var sel = window.getSelection();
    if (sel) sel.removeAllRanges();
  });
  annotBar.appendChild(hlBtn);
  annotBar.appendChild(noteBtn);
  annotBar.appendChild(annotClose);
  annotBar.appendChild(noteInput);
  annotBar.appendChild(noteSave);
  annotBar.appendChild(noteCancel);
  // 点击工具条自身(高亮/笔记/关闭)后,不让 mouseup 重新弹出
  var annotBarClicked = false;
  annotBar.addEventListener("mousedown", function () { annotBarClicked = true; });
  document.addEventListener("mouseup", function (e) {
    setTimeout(function () {
      var clickedBar = annotBarClicked;
      annotBarClicked = false;
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.toString().trim()) {
        if (pendingNote) return; // 笔记输入模式中不清空/不隐藏
        annotBar.hidden = true;
        pendingSel = null;
        return;
      }
      // 点击工具条自身不重新定位/不重复弹出
      if (clickedBar) return;
      if (e.target && e.target.closest && e.target.closest(".pb-annotate-bar")) return;
      var block = annotatableBlock(sel.anchorNode.parentElement);
      if (!block) { annotBar.hidden = true; return; }
      var s = selectionOffsetInBlock(block, sel.anchorNode, sel.anchorOffset);
      var e2 = selectionOffsetInBlock(block, sel.focusNode, sel.focusOffset);
      if (s > e2) { var t = s; s = e2; e2 = t; }
      if (e2 - s < 1) { annotBar.hidden = true; return; }
      // 抓取选区快照,供高亮/笔记按钮使用(点击按钮时实时选区可能已被清空)
      pendingSel = {
        block: block,
        s: s,
        e: e2,
        text: blockPlainText(block).slice(s, e2).replace(/\s+/g, " ").trim(),
      };
      annotBar.hidden = false;
      exitNoteMode();
      var r = sel.getRangeAt(0).getBoundingClientRect();
      annotBar.style.left = Math.min(r.left, window.innerWidth - 180) + "px";
      annotBar.style.top = Math.max(4, r.top - 34) + "px";
    }, 10);
  });
  // 一旦没有选中文本(点击空白/取消选区/完成操作),立即隐藏工具条
  document.addEventListener("selectionchange", function () {
    if (pendingNote) return; // 笔记输入模式中不清空
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      annotBar.hidden = true;
      pendingSel = null;
      exitNoteMode();
    }
  });
  // 批注显隐开关(工具栏 tb-annotate)
  var annotBtn = $("#tb-annotate");
  if (annotBtn) {
    annotBtn.addEventListener("click", function () {
      document.body.classList.toggle("pb-hl-hidden");
      annotBtn.classList.toggle("active");
    });
  }
  renderAnnotations();

  // ---- 翻译(双语模式) ----
  var trBtn = $("#tb-translate");
  var TR_TYPES = ["document_title", "authors", "abstract", "heading", "paragraph", "caption", "reference", "footnote", "list"];
  var translatePanel = document.createElement("div");
  translatePanel.className = "translate-panel";
  translatePanel.hidden = true;
  translatePanel.innerHTML =
    '<h3>翻译</h3>' +
    '<label class="tr-lang-label">目标语言 <input id="tr-lang" value="zh" size="4" maxlength="8"></label>' +
    '<div class="tr-actions">' +
    '<button id="tr-start">开始翻译</button>' +
    '<button id="tr-cancel" disabled>取消</button>' +
    '<button id="tr-toggle">隐藏译文</button>' +
    '<button id="tr-clear">清除译文</button>' +
    '</div>' +
    '<div id="tr-progress" class="tr-progress"></div>' +
    '<div id="tr-errors" class="tr-errors"></div>' +
    '<button id="tr-settings-toggle" class="tr-settings-toggle">⚙ API 设置</button>' +
    '<div id="tr-settings" class="tr-settings" hidden>' +
    '<label>服务地址<br><input id="tr-s-base" placeholder="https://api.deepseek.com/v1 或 http://localhost:11434/v1"></label>' +
    '<label>API Key<br><input id="tr-s-key" type="password" placeholder="可留空(如 Ollama)"></label>' +
    '<label>模型名<br><input id="tr-s-model" placeholder="deepseek-chat"></label>' +
    '<label>并发 <input id="tr-s-conc" type="number" min="1" max="16" value="4" style="width:56px"></label>' +
    '<div class="tr-actions" style="margin-top:6px">' +
    '<button id="tr-s-save">保存</button><button id="tr-s-test">测试连接</button><span id="tr-s-status" class="tr-s-status"></span>' +
    '</div>' +
    '</div>';
  document.body.appendChild(translatePanel);

  // 翻译设置(集成在阅读页,存服务端 workspace/settings.json)
  function trLoadSettings() {
    fetch("/api/settings").then(function (r) { return r.json(); }).then(function (s) {
      $("#tr-s-base").value = s.llm_base_url || "";
      $("#tr-s-key").value = s.llm_api_key || "";
      $("#tr-s-model").value = s.llm_model || "";
      $("#tr-s-conc").value = s.llm_concurrency || 4;
      $("#tr-lang").value = s.lang_to || "zh";
    }).catch(function () {
      var st = $("#tr-s-status");
      st.textContent = "读取设置失败"; st.style.color = "var(--warn-fg)";
    });
  }
  $("#tr-settings-toggle").addEventListener("click", function () {
    var box = $("#tr-settings");
    box.hidden = !box.hidden;
    if (!box.hidden) trLoadSettings();
  });
  $("#tr-s-save").addEventListener("click", function () {
    var body = {
      llm_base_url: $("#tr-s-base").value.trim(),
      llm_api_key: $("#tr-s-key").value.trim(),
      llm_model: $("#tr-s-model").value.trim(),
      llm_concurrency: parseInt($("#tr-s-conc").value, 10) || 4,
      lang_to: $("#tr-lang").value.trim() || "zh"
    };
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      var st = $("#tr-s-status");
      if (r.ok) { st.textContent = "已保存"; st.style.color = ""; return; }
      return r.json().then(function (d) { throw new Error(d.detail || "保存失败"); });
    }).catch(function (e) {
      var st = $("#tr-s-status");
      st.textContent = e.message; st.style.color = "var(--warn-fg)";
    });
  });
  $("#tr-s-test").addEventListener("click", async function () {
    var st = $("#tr-s-status");
    this.disabled = true;
    st.textContent = "测试中...";
    try {
      var resp = await fetch("/api/settings/test", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({llm_base_url: $("#tr-s-base").value.trim(), llm_api_key: $("#tr-s-key").value.trim(),
          llm_model: $("#tr-s-model").value.trim()})});
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "连接失败");
      st.textContent = "连接成功：" + data.text; st.style.color = "";
    } catch (e) { st.textContent = e.message; st.style.color = "var(--warn-fg)"; }
    finally { this.disabled = false; }
  });

  var trRunning = false, trCancel = false, trController = null;
  function trLog(msg) { $("#tr-progress").textContent = msg; }
  function trError(msg) {
    var box = $("#tr-errors");
    var d = document.createElement("div");
    d.textContent = msg;
    box.appendChild(d);
  }
  function translateableBlocks() {
    return blocksData.filter(function (b) {
      return TR_TYPES.indexOf(b.type) >= 0 && (b.text || "").trim();
    });
  }
  // 译文中的 $...$ 转成 MathJax 行内节点(与正文一致,而非显示 LaTeX 原文)
  function renderMathText(text) {
    var frag = document.createDocumentFragment();
    var re = /\$([^$\n]+)\$/g;
    var last = 0, m;
    while ((m = re.exec(text))) {
      frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      var latex = m[1];
      var span = document.createElement("span");
      span.className = "math-inline notranslate";
      span.setAttribute("translate", "no");
      span.setAttribute("data-latex", latex);
      span.textContent = "\\(" + latex + "\\)";
      frag.appendChild(span);
      last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }
  function typesetMath() {
    if (typeof MathJax !== "undefined" && MathJax.typesetPromise) {
      try { MathJax.typesetPromise(); } catch (e) { /* 忽略 */ }
    }
  }
  function renderTranslation(blockId, text) {
    var el = document.getElementById(blockId);
    if (!el) return;
    var old = el.querySelector(".pb-translation");
    if (!old && el.nextElementSibling && el.nextElementSibling.dataset.translationFor === blockId) old = el.nextElementSibling;
    if (old) old.remove();
    var div = document.createElement("div");
    div.className = "pb-translation";
    div.dataset.translationFor = blockId;
    div.appendChild(renderMathText(text || ""));
    // Official Markdown wiring may put the block id directly on a semantic
    // element such as p/h2/li.  A div child would be invalid HTML there.
    if (/^(P|H[1-6]|LI)$/.test(el.tagName)) el.insertAdjacentElement("afterend", div);
    else el.appendChild(div);
    typesetMath();
  }
  async function runTranslationBatch(ids, langTo) {
    var resp = await fetch("/api/documents/" + docId + "/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block_ids: ids, lang_to: langTo }),
      signal: trController ? trController.signal : undefined
    });
    if (!resp.ok) {
      var detail = "";
      try { detail = (await resp.json()).detail || ""; } catch (e) { /* 忽略 */ }
      throw new Error(detail || ("HTTP " + resp.status));
    }
    return (await resp.json()).results || {};
  }
  async function trStart() {
    if (trRunning) return;
    var langTo = $("#tr-lang").value.trim() || "zh";
    var blocks = translateableBlocks();
    if (!blocks.length) { trLog("没有可翻译的文本块"); return; }
    trRunning = true; trCancel = false;
    trController = new AbortController();
    $("#tr-start").disabled = true;
    $("#tr-cancel").disabled = false;
    $("#tr-errors").innerHTML = "";
    var failed = [], done = 0, seenUnits = {};
    // 分母用服务端统计的"语义单元"数(相邻段落合并后),而非原始块数
    var total = blocks.length;
    try {
      var info = await (await fetch("/api/documents/" + docId + "/translate-info")).json();
      if (info && info.total_units > 0) total = info.total_units;
    } catch (e) { /* 统计接口失败时退回块数 */ }
    trLog("翻译中 0/" + total + " 语义段...");
    var BATCH = 16;
    for (var i = 0; i < blocks.length; i += BATCH) {
      if (trCancel) { trLog("已取消(" + Math.min(done, total) + "/" + total + ")"); break; }
      var ids = blocks.slice(i, i + BATCH).map(function (b) { return b.id; });
      try {
        var res = await runTranslationBatch(ids, langTo);
        Object.keys(res).forEach(function (bid) {
          var r = res[bid];
          if (r && r.text) {
            renderTranslation(bid, r.text);
            // 合并单元一次返回多块译文:按单元计数进度,不重复计
            var uid = r.unit || bid;
            if (!seenUnits[uid]) { seenUnits[uid] = 1; done++; }
          }
          else if (r && r.error) { failed.push(bid); trError(bid + ": " + r.error); }
        });
      } catch (e) {
        if (trCancel) break;
        trError("批次失败: " + e.message);
        failed = failed.concat(ids);
      }
      // 跨批次合并可能重复计数,展示时封顶
      trLog("翻译中 " + Math.min(done, total) + "/" + total + (failed.length ? " (失败 " + failed.length + ")" : "") + "...");
    }
    trRunning = false;
    $("#tr-start").disabled = false;
    $("#tr-cancel").disabled = true;
    trController = null;
    trLog((trCancel ? "已取消:" : "完成:") + done + "/" + total + (failed.length ? ",失败 " + failed.length + " 块" : ""));
    if (failed.length) {
      var retry = document.createElement("button");
      retry.textContent = "重试失败 (" + failed.length + ")";
      retry.addEventListener("click", async function () {
        trError(""); $("#tr-errors").innerHTML = "";
        var retryIds = Array.from(new Set(failed));
        for (var j = 0; j < retryIds.length; j += BATCH) {
          var ids2 = retryIds.slice(j, j + BATCH);
          try {
            var res2 = await runTranslationBatch(ids2, langTo);
            Object.keys(res2).forEach(function (bid) {
              var r = res2[bid];
              if (r && r.text) { renderTranslation(bid, r.text); failed = failed.filter(function (x) { return x !== bid; }); }
            });
          } catch (e2) { trError("重试批次失败: " + e2.message); }
        }
        trLog(failed.length ? "仍有 " + failed.length + " 块失败" : "重试完成,全部成功");
      });
      $("#tr-errors").appendChild(retry);
    }
  }
  // 「译」为唯一入口;面板内 ⚙ API 设置 + Shift+Y 直接展开设置
  if (trBtn) {
    trBtn.addEventListener("click", function () {
      translatePanel.hidden = !translatePanel.hidden;
      $("#find-panel").hidden = true;
      tocPanel.hidden = true;
      // 关闭面板时同时收起设置区
      if (translatePanel.hidden) $("#tr-settings").hidden = true;
    });
  }
  $("#tr-start").addEventListener("click", trStart);
  $("#tr-cancel").addEventListener("click", function () {
    trCancel = true;
    if (trController) trController.abort();
  });
  $("#tr-toggle").addEventListener("click", function () {
    var hidden = document.body.classList.toggle("pb-translations-hidden");
    this.textContent = hidden ? "显示译文" : "隐藏译文";
  });
  $("#tr-clear").addEventListener("click", function () {
    all(".pb-translation").forEach(function (el) { el.remove(); });
    trLog("");
  });
  document.addEventListener("keydown", function (e) {
    if (e.target && e.target.id === "tr-lang") return;
  });

  // ---- 表格导出 Excel(纯前端生成 SpreadsheetML,无第三方依赖) ----
  function tableBlocks() {
    return blocksData.filter(function (b) { return b.type === "table"; });
  }
  function tableRows(b) {
    var el = document.getElementById(b.id);
    var t = el ? el.querySelector("table") : null;
    if (!t) return [];
    var rows = [];
    t.querySelectorAll("tr").forEach(function (tr) {
      var cells = [];
      tr.querySelectorAll("th, td").forEach(function (c) {
        cells.push((c.textContent || "").replace(/\s+/g, " ").trim());
      });
      rows.push(cells);
    });
    return rows;
  }
  function buildWorkbook(items) { return PaperLoomExcel.buildWorkbook(items); }
  function downloadExcel(items, filename) {
    var blob = new Blob(["\ufeff" + buildWorkbook(items)], { type: "application/vnd.ms-excel;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    // 阻止合成点击冒泡到 document(否则"点击空白关闭面板"处理器会关掉面板)
    a.addEventListener("click", function (e) { e.stopPropagation(); });
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  function safeName(s) {
    return s.replace(/[\\\/\?\*\[\]:]/g, "_").slice(0, 30);
  }
  function exportTables(bs) {
    var items = bs.map(function (b, i) {
      return { name: "表" + (i + 1), rows: tableRows(b) };
    });
    downloadExcel(items, safeName(document.title || "paperloom") + "-表格.xls");
  }
  // 单个表格 hover → 右上角导出按钮
  all(".block-table").forEach(function (b) {
    var btn = null;
    b.addEventListener("mouseenter", function () {
      if (btn) return;
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "block-copy";
      btn.textContent = "⬇ Excel";
      btn.title = "导出此表格为 Excel";
      btn.addEventListener("click", function () {
        var bid = b.getAttribute("data-block-id");
        var meta = blocksData.filter(function (x) { return x.id === bid; })[0];
        if (meta) exportTables([meta]);
      });
      b.appendChild(btn);
    });
    b.addEventListener("mouseleave", function () { if (btn) { btn.remove(); btn = null; } });
  });
  // 工具栏 📊:表格面板(勾选导出)
  var tablesBtn = $("#tb-tables");
  var tablesPanel = document.createElement("div");
  tablesPanel.id = "tables-panel";
  tablesPanel.className = "tables-panel";
  tablesPanel.hidden = true;
  tablesPanel.innerHTML =
    "<h3>导出表格</h3>" +
    '<div id="tables-list" class="tables-list"></div>' +
    '<div class="tr-actions">' +
    '<button id="tables-export-selected" type="button">导出所选</button>' +
    '<button id="tables-export-all" type="button">导出全部</button>' +
    "</div>";
  document.body.appendChild(tablesPanel);
  function refreshTablesList() {
    var list = tableBlocks();
    var box = $("#tables-list");
    box.innerHTML = "";
    if (!list.length) { box.innerHTML = '<div class="tables-empty">本文档没有表格</div>'; return; }
    list.forEach(function (b, i) {
      var label = document.createElement("label");
      label.className = "tables-item";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = b.id;
      cb.checked = true;
      var first = (b.text || "").replace(/\s+/g, " ").trim().slice(0, 40);
      var span = document.createElement("span");
      span.textContent = "表 " + (i + 1) + " · p." + b.page + (first ? " — " + first : "");
      label.appendChild(cb);
      label.appendChild(span);
      box.appendChild(label);
    });
  }
  if (tablesBtn) {
    tablesBtn.addEventListener("click", function () {
      tablesPanel.hidden = !tablesPanel.hidden;
      $("#find-panel").hidden = true;
      tocPanel.hidden = true;
      translatePanel.hidden = true;
      if (!tablesPanel.hidden) refreshTablesList();
    });
  }
  $("#tables-export-all").addEventListener("click", function () { exportTables(tableBlocks()); });
  $("#tables-export-selected").addEventListener("click", function () {
    var ids = {};
    all("#tables-list input:checked").forEach(function (cb) { ids[cb.value] = 1; });
    var bs = tableBlocks().filter(function (b) { return ids[b.id]; });
    if (!bs.length) return;
    exportTables(bs);
  });

  // ---- 行内公式悬停:复制 LaTeX 代码 ----
  var mathCopyBtn = document.createElement("button");
  mathCopyBtn.type = "button";
  mathCopyBtn.className = "math-copy";
  mathCopyBtn.textContent = "复制 LaTeX";
  mathCopyBtn.hidden = true;
  document.body.appendChild(mathCopyBtn);
  var mathCopyTimer = null, mathCopyTarget = null;
  all(".math-inline").forEach(function (sp) {
    sp.addEventListener("mouseenter", function () {
      clearTimeout(mathCopyTimer);
      mathCopyTarget = sp;
      mathCopyBtn.hidden = false;
      var r = sp.getBoundingClientRect();
      mathCopyBtn.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 110)) + "px";
      mathCopyBtn.style.top = Math.max(4, r.top - 30) + "px";
    });
    sp.addEventListener("mouseleave", function () {
      mathCopyTimer = setTimeout(function () { mathCopyBtn.hidden = true; mathCopyTarget = null; }, 220);
    });
  });
  mathCopyBtn.addEventListener("mouseenter", function () { clearTimeout(mathCopyTimer); });
  mathCopyBtn.addEventListener("mouseleave", function () { mathCopyBtn.hidden = true; });
  mathCopyBtn.addEventListener("click", function () {
    if (!mathCopyTarget) return;
    var latex = mathCopyTarget.getAttribute("data-latex") || mathCopyTarget.textContent || "";
    copyText(latex);
    mathCopyBtn.textContent = "已复制";
    setTimeout(function () { mathCopyBtn.textContent = "复制 LaTeX"; }, 1000);
  });

  // ---- 状态提示 ----
  var status = $("#tb-status");
  var reviewCount = blocksData.filter(function (b) { return b.needs_review; }).length;
  if (reviewCount > 0) {
    status.textContent = "⚠ " + reviewCount + " 处内容需人工核对";
    status.style.color = "var(--warn-fg)";
  }
})();
