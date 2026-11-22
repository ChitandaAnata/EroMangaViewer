/* 系列：把同一作者 / 同一部作品的若干话串成一条可排序的线。
   数据存在每本作品自己身上（series + seriesOrder），也写进旁挂 json，
   所以整库搬迁、单独拷走一本，系列归属和顺序都不会丢。 */
const Series = (() => {
  const $ = (id) => document.getElementById(id);
  const el = {};
  const MAX_RENDER = 400;

  const st = {
    open: false,
    name: "",
    items: [],
    token: 0,
    dragId: "",
    pickTarget: null,   // 详情页里「加入现有系列」要加的那本
    nameOpts: null,     // 新建 / 重命名共用的输入框配置
    all: [],            // 添加作品的候选池
    chosen: new Set(),
    query: "",
    detailId: "",       // 从哪本作品的详情页点过来的，返回到它
    confirmRun: null,   // 确认弹窗里要执行的动作
  };

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (m) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  const fmtSize = (bytes) => {
    if (!bytes) return "—";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0, n = bytes;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  };

  const toast = (msg) => window.toast?.(msg);

  /* ---------------------------------------------------------------- 生命周期 */
  function boot() {
    ["seriesView", "seriesBack", "seriesTitle", "seriesSub", "seriesList",
      "seriesEmpty", "seriesAddBtn", "seriesMoreBtn", "seriesHint", "shelfView",
      "seriesNameModal", "seriesNameTitle", "seriesNameInput", "seriesNameHint", "seriesNameOk",
      "seriesPickModal", "seriesPickList", "seriesPickNew",
      "seriesAddModal", "seriesAddList", "seriesAddQuery", "seriesAddCount", "seriesAddOk",
      "seriesAddSub",
      "seriesConfirmModal", "seriesConfirmTitle", "seriesConfirmText", "seriesConfirmOk",
    ].forEach((id) => { el[id] = $(id); });
    if (!el.seriesView) return;

    el.seriesConfirmOk.addEventListener("click", () => {
      const run = st.confirmRun;
      el.seriesConfirmModal.hidden = true;
      st.confirmRun = null;
      if (typeof run === "function") run();
    });

    el.seriesBack.addEventListener("click", () => {
      if (typeof Series.onBack === "function") Series.onBack();
      else location.hash = "";
    });

    el.seriesAddBtn.addEventListener("click", openAddModal);
    el.seriesMoreBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openSeriesMenu(e.currentTarget);
    });

    el.seriesList.addEventListener("click", onListClick);
    bindDrag();

    // 新名 / 重命名共用的输入框
    el.seriesNameOk.addEventListener("click", submitName);
    el.seriesNameInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submitName(); }
    });

    el.seriesPickNew.addEventListener("click", () => {
      const target = st.pickTarget;
      el.seriesPickModal.hidden = true;
      openNameModal({
        title: "新建系列",
        ok: "创建并加入",
        hint: "创建后这本作品会成为系列的第一话，之后可以继续往里加。",
        async onSubmit(name) {
          await API.saveSeries(target.id, name);
          toast(`已加入系列「${name}」`);
          if (typeof Series.onComicChanged === "function") Series.onComicChanged(target.id);
        },
      });
    });

    el.seriesAddQuery.addEventListener("input", () => {
      st.query = el.seriesAddQuery.value.trim();
      renderAddList();
    });
    el.seriesAddList.addEventListener("change", (e) => {
      const box = e.target.closest("input[type=checkbox]");
      if (!box) return;
      const id = box.dataset.id;
      if (box.checked) st.chosen.add(id); else st.chosen.delete(id);
      updateAddCount();
    });
    el.seriesAddOk.addEventListener("click", submitAdd);
  }

  /* ---------------------------------------------------------------- 打开 / 关闭 */
  async function show(name) {
    if (!el.seriesView) return;
    const clean = String(name || "").trim();
    if (!clean) { hide(); return; }

    st.open = true;
    const first = !st.name;
    st.name = clean;
    if (el.shelfView) el.shelfView.hidden = true;
    el.seriesView.hidden = false;
    const main = document.querySelector(".main");
    if (main && first) main.scrollTop = 0;

    const token = ++st.token;
    if (!st.items.length || st.items[0].series !== clean) renderLoading();
    try {
      const res = await API.seriesGet(clean);
      if (token !== st.token || !st.open) return;
      st.name = (res && res.name) || clean;
      st.items = (res && res.items) || [];
      render();
    } catch (err) {
      if (token !== st.token) return;
      st.items = [];
      render();
      toast("打开系列失败：" + err.message);
    }
  }

  function hide() {
    if (!el.seriesView) return;
    const was = st.open;
    st.open = false;
    st.name = "";
    st.items = [];
    st.token++;
    el.seriesView.hidden = true;
    // 只有确实是系列页在挡着书架时才还回去；别的页面（详情 / 设置）自己会摆好
    if (was && el.shelfView) el.shelfView.hidden = false;
  }

  function renderLoading() {
    el.seriesTitle.textContent = st.name || "系列";
    el.seriesSub.textContent = "正在读取…";
    el.seriesEmpty.hidden = true;
    el.seriesList.innerHTML = Array.from({ length: 4 },
      () => `<div class="series-row skeleton-row"></div>`).join("");
  }

  /* ---------------------------------------------------------------- 渲染 */
  function render() {
    if (!st.open) return;
    el.seriesTitle.textContent = st.name;
    el.seriesTitle.title = st.name;
    const n = st.items.length;
    el.seriesSub.textContent = n ? `共 ${n} 部作品` : "还没有作品";
    el.seriesHint.hidden = n < 2;
    el.seriesEmpty.hidden = n > 0;
    if (!n) {
      el.seriesList.innerHTML = "";
      el.seriesEmpty.innerHTML = `
        <p>这个系列还没有作品。</p>
        <button class="btn primary" id="seriesEmptyAdd">＋ 添加作品</button>`;
      $("seriesEmptyAdd").addEventListener("click", openAddModal);
      return;
    }

    el.seriesList.innerHTML = st.items.map((c, i) => {
      const cat = window.ComicCat && ComicCat.has(c.category)
        ? `<span class="sr-cat" style="--cat:${ComicCat.color(c.category)}">${esc(ComicCat.label(c.category))}</span>`
        : "";
      const pct = c.pages > 0 && c.lastPage > 0
        ? Math.min(100, ((c.lastPage + 1) / c.pages) * 100) : 0;
      return `<article class="series-row" data-id="${c.id}" draggable="true">
        <span class="sr-index">${i + 1}</span>
        <span class="sr-grip" title="拖动调整顺序">
          <svg viewBox="0 0 24 24"><path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01"/></svg>
        </span>
        <img class="sr-cover" src="${c.cover}" alt="" loading="lazy" decoding="async"
             onerror="this.style.opacity='0'">
        <div class="sr-main">
          <h3 class="sr-title" title="${esc(c.title)}">${esc(c.title)}</h3>
          <div class="sr-meta">
            ${cat}
            <span>${c.pages || "?"} 页</span>
            <span class="sep">·</span>
            <span>${fmtSize(c.size)}</span>
            ${pct > 0 ? `<span class="sr-progress"><i style="width:${pct.toFixed(1)}%"></i></span>` : ""}
          </div>
        </div>
        <div class="sr-actions">
          <button class="sr-btn" data-move="-1" title="往上挪" ${i === 0 ? "disabled" : ""}>
            <svg viewBox="0 0 24 24"><path d="M6 14l6-6 6 6"/></svg>
          </button>
          <button class="sr-btn" data-move="1" title="往下挪" ${i === n - 1 ? "disabled" : ""}>
            <svg viewBox="0 0 24 24"><path d="M6 10l6 6 6-6"/></svg>
          </button>
          <button class="sr-btn danger" data-remove title="移出系列">
            <svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V5h6v2M7 7l1 12h8l1-12"/></svg>
          </button>
        </div>
      </article>`;
    }).join("");
  }

  /* ---------------------------------------------------------------- 排序 */
  function bindDrag() {
    const list = el.seriesList;

    list.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".series-row[data-id]");
      if (!row) return;
      st.dragId = row.dataset.id;
      row.classList.add("dragging");
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", st.dragId);
      } catch (_) { /* 某些环境不给写 */ }
    });

    list.addEventListener("dragend", () => {
      st.dragId = "";
      list.querySelectorAll(".series-row").forEach((r) =>
        r.classList.remove("dragging", "drop-before", "drop-after"));
    });

    list.addEventListener("dragover", (e) => {
      if (!st.dragId) return;
      const row = e.target.closest(".series-row[data-id]");
      if (!row || row.dataset.id === st.dragId) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const r = row.getBoundingClientRect();
      const after = e.clientY > r.top + r.height / 2;
      list.querySelectorAll(".series-row").forEach((x) => x.classList.remove("drop-before", "drop-after"));
      row.classList.add(after ? "drop-after" : "drop-before");
    });

    list.addEventListener("drop", (e) => {
      if (!st.dragId) return;
      const row = e.target.closest(".series-row[data-id]");
      if (!row) return;
      e.preventDefault();
      const r = row.getBoundingClientRect();
      const after = e.clientY > r.top + r.height / 2;
      moveTo(st.dragId, row.dataset.id, after);
    });
  }

  /* 把 dragId 插到 targetId 前面 / 后面 */
  function moveTo(dragId, targetId, after) {
    if (!dragId || dragId === targetId) return;
    const ids = st.items.map((c) => c.id);
    const from = ids.indexOf(dragId);
    if (from < 0) return;
    const [moved] = st.items.splice(from, 1);
    let to = st.items.findIndex((c) => c.id === targetId);
    if (to < 0) { st.items.splice(from, 0, moved); return; }
    if (after) to += 1;
    st.items.splice(to, 0, moved);
    render();
    saveOrder();
  }

  function onListClick(e) {
    const row = e.target.closest(".series-row[data-id]");
    if (!row) return;
    const id = row.dataset.id;

    const moveBtn = e.target.closest("[data-move]");
    if (moveBtn) {
      const dir = Number(moveBtn.dataset.move);
      const i = st.items.findIndex((c) => c.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= st.items.length) return;
      const [moved] = st.items.splice(i, 1);
      st.items.splice(j, 0, moved);
      render();
      saveOrder();
      return;
    }

    if (e.target.closest("[data-remove]")) {
      removeOne(id);
      return;
    }

    // 点行本身 = 进详情；记下来源，详情页的「返回」能回到这个系列
    if (typeof Series.onOpenComic === "function") Series.onOpenComic(id);
    else location.hash = "comic=" + id;
  }

  async function saveOrder() {
    const ids = st.items.map((c) => c.id);
    try {
      await API.seriesOrder(st.name, ids);
    } catch (err) {
      toast("保存顺序失败：" + err.message);
      show(st.name);   // 回到服务端的真实顺序
    }
  }

  async function removeOne(id) {
    const c = st.items.find((x) => x.id === id);
    try {
      await API.saveSeries(id, "");
      st.items = st.items.filter((x) => x.id !== id);
      render();
      toast(c ? `已把《${c.title}》移出系列` : "已移出系列");
      if (typeof Series.onComicChanged === "function") Series.onComicChanged(id);
    } catch (err) {
      toast("移出失败：" + err.message);
    }
  }

  /* ---------------------------------------------------------------- 系列自己的菜单 */
  function openSeriesMenu(anchor) {
    const r = anchor.getBoundingClientRect();
    if (typeof window.openMenu !== "function") return;
    window.openMenu(r.left, r.bottom + 6, [
      {
        label: "重命名系列",
        hint: st.name,
        run: () => openNameModal({
          title: "重命名系列",
          ok: "保存",
          initial: st.name,
          hint: "改名会同步到系列里的每一本作品。新名字已存在时两个系列会合并。",
          async onSubmit(name) {
            const res = await API.seriesRename(st.name, name);
            toast(`已重命名为「${res.to}」`);
            if (typeof Series.onRenamed === "function") Series.onRenamed(res.from, res.to);
            if (typeof Series.onOpenSeries === "function") Series.onOpenSeries(res.to);
            else show(res.to);
          },
        }),
      },
      { sep: true },
      {
        label: "解散系列",
        hint: "作品保留",
        run: () => confirmDissolve(),
      },
    ], st.name);
  }

  function confirmAction(title, text, okLabel, run) {
    el.seriesConfirmTitle.textContent = title;
    el.seriesConfirmText.textContent = text;
    el.seriesConfirmOk.textContent = okLabel || "确定";
    st.confirmRun = run;
    el.seriesConfirmModal.hidden = false;
  }

  async function confirmDissolve() {
    const n = st.items.length;
    if (!n) return;
    confirmAction(
      "解散系列",
      `解散「${st.name}」？${n} 部作品会被移出系列，作品本身、标签、阅读进度都不动。`,
      "解散",
      async () => {
        try {
          await API.seriesDelete(st.name);
          toast(`已解散系列「${st.name}」`);
          if (typeof Series.onDissolved === "function") Series.onDissolved(st.name);
          (Series.onBack || (() => { location.hash = ""; }))();
        } catch (err) {
          toast("解散失败：" + err.message);
        }
      },
    );
  }

  /* ---------------------------------------------------------------- 详情页用：系列菜单 */
  /* anchor 是那个按钮；onDone 在系列归属真的变了之后回调 */
  function menuFor(comic, anchor, onDone) {
    if (typeof window.openMenu !== "function") return;
    const r = anchor.getBoundingClientRect();
    const items = [
      {
        label: "新建系列…",
        run: () => openNameModal({
          title: "新建系列",
          ok: "创建并加入",
          initial: "",
          hint: "输入系列名（比如作品名 / 第几话），创建后这本会作为第一话加进去。",
          async onSubmit(name) {
            await API.saveSeries(comic.id, name);
            toast(`已加入系列「${name}」`);
            onDone && onDone();
          },
        }),
      },
      {
        label: "加入现有系列…",
        run: () => openPickModal(comic, onDone),
      },
    ];
    if (comic.series) {
      items.push({ sep: true }, {
        label: "移出系列",
        hint: comic.series,
        run: async () => {
          try {
            await API.saveSeries(comic.id, "");
            toast("已移出系列");
            onDone && onDone();
          } catch (err) {
            toast("移出失败：" + err.message);
          }
        },
      });
    }
    window.openMenu(r.left, r.bottom + 6, items, comic.title || "");
  }

  /* ---------------------------------------------------------------- 选一个已有系列 */
  async function openPickModal(comic, onDone) {
    st.pickTarget = comic;
    st._pickDone = onDone;
    el.seriesPickList.innerHTML = `<p class="field-hint">正在读取…</p>`;
    el.seriesPickModal.hidden = false;
    try {
      const res = await API.seriesList();
      const list = (res && res.items) || [];
      if (!list.length) {
        el.seriesPickList.innerHTML = `<p class="field-hint">还没有任何系列，先点「新建系列」。</p>`;
        return;
      }
      el.seriesPickList.innerHTML = list.map((s) => `
        <button class="pick-row" type="button" data-name="${esc(s.name)}">
          <span class="pick-cover">
            ${s.cover ? `<img src="${s.cover}" alt="" loading="lazy">` : ""}
          </span>
          <span class="pick-main">
            <b>${esc(s.name)}</b>
            <em>${s.count} 部</em>
          </span>
          <svg class="set-chev" viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg>
        </button>`).join("");
      el.seriesPickList.querySelectorAll("[data-name]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const name = btn.dataset.name;
          const target = st.pickTarget;
          el.seriesPickModal.hidden = true;
          if (!target) return;
          if (target.series === name) { toast("已经在「" + name + "」里了"); return; }
          try {
            await API.saveSeries(target.id, name);
            toast(`已加入系列「${name}」`);
            const cb = st._pickDone;
            cb && cb();
          } catch (err) {
            toast("加入失败：" + err.message);
          }
        });
      });
    } catch (err) {
      el.seriesPickList.innerHTML = `<p class="field-hint">读取系列失败：${esc(err.message)}</p>`;
    }
  }

  /* ---------------------------------------------------------------- 新建 / 重命名输入框 */
  function openNameModal(opts) {
    st.nameOpts = opts;
    el.seriesNameTitle.textContent = opts.title || "新建系列";
    el.seriesNameOk.textContent = opts.ok || "创建";
    el.seriesNameHint.textContent = opts.hint || "";
    el.seriesNameInput.value = opts.initial || "";
    el.seriesNameModal.hidden = false;
    setTimeout(() => { el.seriesNameInput.focus(); el.seriesNameInput.select(); }, 30);
  }

  async function submitName() {
    const opts = st.nameOpts;
    const name = el.seriesNameInput.value.trim().replace(/\s+/g, " ");
    if (!name) {
      el.seriesNameInput.focus();
      return;
    }
    if (!opts) return;
    el.seriesNameOk.disabled = true;
    try {
      await opts.onSubmit(name);
      el.seriesNameModal.hidden = true;
    } catch (err) {
      toast("保存失败：" + err.message);
    } finally {
      el.seriesNameOk.disabled = false;
    }
  }

  /* ---------------------------------------------------------------- 添加作品 */
  async function openAddModal() {
    st.all = [];
    st.chosen = new Set();
    st.query = "";
    el.seriesAddQuery.value = "";
    el.seriesAddSub.textContent = `勾选后加到这个系列末尾，顺序之后可以拖`;
    el.seriesAddList.innerHTML = `<p class="field-hint">正在读取库…</p>`;
    updateAddCount();
    el.seriesAddModal.hidden = false;
    try {
      const res = await API.list({ sort: "title" });
      st.all = (res && res.items) || [];
      renderAddList();
    } catch (err) {
      el.seriesAddList.innerHTML = `<p class="field-hint">读取失败：${esc(err.message)}</p>`;
    }
  }

  function renderAddList() {
    const have = new Set(st.items.map((c) => c.id));
    const q = st.query.toLowerCase();
    const pool = st.all.filter((c) => !have.has(c.id)
      && (!q || String(c.title || "").toLowerCase().includes(q)
        || String(c.path || "").toLowerCase().includes(q)));
    if (!pool.length) {
      el.seriesAddList.innerHTML = `<p class="field-hint">${
        st.all.length ? "没有可添加的作品了（都已在系列里或搜索无结果）" : "库里还没有作品"}</p>`;
      return;
    }
    const shown = pool.slice(0, MAX_RENDER);
    el.seriesAddList.innerHTML = shown.map((c) => `
      <label class="add-row">
        <input type="checkbox" data-id="${c.id}" ${st.chosen.has(c.id) ? "checked" : ""}>
        <span class="add-cover"><img src="${c.cover}" alt="" loading="lazy" onerror="this.style.opacity='0'"></span>
        <span class="add-main">
          <b title="${esc(c.title)}">${esc(c.title)}</b>
          <em>${c.pages || "?"} 页 · ${fmtSize(c.size)}${c.series ? ` · 已在「${esc(c.series)}」` : ""}</em>
        </span>
      </label>`).join("") + (pool.length > shown.length
        ? `<p class="field-hint">只显示前 ${shown.length} 部，用搜索缩小范围。</p>` : "");
  }

  function updateAddCount() {
    const n = st.chosen.size;
    el.seriesAddCount.textContent = `已选 ${n} 部`;
    el.seriesAddOk.disabled = !n;
    el.seriesAddOk.textContent = n ? `添加 ${n} 部` : "添加";
  }

  async function submitAdd() {
    const ids = [...st.chosen];
    if (!ids.length) return;
    el.seriesAddOk.disabled = true;
    let ok = 0;
    for (const id of ids) {
      try {
        await API.saveSeries(id, st.name);
        ok++;
      } catch (_) { /* 单本失败不影响其它的 */ }
    }
    el.seriesAddModal.hidden = true;
    toast(ok === ids.length ? `已添加 ${ok} 部` : `添加了 ${ok} 部，${ids.length - ok} 部失败`);
    if (ok && typeof Series.onComicChanged === "function") {
      for (const id of ids) Series.onComicChanged(id);
    }
    show(st.name);
  }

  /* ---------------------------------------------------------------- 对外 */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  return {
    show,
    hide,
    refresh: () => show(st.name),
    isOpen: () => st.open,
    current: () => st.name,
    menuFor,
    openNameModal,
    onBack: null,
    onOpenComic: null,
    onOpenSeries: null,
    onComicChanged: null,
    onRenamed: null,
    onDissolved: null,
  };
})();
