/* 漫画详情页：封面 + 元信息 + 标签 + 分页页面预览 */
const Detail = (() => {
  const $ = (id) => document.getElementById(id);
  const el = {};
  const PER_PAGE = 24;

  const st = {
    id: "",
    comic: null,
    open: false,
    editing: false,
    page: 0,
    token: 0,
    savedScroll: 0,
  };

  /* ---------------------------------------------------------------- 工具 */
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (m) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  const safeColor = (c) => (/^#[0-9a-f]{3,8}$/i.test(c || "") ? c : "#8b93a7");

  const baseName = (p) => String(p || "").split(/[\\/]/).pop() || "—";

  /* ---------------------------------------------------------------- 生命周期 */
  function boot() {
    ["detail", "detailBack", "detailCrumb", "detailCover", "detailCoverImg",
      "detailTitle", "detailTags", "detailError", "detailCat", "detailStatus",
      "detailSeries",
      "detailFilename", "detailFileExt", "detailFileHint", "detailRenameBtn",
      "detailProgress", "detailProgressText", "detailProgressFill",
      "detailRead", "detailReadLabel", "detailRestart", "detailFav",
      "detailEdit", "detailEditLabel",
      "pagesCount", "pagesPager", "pageGrid", "pagesEmpty", "shelfView",
    ].forEach((id) => { el[id] = $(id); });
    if (!el.detail) return;

    // 作品分类下拉：固定六个分类 + 「未分类」，放在标题和标签之间
    el.detailCat.innerHTML = `<option value="">未分类</option>` +
      ComicCat.LIST.map((c) => `<option value="${c.key}">${c.label}</option>`).join("");
    el.detailCat.addEventListener("change", saveCategory);
    el.detailStatus.addEventListener("change", saveStatus);
    el.detailFav.addEventListener("click", toggleFavorite);
    el.detailRenameBtn.addEventListener("click", saveFilename);
    el.detailFilename.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); saveFilename(); }
    });

    el.detailBack.addEventListener("click", () => {
      if (typeof Detail.onBack === "function") Detail.onBack();
      else location.hash = "";
    });

    // 编辑模式：默认关闭，页面上只剩「看」和「读」
    el.detailEdit.addEventListener("click", () => setEditing(!st.editing));

    el.detailCover.addEventListener("click", () => readFrom("resume"));
    el.detailRead.addEventListener("click", () => readFrom("resume"));
    el.detailRestart.addEventListener("click", resetProgress);

    el.detailTags.addEventListener("click", (e) => {
      const add = e.target.closest("#dAddTag");
      if (add) {
        if (st.comic && typeof Detail.onEditTags === "function") Detail.onEditTags(st.comic);
        return;
      }
      const chip = e.target.closest("[data-tag]");
      if (chip && typeof Detail.onFilterTag === "function") Detail.onFilterTag(chip.dataset.tag);
    });

    // 系列：点名字进系列页；编辑模式下「编辑系列」弹菜单（新建 / 加入现有 / 移出）
    el.detailSeries.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-series]");
      if (chip) {
        if (typeof Detail.onOpenSeries === "function") Detail.onOpenSeries(chip.dataset.series);
        return;
      }
      const btn = e.target.closest("#dSeriesBtn");
      if (btn && st.comic) {
        e.stopPropagation();   // 别让这次点击把刚弹出的菜单立刻关掉
        if (typeof Series !== "undefined" && Series.menuFor) {
          Series.menuFor(st.comic, btn, () => refresh());
        }
      }
    });

    el.pagesPager.addEventListener("click", (e) => {
      const b = e.target.closest("[data-pg]");
      if (!b || b.disabled) return;
      const n = Number(b.dataset.pg);
      if (!Number.isFinite(n)) return;
      st.page = n;
      renderPages();
      el.detail.querySelector(".pages-panel")?.scrollIntoView({ block: "start", behavior: "smooth" });
    });

    el.pageGrid.addEventListener("click", (e) => {
      const cell = e.target.closest("[data-page]");
      if (!cell || !st.comic) return;
      const index = Number(cell.dataset.page);
      if (!Number.isFinite(index)) return;
      if (typeof Detail.onRead === "function") Detail.onRead(st.comic, index);
    });
  }

  /* ---------------------------------------------------------------- 编辑模式 */
  // 关：分类下拉、更新状态、＋编辑标签等编辑入口都不露。
  // 重置阅读进度不在这里：它只跟「有没有进度」有关，只读态也照常显示。
  function setEditing(on) {
    st.editing = !!on;
    el.detail.classList.toggle("editing", st.editing);
    el.detailEdit.setAttribute("aria-pressed", st.editing ? "true" : "false");
    el.detailEdit.title = st.editing
      ? "关掉后隐藏分类、标签等编辑入口"
      : "打开后可以改作品分类和标签";
    if (el.detailEditLabel) {
      el.detailEditLabel.textContent = st.editing ? "完成编辑" : "编辑模式";
    }
    // 分类胶囊只读态也照常显示，只是不能点开
    if (el.detailCat) el.detailCat.disabled = !st.editing;
    if (el.detailStatus) el.detailStatus.disabled = !st.editing;
    // 只读态连「＋ 编辑标签」那一行都不渲染
    if (st.comic) { renderTags(); renderSeries(); }
  }

  /* ---------------------------------------------------------------- 打开 / 关闭 */
  async function open(id) {
    if (st.open && st.id === id) {
      await refresh();
      return;
    }
    if (!id) return;

    st.open = true;
    st.id = id;
    st.comic = null;
    st.page = 0;
    setEditing(false);
    const token = ++st.token;

    const main = $("main");
    if (main) {
      st.savedScroll = main.scrollTop;
      main.scrollTop = 0;
    }
    el.shelfView.hidden = true;
    el.detail.hidden = false;
    renderLoading();

    try {
      const comic = await API.comic(id);
      if (token !== st.token || !st.open) return;
      st.comic = comic;
      render();
    } catch (err) {
      if (token !== st.token) return;
      window.toast?.("打开详情失败：" + err.message);
      close();
      if (location.hash) location.hash = "";
      // 文件可能已经被删掉/移走了，顺手把书架刷成真实状态
      window.refreshAll?.();
    }
  }

  function close() {
    if (!st.open) return;
    st.open = false;
    st.id = "";
    st.comic = null;
    st.token++;
    setEditing(false);
    el.detail.hidden = true;
    el.shelfView.hidden = false;
    const main = $("main");
    if (main) main.scrollTop = st.savedScroll;
  }

  async function refresh() {
    if (!st.open || !st.id) return;
    const token = ++st.token;
    try {
      const comic = await API.comic(st.id);
      if (token !== st.token || !st.open) return;
      st.comic = comic;
      render();
    } catch (_) { /* 网络抖动时保持当前内容 */ }
  }

  function readFrom(where) {
    if (!st.comic) return;
    if (!st.comic.pages) {
      window.toast?.(st.comic.error || "这本漫画暂时打不开");
      return;
    }
    const page = where === "start" ? 0 : Math.min(st.comic.pages - 1, st.comic.lastPage || 0);
    if (typeof Detail.onRead === "function") Detail.onRead(st.comic, page);
  }

  /* 重置阅读进度：清掉进度，但留在详情页（想读点「开始阅读」） */
  async function resetProgress() {
    if (!st.comic) return;
    const id = st.comic.id;
    el.detailRestart.disabled = true;
    try {
      await API.saveProgress(id, 0);
      if (!st.comic || st.comic.id !== id) return;
      st.comic.lastPage = 0;
      renderProgress();
      if (typeof Detail.onProgress === "function") Detail.onProgress(st.comic, 0);
      window.toast?.("阅读进度已重置");
    } catch (err) {
      window.toast?.("重置失败：" + err.message);
    } finally {
      el.detailRestart.disabled = false;
    }
  }

  /* ---------------------------------------------------------------- 渲染 */
  function renderLoading() {
    el.detailTitle.textContent = "正在加载…";
    el.detailCrumb.textContent = "";
    el.detailCoverImg.removeAttribute("src");
    el.detailTags.innerHTML = "";
    el.detailError.hidden = true;
    el.detailProgress.hidden = true;
    el.detailRead.disabled = true;
    el.detailReadLabel.textContent = "开始阅读";
    el.detailRestart.hidden = true;
    el.detailCat.value = "";
    el.detailCat.disabled = true;
    if (el.detailSeries) {
      el.detailSeries.innerHTML = "";
      el.detailSeries.hidden = true;
    }
    el.detailStatus.value = "";
    el.detailStatus.disabled = true;
    el.detailFav.setAttribute("aria-pressed", "false");
    el.detailFav.title = "标记为喜爱";
    el.detailFilename.value = "";
    el.detailFileExt.textContent = "";
    el.pagesCount.textContent = "正在读取…";
    el.pagesPager.innerHTML = "";
    el.pageGrid.innerHTML = Array.from({ length: 12 },
      () => `<div class="page-cell skeleton-cell"></div>`).join("");
    el.pagesEmpty.hidden = true;
  }

  function render() {
    const c = st.comic;
    if (!c) return;

    el.detailTitle.textContent = c.title || baseName(c.path);
    el.detailTitle.title = c.title || "";
    el.detailCrumb.textContent = c.path || "";

    if (c.cover) el.detailCoverImg.src = c.cover;
    else el.detailCoverImg.removeAttribute("src");

    renderCat();
    renderStatus();
    renderFav();
    renderFile();
    renderTags();
    renderSeries();

    if (c.error) {
      el.detailError.hidden = false;
      el.detailError.textContent = c.error;
    } else {
      el.detailError.hidden = true;
    }

    renderProgress();
    renderPages();
  }

  /* 阅读进度条、主按钮文案、重置进度按钮的显隐都只跟进度有关 */
  function renderProgress() {
    const c = st.comic;
    if (!c) return;
    const hasProgress = c.pages > 0 && c.lastPage > 0;
    const pct = hasProgress ? Math.min(100, ((c.lastPage + 1) / c.pages) * 100) : 0;

    el.detailProgress.hidden = !hasProgress;
    el.detailProgressFill.style.width = pct.toFixed(1) + "%";
    el.detailProgressText.textContent = c.pages
      ? `第 ${Math.min(c.lastPage + 1, c.pages)} / ${c.pages} 页 · ${Math.round(pct)}%`
      : "—";

    // 没读过就没有「重置」可言
    el.detailRestart.hidden = !hasProgress;

    el.detailRead.disabled = !c.pages;
    el.detailReadLabel.textContent = !c.pages
      ? "无法阅读"
      : (hasProgress ? `继续阅读 · 第 ${Math.min(c.lastPage + 1, c.pages)} 页` : "开始阅读");
  }

  /* 作品分类：标题和标签之间的一枚下拉，改完立刻存库并同步旁挂文件 */
  function renderCat() {
    const key = ComicCat.norm(st.comic && st.comic.category);
    el.detailCat.value = key;
    el.detailCat.dataset.cat = key || "none";
    el.detailCat.style.setProperty("--cat", key ? ComicCat.color(key) : "var(--muted)");
    el.detailCat.title = key ? `作品分类：${ComicCat.label(key)}` : "还没分类，选一个";
    el.detailCat.disabled = !st.editing;
  }

  async function saveCategory() {
    if (!st.comic) return;
    const id = st.comic.id;
    const value = el.detailCat.value;
    el.detailCat.disabled = true;
    try {
      const res = await API.saveCategory(id, value);
      const saved = (res && res.comic && res.comic.category) || "";
      if (st.comic && st.comic.id === id) {
        st.comic.category = saved;
        renderCat();
        if (typeof Detail.onCategory === "function") Detail.onCategory(st.comic);
      }
      window.toast?.(saved ? `分类：${ComicCat.label(saved)}` : "已设为未分类");
    } catch (err) {
      window.toast?.("保存分类失败：" + err.message);
      renderCat();
    } finally {
      el.detailCat.disabled = !st.editing;
    }
  }

  /* 更新状态：紧跟作品名，标成「更新中」后会出现在侧栏对应列表 */
  function renderStatus() {
    const key = st.comic && st.comic.status === "updating" ? "updating" : "";
    el.detailStatus.value = key;
    el.detailStatus.dataset.status = key || "done";
    el.detailStatus.title = key
      ? "当前：更新中（作品会出现在侧栏「更新中」）"
      : "当前：更新完毕";
    el.detailStatus.disabled = !st.editing;
  }

  async function saveStatus() {
    if (!st.comic) return;
    const id = st.comic.id;
    const value = el.detailStatus.value;
    el.detailStatus.disabled = true;
    try {
      const res = await API.saveStatus(id, value);
      const saved = (res && res.comic && res.comic.status) || "";
      if (st.comic && st.comic.id === id) {
        st.comic.status = saved;
        renderStatus();
        if (typeof Detail.onStatus === "function") Detail.onStatus(st.comic);
      }
      window.toast?.(saved === "updating" ? "已标为更新中" : "已标为更新完毕");
    } catch (err) {
      window.toast?.("保存更新状态失败：" + err.message);
      renderStatus();
    } finally {
      el.detailStatus.disabled = !st.editing;
    }
  }

  /* 喜爱：常驻按钮，跟编辑模式无关；按下即进侧栏 Favorite */
  function renderFav() {
    const on = !!(st.comic && st.comic.favorite);
    el.detailFav.setAttribute("aria-pressed", on ? "true" : "false");
    el.detailFav.title = on ? "取消喜爱" : "标记为喜爱";
  }

  async function toggleFavorite() {
    if (!st.comic) return;
    const id = st.comic.id;
    const value = !st.comic.favorite;
    el.detailFav.disabled = true;
    try {
      const res = await API.saveFavorite(id, value);
      const saved = !!(res && res.comic && res.comic.favorite);
      if (st.comic && st.comic.id === id) {
        st.comic.favorite = saved;
        renderFav();
        if (typeof Detail.onFavorite === "function") Detail.onFavorite(st.comic);
      }
      window.toast?.(saved ? "已加入 Favorite" : "已取消喜爱");
    } catch (err) {
      window.toast?.("标记失败：" + err.message);
    } finally {
      el.detailFav.disabled = false;
    }
  }

  /* 文件名编辑：输入框只放主名，扩展名固定显示，保存时交给后端做原子改名 */
  function renderFile() {
    const c = st.comic;
    if (!c) return;
    const name = baseName(c.path);
    const dot = name.lastIndexOf(".");
    el.detailFilename.value = dot > 0 ? name.slice(0, dot) : name;
    el.detailFileExt.textContent = dot > 0 ? name.slice(dot) : "";
  }

  async function saveFilename() {
    if (!st.comic) return;
    const filename = el.detailFilename.value.trim();
    if (!filename) {
      window.toast?.("文件名不能为空");
      el.detailFilename.focus();
      return;
    }
    const id = st.comic.id;
    el.detailRenameBtn.disabled = true;
    el.detailFilename.disabled = true;
    try {
      const res = await API.renameFile(id, filename);
      if (st.comic && st.comic.id === id && res && res.comic) {
        st.comic = res.comic;
        render();
        if (typeof Detail.onRename === "function") Detail.onRename(st.comic);
      }
      window.toast?.((res && res.unchanged) ? "文件名没有变化" : "已改名，源文件和书签文件已同步");
    } catch (err) {
      window.toast?.("改名失败：" + err.message);
    } finally {
      el.detailRenameBtn.disabled = false;
      el.detailFilename.disabled = false;
    }
  }

  /* 标签按分类成行：语言 / 原作 / 角色 / 艺术家 / 社团 / 女性 / 男性 / 混合 */
  function renderTags() {
    const tags = (st.comic && st.comic.tags) || [];
    const groups = TagNS.group(tags);

    if (!groups.length) {
      // 只读态只说明现状，不劝人去编辑
      el.detailTags.innerHTML = st.editing
        ? `<div class="tag-empty">
          <span>还没有标签，可以补上语言、原作、角色、社团等信息</span>
          <button class="d-tag d-tag-add" id="dAddTag">+ 添加标签</button>
        </div>`
        : `<div class="tag-empty"><span>还没有标签</span></div>`;
      return;
    }

    const rows = groups.map((g) => `
      <div class="tag-line">
        <span class="tag-line-label">${esc(g.label)}</span>
        <div class="tag-line-items">
          ${g.tags.map((t) => `<button class="d-tag" data-tag="${esc(t.name)}" style="--tag:${safeColor(t.color)}" title="${esc(t.name)}">${esc(TagNS.label(t))}</button>`).join("")}
        </div>
      </div>`).join("");

    el.detailTags.innerHTML = rows + (st.editing ? `
      <div class="tag-line">
        <span class="tag-line-label"></span>
        <div class="tag-line-items">
          <button class="d-tag d-tag-add" id="dAddTag">+ 编辑标签</button>
        </div>
      </div>` : "");
  }

  /* 系列：同一部作品的若干话串成一条线。
     只读态只在「已经在某个系列里」时显示一行，点名字进系列页；
     编辑模式多一个「编辑系列」入口（新建 / 加入现有 / 移出）。 */
  function renderSeries() {
    const c = st.comic;
    if (!c) { el.detailSeries.innerHTML = ""; return; }
    const name = String(c.series || "");
    const order = Number(c.seriesOrder || 0);

    if (!name && !st.editing) {
      el.detailSeries.innerHTML = "";
      el.detailSeries.hidden = true;
      return;
    }
    el.detailSeries.hidden = false;

    const chip = name
      ? `<button class="d-series" data-series="${esc(name)}" title="查看系列「${esc(name)}」">
           <svg viewBox="0 0 24 24"><path d="M4 5.5h7v13H4zM13 5.5h7v13h-7z"/></svg>
           <span>${esc(name)}</span>
           ${order > 0 ? `<em>第 ${order} 话</em>` : ""}
         </button>`
      : "";

    const editBtn = st.editing
      ? `<button class="d-series-edit" id="dSeriesBtn" type="button">${name ? "编辑系列" : "＋ 加入系列"}</button>`
      : "";

    el.detailSeries.innerHTML = `
      <div class="series-line">
        <span class="series-line-label">系列</span>
        <div class="series-line-items">
          ${chip}
          ${!name && st.editing ? `<span class="series-empty-hint">还没归到任何系列</span>` : ""}
          ${editBtn}
        </div>
      </div>`;
  }

  function pageList(cur, total) {
    if (total <= 9) return Array.from({ length: total }, (_, i) => i);
    const set = new Set([0, total - 1, cur - 1, cur, cur + 1]);
    if (cur < 3) [1, 2, 3].forEach((n) => set.add(n));
    if (cur > total - 4) [total - 2, total - 3, total - 4].forEach((n) => set.add(n));
    const nums = [...set].filter((n) => n >= 0 && n < total).sort((a, b) => a - b);
    const out = [];
    let prev = -2;
    for (const n of nums) {
      if (n - prev > 1) out.push("…");
      out.push(n);
      prev = n;
    }
    return out;
  }

  function renderPages() {
    const c = st.comic;
    if (!c) return;
    const total = c.pages || 0;

    if (!total) {
      el.pageGrid.innerHTML = "";
      el.pagesEmpty.hidden = false;
      el.pagesEmpty.textContent = c.error ? "无法生成页面预览：" + c.error : "这本漫画没有可预览的页面。";
      el.pagesCount.textContent = "共 0 页";
      el.pagesPager.innerHTML = "";
      return;
    }
    el.pagesEmpty.hidden = true;

    const totalChunks = Math.max(1, Math.ceil(total / PER_PAGE));
    if (st.page >= totalChunks) st.page = totalChunks - 1;
    if (st.page < 0) st.page = 0;

    const start = st.page * PER_PAGE;
    const end = Math.min(total, start + PER_PAGE);
    el.pagesCount.textContent = `第 ${start + 1}–${end} 页 · 共 ${total} 页`;

    const ver = `${Math.round(c.mtime || 0)}-${c.size || 0}`;
    let html = "";
    for (let i = start; i < end; i++) {
      html += `<button class="page-cell" type="button" data-page="${i}" title="从第 ${i + 1} 页开始阅读">
        <img src="/api/comics/${c.id}/thumb/${i}?v=${ver}" alt="第 ${i + 1} 页" loading="lazy" decoding="async">
        <span class="page-no">${i + 1}</span>
        <span class="page-open">
          <svg viewBox="0 0 24 24"><path d="M8 5.5 18.5 12 8 18.5z"/></svg>
        </span>
      </button>`;
    }
    el.pageGrid.innerHTML = html;

    renderPager(totalChunks);
  }

  function renderPager(total) {
    if (total <= 1) {
      el.pagesPager.innerHTML = "";
      return;
    }
    const btn = (label, target, opts = {}) =>
      `<button class="pg-btn${opts.active ? " active" : ""}${opts.dots ? " dots" : ""}"
        ${opts.disabled ? "disabled" : ""} data-pg="${target}">${label}</button>`;

    let html = btn("‹", st.page - 1, { disabled: st.page <= 0 });
    for (const n of pageList(st.page, total)) {
      if (n === "…") html += `<span class="pg-dots">…</span>`;
      else html += btn(String(n + 1), n, { active: n === st.page });
    }
    html += btn("›", st.page + 1, { disabled: st.page >= total - 1 });
    el.pagesPager.innerHTML = html;
  }

  /* ---------------------------------------------------------------- 对外 */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  return {
    open,
    close,
    refresh,
    isOpen: () => st.open,
    isEditing: () => st.editing,
    currentId: () => st.id,
    current: () => st.comic,
    onRead: null,
    onEditTags: null,
    onFilterTag: null,
    onOpenSeries: null,
    onCategory: null,
    onStatus: null,
    onFavorite: null,
    onProgress: null,
    onRename: null,
    onBack: null,
  };
})();
