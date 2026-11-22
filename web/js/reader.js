/* 阅读器：翻页 / 滚动双模式、键盘与滑动操作、进度记忆 */
const Reader = (() => {
  const el = {};
  const st = {
    comic: null,
    page: 0,
    total: 0,
    mode: "paged",
    open: false,
    uiTimer: null,
    saveTimer: null,
    loadToken: 0,
    programmaticUntil: 0,
  };

  const IDS = [
    "reader", "readerTop", "readerBottom", "rTitle", "rStage", "rImg", "rScroll",
    "rSlider", "rPageLabel", "rPct", "rPrev", "rNext", "rPrevBtn", "rNextBtn",
    "rModeBtn", "rModeLabel", "rFullBtn", "rBack", "rSpinner",
  ];

  function boot() {
    IDS.forEach((id) => { el[id] = document.getElementById(id); });
    if (!el.reader) return;

    el.rBack.addEventListener("click", close);
    el.rModeBtn.addEventListener("click", toggleMode);
    el.rFullBtn.addEventListener("click", toggleFull);
    el.rPrevBtn.addEventListener("click", (e) => { e.stopPropagation(); prev(); });
    el.rNextBtn.addEventListener("click", (e) => { e.stopPropagation(); next(); });
    el.rPrev.addEventListener("click", prev);
    el.rNext.addEventListener("click", next);

    el.rSlider.addEventListener("input", () => {
      goTo(Number(el.rSlider.value), { silent: true });
    });
    el.rSlider.addEventListener("change", () => scheduleSave());

    el.reader.addEventListener("mousemove", showUI);
    el.reader.addEventListener("pointerdown", showUI);
    el.rScroll.addEventListener("scroll", onScroll, { passive: true });

    bindSwipe();
  }

  function bindSwipe() {
    let x0 = 0, y0 = 0, t0 = 0;
    el.rStage.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) return;
      x0 = e.touches[0].clientX;
      y0 = e.touches[0].clientY;
      t0 = Date.now();
    }, { passive: true });

    el.rStage.addEventListener("touchend", (e) => {
      if (st.mode !== "paged") return;
      const t = e.changedTouches[0];
      const dx = t.clientX - x0;
      const dy = t.clientY - y0;
      if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.4) {
        dx < 0 ? next() : prev();
      } else if (Math.abs(dx) < 12 && Math.abs(dy) < 12 && Date.now() - t0 < 300) {
        el.reader.classList.toggle("ui-hidden");
      }
    }, { passive: true });
  }

  /* ---------------- 数据 ---------------- */
  function pageUrl(i) {
    return `/api/comics/${st.comic.id}/page/${i}`;
  }

  function clampPage(i) {
    return Math.max(0, Math.min(st.total - 1, Math.trunc(i) || 0));
  }

  /* ---------------- 打开 / 关闭 ---------------- */
  function open(comic, opts = {}) {
    if (!comic || !comic.pages) return false;
    st.comic = comic;
    st.total = comic.pages;
    const start = Number.isFinite(opts.page) ? opts.page : (comic.lastPage || 0);
    st.page = clampPage(start);
    st.open = true;
    st.loadToken++;

    el.rTitle.textContent = comic.title || "";
    el.rTitle.title = comic.path || comic.title || "";
    el.rBack.querySelector("span").textContent = opts.backLabel || "书架";
    el.rSlider.max = String(st.total - 1);
    el.rSlider.value = String(st.page);
    el.reader.hidden = false;
    document.body.style.overflow = "hidden";

    st.mode = localStorage.getItem("readerMode") === "scroll" ? "scroll" : "paged";
    applyMode();
    document.addEventListener("keydown", onKey);
    showUI();
    return true;
  }

  function close() {
    if (!st.open) return;
    flushSave();
    st.open = false;
    el.reader.hidden = true;
    el.reader.classList.remove("ui-hidden");
    document.body.style.overflow = "";
    document.removeEventListener("keydown", onKey);
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    el.rImg.removeAttribute("src");
    el.rScroll.innerHTML = "";
    st.comic = null;
    if (typeof Reader.onClose === "function") Reader.onClose();
  }

  /* ---------------- 渲染 ---------------- */
  function applyMode() {
    el.rModeLabel.textContent = st.mode === "paged" ? "翻页" : "滚动";
    el.rModeBtn.title = st.mode === "paged" ? "切换为连续滚动" : "切换为单页翻页";
    if (st.mode === "paged") {
      el.rScroll.hidden = true;
      el.rScroll.style.display = "none";
      el.rStage.style.display = "flex";
      showPage();
    } else {
      el.rStage.style.display = "none";
      el.rScroll.hidden = false;
      el.rScroll.style.display = "block";
      buildScroll();
    }
    syncMeta();
  }

  function syncMeta() {
    el.rPageLabel.textContent = `${st.page + 1} / ${st.total}`;
    el.rSlider.value = String(st.page);
    el.rPct.textContent = Math.round(((st.page + 1) / st.total) * 100) + "%";
    el.rPrevBtn.disabled = st.page <= 0;
    el.rNextBtn.disabled = st.page >= st.total - 1;
  }

  function showPage() {
    const token = ++st.loadToken;
    const url = pageUrl(st.page);
    el.rImg.classList.add("loading");
    el.rSpinner.classList.add("show");

    const probe = new Image();
    probe.onload = () => {
      if (token !== st.loadToken || !st.open) return;
      el.rImg.src = url;
      el.rImg.classList.remove("loading");
      el.rSpinner.classList.remove("show");
    };
    probe.onerror = () => {
      if (token !== st.loadToken) return;
      el.rSpinner.classList.remove("show");
      el.rImg.classList.remove("loading");
      window.toast?.("这一页加载失败了");
    };
    probe.src = url;

    preload(st.page + 1);
    preload(st.page - 1);
  }

  function preload(i) {
    if (i < 0 || i >= st.total) return;
    const im = new Image();
    im.src = pageUrl(i);
  }

  function buildScroll() {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < st.total; i++) {
      const img = document.createElement("img");
      img.loading = i > 2 ? "lazy" : "eager";
      img.decoding = "async";
      img.dataset.i = String(i);
      img.alt = "";
      img.src = pageUrl(i);
      frag.appendChild(img);
    }
    el.rScroll.innerHTML = "";
    el.rScroll.appendChild(frag);
    st.programmaticUntil = Date.now() + 400;
    requestAnimationFrame(() => {
      const node = el.rScroll.querySelector(`img[data-i="${st.page}"]`);
      if (node) node.scrollIntoView({ block: "start" });
    });
  }

  function onScroll() {
    if (st.mode !== "scroll" || !st.open) return;
    if (Date.now() < st.programmaticUntil) return;

    const rect = el.rScroll.getBoundingClientRect();
    const probeY = rect.top + rect.height * 0.35;
    const imgs = el.rScroll.children;
    let idx = 0;
    for (let i = 0; i < imgs.length; i++) {
      if (imgs[i].getBoundingClientRect().top <= probeY) idx = i;
    }
    if (idx !== st.page) {
      st.page = idx;
      syncMeta();
      scheduleSave();
    }
  }

  /* ---------------- 导航 ---------------- */
  function goTo(i, opts = {}) {
    if (!st.open) return;
    const target = clampPage(i);
    const changed = target !== st.page;
    st.page = target;
    syncMeta();

    if (st.mode === "paged") {
      showPage();
    } else {
      st.programmaticUntil = Date.now() + 300;
      const node = el.rScroll.querySelector(`img[data-i="${st.page}"]`);
      if (node) node.scrollIntoView({ block: "start" });
    }
    if (changed && !opts.silent) scheduleSave();
  }

  const next = () => goTo(st.page + 1);
  const prev = () => goTo(st.page - 1);

  function onKey(e) {
    if (!st.open) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;

    switch (e.key) {
      case "Escape": close(); e.preventDefault(); break;
      case "ArrowRight": next(); e.preventDefault(); break;
      case "ArrowLeft": prev(); e.preventDefault(); break;
      case "PageDown": next(); e.preventDefault(); break;
      case "PageUp": prev(); e.preventDefault(); break;
      case "Home": goTo(0); e.preventDefault(); break;
      case "End": goTo(st.total - 1); e.preventDefault(); break;
      case "f": case "F": toggleFull(); e.preventDefault(); break;
      case " ": if (st.mode === "paged") { next(); e.preventDefault(); } break;
      default: break;
    }
  }

  function toggleMode() {
    if (!st.open) return;
    st.mode = st.mode === "paged" ? "scroll" : "paged";
    localStorage.setItem("readerMode", st.mode);
    applyMode();
    showUI();
  }

  function toggleFull() {
    if (!document.fullscreenElement) {
      el.reader.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.().catch(() => {});
    }
  }

  function showUI() {
    if (!st.open) return;
    el.reader.classList.remove("ui-hidden");
    clearTimeout(st.uiTimer);
    st.uiTimer = setTimeout(() => {
      if (st.open) el.reader.classList.add("ui-hidden");
    }, 2600);
  }

  /* ---------------- 进度保存 ---------------- */
  function scheduleSave() {
    clearTimeout(st.saveTimer);
    st.saveTimer = setTimeout(flushSave, 900);
  }

  function flushSave() {
    clearTimeout(st.saveTimer);
    const comic = st.comic;
    if (!comic) return;
    const page = st.page;
    if (comic.lastPage === page) return;
    comic.lastPage = page;
    API.saveProgress(comic.id, page).catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  return { open, close, onClose: null, isOpen: () => st.open };
})();
