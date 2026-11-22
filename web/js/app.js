/* 书架主程序 */
(() => {
  const $ = (id) => document.getElementById(id);
  const shelf = $("shelf");
  const emptyBox = $("empty");

  const S = {
    comics: [],
    allComics: [],
    tags: [],
    stats: { total: 0 },
    tagSel: [],      // 已勾选的标签（多选，取交集）
    collapsedNs: new Set(), // 侧栏里折叠起来的标签分类
    searchHits: [],  // 顶部搜索框当前的标签联想结果
    searchPick: -1,  // 键盘选中的联想项
    mode: "",        // "" = 全部 / __untagged__ = 未进行标记 / __updating__ = 更新中 / __favorite__ = Favorite
    q: "",
    sort: "recent",
    view: "grid",
    loading: true,
    scanning: false,
    wasScanning: false,
    libraryExists: true,
    library: "",
    settings: null,
    sidecar: true,
    tagFile: null,
    tagTarget: null,
    editingTags: [],
    tagQuery: "",       // 标签管理页的搜索词
    settingsOrigin: "", // 打开设置前所在的 hash，关闭时回到这里
    newTagNs: "",
    newTagFrom: false,
    pollTimer: null,
    readerOrigin: "",
    hist: [],        // 后退栈：每项是「离开时那一屏」的快照（见 navSnapshot）
  };

  /* ---------------------------------------------------------------- 工具 */
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (m) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  const safeColor = (c) => (/^#[0-9a-f]{3,8}$/i.test(c || "") ? c : "#8b93a7");

  const fmtSize = (bytes) => {
    if (!bytes) return "—";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0, n = bytes;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  };

  const shortenPath = (p, max = 40) => {
    if (!p) return "—";
    if (p.length <= max) return p;
    return "…" + p.slice(-max);
  };

  // 只保留末尾几段路径，侧栏空间有限
  const tailPath = (p, n = 2) => {
    if (!p) return "—";
    const parts = p.split(/[\\/]+/).filter(Boolean);
    if (parts.length <= n) return p;
    return "…\\" + parts.slice(-n).join("\\");
  };

  let toastTimer = null;
  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add("show"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => { el.hidden = true; }, 260);
    }, 2400);
  }
  window.toast = toast;

  function debounce(fn, ms) {
    let t = null;
    const run = (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
    run.cancel = () => { clearTimeout(t); t = null; };
    return run;
  }

  /* 顶部搜索：书名继续走后端 q，标签则先弹联想，选中后按标签筛选 */
  const commitSearch = debounce((value) => {
    pushNav();
    S.q = String(value || "").trim();
    if (Detail.isOpen()) go("");
    loadComics();
  }, 260);

  /* ---------------------------------------------------------- 后退栈
     像资源管理器的「后退一步」：记下离开时那一屏（详情页 / 栏目 / 勾选的标签 /
     搜索词 / 滚动位置），后退就整屏还原。
     典型场景：详情页点一个标签 → 跳到该标签的筛选结果 → 后退回到那本作品的详情页。 */
  const NAV_MAX = 40;
  let navRestoring = false;

  function navSnapshot() {
    const main = document.querySelector(".main");
    return {
      detail: Detail.isOpen() ? Detail.currentId() : "",
      series: (typeof Series !== "undefined" && Series.isOpen()) ? Series.current() : "",
      mode: S.mode,
      tags: [...S.tagSel],
      q: S.q,
      sort: S.sort,
      scroll: main ? main.scrollTop : 0,
    };
  }

  /* 「同一个地方」：详情页、栏目、排序、标签组合都相同。搜索词不算——
     边打字边筛不该各记一条，否则后退要按十几次。 */
  function samePlace(a, b) {
    return a.detail === b.detail
      && (a.series || "") === (b.series || "")
      && a.mode === b.mode
      && a.sort === b.sort
      && a.tags.join("\u0001") === b.tags.join("\u0001");
  }

  function sameSnapshot(a, b) {
    return a.q === b.q && samePlace(a, b);
  }

  /* 每次跳转前调用：把「现在这一屏」压栈 */
  function pushNav() {
    if (navRestoring) return;
    const cur = navSnapshot();
    const top = S.hist[S.hist.length - 1];
    if (top && samePlace(top, cur)) return;
    S.hist.push(cur);
    if (S.hist.length > NAV_MAX) S.hist.shift();
    renderHeader();
  }

  /* 后退一步。没有历史（比如直接用 #comic=… 打开）时退回书架 */
  async function navBack() {
    if (!S.hist.length) {
      go("");
      return;
    }
    const sn = S.hist.pop();
    navRestoring = true;
    try {
      commitSearch.cancel();
      hideSearchSuggest();
      S.q = sn.q;
      S.mode = sn.mode;
      S.tagSel = [...sn.tags];
      S.sort = sn.sort;
      const inp = $("searchInput");
      if (inp) inp.value = sn.q;
      const sel = $("sortSelect");
      if (sel) sel.value = sn.sort;
      renderTags();
      if (sn.detail) go("comic=" + sn.detail);
      else if (sn.series) go("series=" + encodeURIComponent(sn.series));
      else go("");
      await loadComics();
      // 书架才需要找回原来的滚动位置；详情页自己从顶部开始
      if (!sn.detail && sn.scroll) {
        const main = document.querySelector(".main");
        if (main) main.scrollTop = sn.scroll;
      }
    } finally {
      navRestoring = false;
      renderHeader();
    }
  }

  /* 后退按钮上的提示：下一站去哪 */
  function backHint(sn) {
    if (sn.detail) {
      const hit = [...(S.allComics || []), ...(S.comics || [])].find((c) => c.id === sn.detail);
      return hit ? hit.title : "上一本作品";
    }
    if (sn.series) return `系列「${sn.series}」`;
    if (sn.tags.length) {
      const names = sn.tags.map((n) => {
        const t = (S.tags || []).find((x) => x.name === n);
        return t ? TagNS.label(t) : n;
      });
      return names.join(" + ");
    }
    if (sn.q) return `搜索「${sn.q}」`;
    return MODE_LABEL[sn.mode] || "全部作品";
  }

  /* ---------------------------------------------------------------- 主题 */
  function applyTheme(theme) {
    document.body.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === "light" ? "#f4f5f9" : "#0b0d12";
    localStorage.setItem("theme", theme);
    document.querySelectorAll("#themeSeg button").forEach((b) => {
      b.classList.toggle("active", b.dataset.themeVal === theme);
    });
    const label = $("setThemeValue");
    if (label) label.textContent = theme === "light" ? "浅色" : "深色";
  }

  /* ---------------------------------------------------------------- 渲染 */
  function cardHTML(c, idx) {
    const tags = (c.tags || []).slice(0, 3).map((t) =>
      `<span class="mini-tag" style="color:${safeColor(t.color)}" title="${esc(t.name)}">${esc(TagNS.label(t))}</span>`).join("");

    const pct = c.pages > 0 && c.lastPage > 0
      ? Math.min(100, ((c.lastPage + 1) / c.pages) * 100) : 0;

    const progress = pct > 0
      ? `<div class="card-progress" style="width:${pct.toFixed(1)}%"></div>` : "";

    const err = c.error ? `<div class="card-err">${esc(c.error)}</div>` : "";
    const cat = ComicCat.has(c.category)
      ? `<span class="card-cat" style="--cat:${ComicCat.color(c.category)}">${esc(ComicCat.label(c.category))}</span>`
      : "";

    return `<article class="card" data-id="${c.id}" style="animation-delay:${Math.min(idx * 18, 380)}ms">
      <div class="card-cover">
        <img src="${c.cover}" alt="" loading="${idx < 24 ? "eager" : "lazy"}" decoding="async"
             onerror="this.style.opacity='0'">
        ${err}
        ${progress}
      </div>
      <button class="card-quick" data-act="tags" aria-label="管理标签">
        <svg viewBox="0 0 24 24"><path d="M3 12V5a2 2 0 0 1 2-2h7l9 9-9 9z"/><circle cx="8" cy="8" r="1.4"/></svg>
      </button>
      <div class="card-body">
        <div class="card-main">
          <div class="card-head">
            ${cat}
            <h3 class="card-title" title="${esc(c.title)}">${esc(c.title)}</h3>
          </div>
          <div class="card-meta">
            <span>${c.pages || "?"} 页</span>
            ${tags ? `<span class="sep">·</span>${tags}` : ""}
          </div>
        </div>
        <div class="card-extra">
          <span>${fmtSize(c.size)}</span>
          <span class="card-path" title="${esc(c.path)}">${esc(c.path)}</span>
        </div>
      </div>
    </article>`;
  }

  function renderSkeleton(count = 12) {
    shelf.hidden = false;
    emptyBox.hidden = true;
    shelf.innerHTML = Array.from({ length: count },
      () => `<div class="skeleton"></div>`).join("");
  }

  function renderShelf() {
    if (S.loading) {
      renderSkeleton();
      return;
    }

    if (!S.comics.length) {
      shelf.innerHTML = "";
      shelf.hidden = true;
      renderEmpty();
      return;
    }

    shelf.hidden = false;
    emptyBox.hidden = true;
    shelf.innerHTML = S.comics.map(cardHTML).join("");
    document.querySelector(".main")?.scrollTo?.({ top: 0, behavior: "auto" });
  }

  function renderEmpty() {
    const el = emptyBox;
    el.hidden = false;

    let art = `<div class="empty-art"><i></i><i></i><i></i></div>`;

    if (S.q) {
      el.innerHTML = `${art}<h2>没有匹配的作品</h2>
        <p>换个关键词试试，或者到「全部作品」里看看。</p>
        <button class="btn ghost" id="emptyReset">清除搜索</button>`;
      $("emptyReset").onclick = () => {
        S.q = "";
        $("searchInput").value = "";
        commitSearch.cancel();
        hideSearchSuggest();
        loadComics();
      };
      return;
    }

    if (!S.libraryExists) {
      el.innerHTML = `${art}<h2>还没有指定漫画库</h2>
        <p>选择一个存放漫画压缩包的文件夹，MangaShelf 会扫描里面的 .zip / .cbz / .rar / .cbr / .7z / .cb7 / .pdf。</p>
        <button class="btn primary" id="emptySettings">选择漫画库文件夹</button>`;
      $("emptySettings").onclick = () => openSettings();
      return;
    }

    if (S.scanning) {
      el.innerHTML = `${art}<h2>正在扫描漫画库…</h2>
        <p>首次扫描需要为每本漫画生成封面，作品多的话请稍等一会儿。</p>`;
      return;
    }

    // 侧栏模式和标签是叠加筛选：交集为空时别误导成「这个模式本来就没有作品」
    if (S.mode && S.tagSel.length) {
      el.innerHTML = `${art}<h2>没有同时满足这些条件的作品</h2>
        <p>取消一两个标签，或者点侧栏的「全部作品」重新筛选。</p>`;
      return;
    }

    if (S.mode === "__untagged__") {
      el.innerHTML = `${art}<h2>所有作品都已经标记过了</h2>
        <p>可以回到「全部作品」查看书架。</p>`;
      return;
    }
    if (S.mode === "__updating__") {
      el.innerHTML = `${art}<h2>没有更新中的作品</h2>
        <p>在作品详情页打开「编辑模式」，把作品名后面的状态改成「更新中」。</p>`;
      return;
    }
    if (S.mode === "__favorite__") {
      el.innerHTML = `${art}<h2>还没有标记为喜爱的作品</h2>
        <p>在作品详情页点一下「开始阅读」左边的爱心，就会出现在这里。</p>`;
      return;
    }

    if (S.tagSel.length > 1) {
      el.innerHTML = `${art}<h2>没有同时拥有这些标签的作品</h2>
        <p>取消勾选一两个标签，或者换一组试试。</p>`;
      return;
    }

    if (S.tagSel.length) {
      el.innerHTML = `${art}<h2>这个标签下还没有作品</h2>
        <p>在作品卡片上点「标签」，就能把它归到这个分类里。</p>`;
      return;
    }

    el.innerHTML = `${art}<h2>书架还是空的</h2>
      <p>把漫画压缩包放进这个文件夹：<br><code>${esc(shortenPath(S.library, 60))}</code><br>然后点右上角的「扫描」。</p>
      <div style="display:flex;gap:10px;margin-top:14px">
        <button class="btn primary" id="emptyScan">立即扫描</button>
        <button class="btn ghost" id="emptyOpen">打开文件夹</button>
      </div>`;
    $("emptyScan").onclick = () => runScan(false);
    $("emptyOpen").onclick = () => openLibraryFolder();
  }

  function renderCounts() {
    // 侧栏四项与标签叠加：勾选标签后，计数也跟着收窄到「该模式 ∩ 已选标签」
    const sel = new Set(S.tagSel);
    const pool = (S.allComics || []).filter((c) => {
      if (!sel.size) return true;
      const names = new Set((c.tags || []).map((t) => t.name));
      return [...sel].every((n) => names.has(n));
    });
    const total = sel.size ? pool.length : (S.stats.total ?? pool.length ?? 0);
    $("countAll").textContent = String(total);
    $("countUntagged").textContent = String(pool.filter((c) => !(c.tags || []).length).length);
    $("countUpdating").textContent = String(pool.filter((c) => c.status === "updating").length);
    $("countFavorite").textContent = String(pool.filter((c) => c.favorite).length);
  }

  /* 侧栏标签：仅用于筛选。勾选多个时取交集（同时拥有这些标签的作品） */
  function renderTags() {
    const list = $("tagList");
    const hint = $("tagHint");
    if (!S.tags.length) {
      list.innerHTML = "";
      hint.hidden = false;
      return;
    }
    hint.hidden = true;
    const sel = new Set(S.tagSel);
    const groups = TagNS.group(S.tags);
    list.innerHTML = groups.map((g) => {
      const collapsed = S.collapsedNs.has(g.ns);
      const picked = g.tags.filter((t) => sel.has(t.name)).length;
      return `
      <div class="tag-group ${collapsed ? "collapsed" : ""}" data-ns="${esc(g.ns)}">
        <button class="tag-group-head" type="button" data-ns="${esc(g.ns)}"
                aria-expanded="${!collapsed}" title="${collapsed ? "展开" : "折叠"}「${esc(g.label)}」">
          ${esc(g.label)}<em>${picked ? `${picked} / ` : ""}${g.tags.length}</em>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </button>
        <div class="tag-rows">
          ${g.tags.map((t) => {
            const on = sel.has(t.name);
            return `
            <button class="tag-row ${on ? "checked" : ""}" data-tag-name="${esc(t.name)}"
                    role="checkbox" aria-checked="${on}" title="${esc(t.name)}">
              <span class="swatch" style="background:${safeColor(t.color)}"></span>
              <span class="tname">${esc(TagNS.label(t))}</span>
              <span class="tcount">${t.count}</span>
              <span class="tcheck" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>
              </span>
            </button>`;
          }).join("")}
        </div>
      </div>`;
    }).join("");
  }

  /* 折叠 / 展开标签分类：纯界面状态，跟着浏览器走，不动筛选条件 */
  function toggleTagGroup(ns) {
    if (!ns) return;
    if (S.collapsedNs.has(ns)) S.collapsedNs.delete(ns);
    else S.collapsedNs.add(ns);
    try {
      localStorage.setItem("tagGroupsCollapsed", JSON.stringify([...S.collapsedNs]));
    } catch (_) { /* 隐私模式下忽略 */ }
    renderTags();
  }

  const MODE_LABEL = {
    __untagged__: "未进行标记",
    __updating__: "更新中",
    __favorite__: "Favorite",
  };

  function renderHeader() {
    const picked = S.tagSel.map((n) => S.tags.find((t) => t.name === n)).filter(Boolean);
    const modeLabel = MODE_LABEL[S.mode] || "";
    let title;
    if (modeLabel) {
      title = picked.length
        ? `${modeLabel} · ${picked.length === 1 ? TagNS.label(picked[0]) : `已选 ${picked.length} 个标签`}`
        : modeLabel;
    } else if (!picked.length) title = "全部作品";
    else if (picked.length === 1) title = TagNS.label(picked[0]);
    else title = `已选 ${picked.length} 个标签`;
    $("viewTitle").textContent = title;
    $("viewTitle").title = [modeLabel, ...picked.map((t) => t.name)].filter(Boolean).join(" & ");

    // 后退按钮：有历史才出现；提示里写清下一站，别让人猜
    const back = $("viewBack");
    if (back) {
      const top = S.hist[S.hist.length - 1];
      const canBack = !!top && !sameSnapshot(top, navSnapshot());
      back.hidden = !canBack;
      if (canBack) back.title = `后退一步：${backHint(top)}`;
    }

    document.querySelectorAll(".nav-item").forEach((b) => {
      const t = b.dataset.tag || "";
      // 模式仍然选中，哪怕下面又勾了标签；只有「全部作品」在勾了标签时让位给标签标题
      const active = t === S.mode && (S.mode !== "" || !picked.length);
      b.classList.toggle("active", active);
    });

    const n = S.comics.length;
    let sub = `共 ${n} 本`;
    const parts = [];
    if (modeLabel) parts.push(modeLabel);
    if (picked.length > 1) parts.push(`同时拥有 ${picked.map((t) => TagNS.label(t)).join(" + ")}`);
    if (parts.length) sub += ` · ${parts.join(" · ")}`;
    if (S.q) sub += ` · 搜索「${S.q}」`;
    if (S.scanning) sub += " · 正在扫描";
    $("viewSub").textContent = sub;
  }

  function updateLibInfo(st) {
    if (st && st.library) S.library = st.library;
    if (st && st.stats) S.stats = st.stats;
    $("aboutLib").textContent = tailPath(S.library, 3);
    $("aboutLib").title = S.library || "";
    renderHubValues();
  }

  /* 设置首页各条目的摘要（库路径 / 标签数 / 跟随状态 / 主题） */
  function renderHubValues() {
    if (!$("setLibValue")) return;
    $("setLibValue").textContent = S.library ? tailPath(S.library, 2) : "未设置";
    $("setLibValue").title = S.library || "";

    const total = (S.tags || []).length;
    const used = (S.tags || []).reduce((n, t) => n + (t.count > 0 ? 1 : 0), 0);
    $("setTagValue").textContent = total ? `${total} 个标签 · ${used} 个在用` : "还没有标签";
    $("setTagValue").title = total ? `${total} 个标签，其中 ${used} 个已用在作品上` : "";

    let side = S.sidecar ? "已开启" : "已关闭";
    if (S.sidecar) {
      if (!S.tagFile) side += " · 状态未知";
      else if (!S.tagFile.exists) side += " · 尚未写出";
      else side += S.tagFile.inSync ? " · 已同步" : " · 待同步";
    }
    $("setSidecarValue").textContent = side;
    $("setThemeValue").textContent = document.body.dataset.theme === "light" ? "浅色" : "深色";

    $("aboutTotal").textContent = `${S.stats.total || 0} 本`;
    $("aboutTags").textContent = total ? `${total} 个（${used} 个在用）` : "还没有标签";
  }

  function updateTagFileUI() {
    const el = $("tagFileState");
    if (el) {
      const f = S.tagFile;
      if (!f) {
        el.textContent = "未知";
        el.dataset.state = "off";
      } else if (!f.exists) {
        el.textContent = "尚未生成";
        el.dataset.state = "off";
      } else {
        el.textContent = f.inSync
          ? `已同步 · ${f.tags} 个标签`
          : `待同步 · ${f.tags} 个标签`;
        el.dataset.state = f.inSync ? "ok" : "warn";
      }
    }
    renderHubValues();
  }

  function updateScanUI(scan) {
    const bar = $("scanBar");
    const btn = $("scanBtn");
    if (scan.running) {
      bar.hidden = false;
      btn.disabled = true;
      const pct = scan.total ? Math.round((scan.done / scan.total) * 100) : 0;
      $("scanFill").style.width = pct + "%";
      $("scanText").textContent = scan.total
        ? `扫描中 ${scan.done}/${scan.total}${scan.current ? " · " + scan.current.split("/").pop() : ""}`
        : "正在查找文件…";
    } else {
      bar.hidden = true;
      btn.disabled = false;
    }
  }

  /* ---------------------------------------------------------------- 数据 */
  async function loadComics() {
    S.loading = true;
    renderShelf();
    try {
      const res = await API.list({
        // 侧栏模式与标签勾选是叠加关系：先按标签取交集，再在结果里套模式
        tags: S.tagSel,
        q: S.q,
        sort: S.sort,
      });
      let items = res.items || [];
      if (S.mode === "__untagged__") items = items.filter((c) => !(c.tags || []).length);
      else if (S.mode === "__updating__") items = items.filter((c) => c.status === "updating");
      else if (S.mode === "__favorite__") items = items.filter((c) => c.favorite);
      S.comics = items;
      if (!S.tagSel.length && !S.mode && !S.q && S.sort === "recent") S.allComics = items;
    } catch (err) {
      S.comics = [];
      toast("加载书架失败：" + err.message);
    } finally {
      S.loading = false;
      renderShelf();
      renderHeader();
      renderCounts();
    }
  }

  async function loadTags() {
    try {
      const res = await API.tags();
      S.tags = res.tags || [];
      renderTags();
    } catch (_) { /* 忽略 */ }
  }

  async function refreshAll() {
    try {
      const st = await API.state();
      S.libraryExists = st.libraryExists;
      S.library = st.library;
      S.stats = st.stats;
      updateLibInfo(st);
      if (st.scan && st.scan.running) {
        S.wasScanning = true;
        pollScan();
      }
    } catch (_) { /* 服务可能刚启动 */ }
    await Promise.all([loadComics(), loadTags()]);
  }
  window.refreshAll = refreshAll;

  /* ---------------------------------------------------------------- 扫描 */
  async function pollScan() {
    clearTimeout(S.pollTimer);
    try {
      const { scan } = await API.progress();
      S.scanning = scan.running;
      updateScanUI(scan);

      if (scan.running) {
        S.wasScanning = true;
        S.pollTimer = setTimeout(pollScan, 550);
        if (S.loading === false && !S.comics.length) renderEmpty();
      } else if (S.wasScanning) {
        S.wasScanning = false;
        await refreshAll();
        const parts = [];
        if (scan.added) parts.push(`新增 ${scan.added}`);
        if (scan.updated) parts.push(`更新 ${scan.updated}`);
        if (scan.removed) parts.push(`移除 ${scan.removed}`);
        if (scan.failed) parts.push(`失败 ${scan.failed}`);
        toast(parts.length ? `扫描完成：${parts.join("，")}` : "扫描完成");
        if (scan.errors && scan.errors.length) {
          console.warn("[扫描警告]", scan.errors);
        }
      }
    } catch (_) { /* 网络抖动，下一次再试 */ }
  }

  async function runScan(force) {
    try {
      const res = await API.scan(force);
      S.wasScanning = true;
      S.scanning = true;
      updateScanUI(res.scan || { running: true, total: 0, done: 0 });
      if (!res.started) toast("已经在扫描中啦");
      pollScan();
    } catch (err) {
      toast("无法启动扫描：" + err.message);
    }
  }

  /* ---------------------------------------------------------------- 标签弹窗 */
  const NS_SAMPLE = {
    language: "chinese",
    parody: "舰队Collection",
    character: "长波",
    artist: "yositama",
    group: "CUNICULUS",
    female: "巨乳",
    male: "单男主",
    mixed: "热血",
    other: "杂项",
  };

  function paintCatPicker(box, current, onPick) {
    if (!box) return;
    box.innerHTML = TagNS.ORDER.map((ns) =>
      `<button class="cat-btn${ns === current ? " active" : ""}" data-ns="${ns}">${TagNS.labelOf(ns)}</button>`).join("");
    box.onclick = (e) => {
      const b = e.target.closest("[data-ns]");
      if (!b) return;
      onPick(b.dataset.ns);
    };
  }

  function openTagModal(comic) {
    S.tagTarget = comic;
    S.editingTags = (comic.tags || []).map((t) => ({
      name: t.name,
      ns: t.ns || TagNS.parse(t.name).ns,
      label: TagNS.label(t),
      color: t.color || "",
      display: t.display || "",
    }));
    $("tagComicTitle").textContent = comic.title;
    $("tagComicTitle").title = comic.path || comic.title;

    renderChips();
    $("tagSidecarNote").hidden = !S.sidecar;
    $("tagModal").hidden = false;

    // 标签目录可能在别处刚改过（新建 / 删除 / 换设备同步），顺手拉一次最新的
    API.tags().then((res) => {
      if (!res || !S.tagTarget || S.tagTarget.id !== comic.id) return;
      S.tags = res.tags || S.tags;
      renderTagSelects();
    }).catch(() => {});
  }

  function renderChips() {
    const box = $("tagChips");
    if (!S.editingTags.length) {
      box.innerHTML = `<span class="chips-empty">还没有标签，在下面从标签目录里选一个</span>`;
      renderTagSelects();
      return;
    }
    box.innerHTML = TagNS.group(S.editingTags).map((g) => `
      <div class="chip-group">
        <span class="chip-group-label">${esc(g.label)}</span>
        <div class="chip-group-items">
          ${g.tags.map((t) => {
            const shown = t.label || TagNS.label(t.name);
            const raw = t.name !== shown ? `<em>${esc(t.name)}</em>` : "";
            return `<span class="tag-chip" style="color:${safeColor(t.color)}">
              <span class="chip-text"><b>${esc(shown)}</b>${raw}</span>
              <button class="x edit" data-ei="${S.editingTags.indexOf(t)}" title="修改显示名">
                <svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>
              </button>
              <button class="x" data-ri="${S.editingTags.indexOf(t)}" title="移除">
                <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
            </span>`;
          }).join("")}
        </div>
      </div>`).join("");
    renderTagSelects();
  }

  /* 下拉：每个分类各一个；分类在该本上已加满时保留一行但禁用，位置不跳动 */
  function renderTagSelects() {
    const box = $("tagSelects");
    if (!box) return;
    const groups = TagNS.group(S.tags || []);
    if (!groups.length) {
      box.innerHTML = `<p class="tag-add-empty">标签目录是空的，先点「＋ 新建标签」</p>`;
      return;
    }
    box.innerHTML = groups.map((g) => {
      const pool = g.tags.filter((t) => !S.editingTags.some((e) => e.name === t.name));
      const full = !pool.length;
      const head = full ? `${g.label}标签都已加上` : `选择${g.label}标签…`;
      const opts = pool.map((t) =>
        `<option value="${esc(t.name)}" title="${esc(t.name)}">${esc(TagNS.label(t))}（${t.count}）</option>`).join("");
      return `<label class="tag-add-field${full ? " is-full" : ""}">
        <span class="cat-name">${esc(g.label)}</span>
        <select class="tag-select" data-ns="${esc(g.ns)}"${full ? " disabled" : ""} aria-label="从「${esc(g.label)}」分类里选择标签">
          <option value="">${esc(head)}</option>${opts}
        </select>
      </label>`;
    }).join("");
  }

  /* 从标签目录选中一项，加入当前作品 */
  function addTagFromCatalog(name) {
    if (!name) return;
    if (S.editingTags.some((t) => t.name === name)) return;
    const known = S.tags.find((t) => t.name === name);
    if (!known) { toast("标签目录里找不到这个标签"); return; }
    S.editingTags.push({
      name: known.name,
      ns: known.ns || TagNS.parse(known.name).ns,
      label: TagNS.label(known),
      color: known.color || "",
      display: known.display || "",
    });
    renderChips();
  }

  /* 给已存在的标签改界面显示名（全局生效，不动原始标签名） */
  async function editChipDisplay(idx) {
    const t = S.editingTags[idx];
    if (!t) return;
    const next = prompt(`为「${t.name}」设置界面显示名（留空恢复默认）`, t.label || "");
    if (next === null) return;
    try {
      await API.updateTag(t.name, { display: next.trim() });
      const res = await API.tags();
      S.tags = res.tags || S.tags;
      const known = S.tags.find((x) => x.name === t.name);
      t.display = next.trim();
      t.label = known ? TagNS.label(known) : TagNS.label(t.name);
      renderChips();
      renderTags();
      if (Detail.isOpen()) Detail.refresh();
      toast("显示名已更新");
    } catch (err) {
      toast("修改失败：" + err.message);
    }
  }

  async function saveTags() {
    const comic = S.tagTarget;
    if (!comic) return;
    try {
      const payload = S.editingTags.map((t) => ({ name: t.name, display: t.display || "" }));
      const res = await API.setTags(comic.id, payload);
      comic.tags = res.tags || [];
      S.tags = res.allTags || S.tags;
      renderChips();
      renderTags();
      renderShelf();
      $("tagModal").hidden = true;
      if (Detail.isOpen()) Detail.refresh();
      toast("标签已保存");
    } catch (err) {
      toast("保存失败：" + err.message);
    }
  }

  /* 勾选 / 取消一个标签：跟侧栏模式叠加，多选取交集（同时拥有全部所选标签）。
     切换侧栏模式才会清空勾选，勾标签本身不会把模式丢掉。 */
  function toggleTag(name) {
    if (!name) return;
    pushNav();
    const i = S.tagSel.indexOf(name);
    if (i >= 0) S.tagSel.splice(i, 1);
    else S.tagSel.push(name);
    closeNav();
    if (Detail.isOpen()) go("");
    renderTags();
    renderHeader();
    loadComics();
  }

  /* 顶部搜索的标签联想：原始名 / 显示名 / 分类名都能命中，精确匹配优先 */
  function tagMatchScore(t, q) {
    const name = String(t.name || "").toLowerCase();
    const label = String(TagNS.label(t) || "").toLowerCase();
    const value = TagNS.parse(t.name || "").value.toLowerCase();
    const ns = TagNS.labelOf(TagNS.nsOf(t)).toLowerCase();
    if (name === q || label === q || value === q) return 0;
    if (name.startsWith(q) || label.startsWith(q) || value.startsWith(q)) return 1;
    if (name.includes(q) || label.includes(q) || ns.includes(q)) return 2;
    return -1;
  }

  function hideSearchSuggest() {
    const box = $("searchSuggest");
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
    S.searchHits = [];
    S.searchPick = -1;
  }

  function paintSuggestPick() {
    $("searchSuggest").querySelectorAll("button[data-i]").forEach((b) => {
      b.classList.toggle("sel", Number(b.dataset.i) === S.searchPick);
    });
  }

  function renderSearchSuggest(raw) {
    const box = $("searchSuggest");
    const q = String(raw || "").trim().toLowerCase();
    if (!q) { hideSearchSuggest(); return; }
    S.searchHits = (S.tags || [])
      .map((t) => ({ t, score: tagMatchScore(t, q) }))
      .filter((x) => x.score >= 0)
      .sort((a, b) => a.score - b.score
        || (b.t.count || 0) - (a.t.count || 0)
        || TagNS.compare(a.t, b.t))
      .slice(0, 8)
      .map((x) => x.t);
    S.searchPick = -1;
    if (!S.searchHits.length) { hideSearchSuggest(); return; }
    box.innerHTML = `<div class="ss-head">按标签筛选</div>` + S.searchHits.map((t, i) => `
      <button type="button" data-i="${i}">
        <span class="swatch" style="background:${safeColor(t.color)}"></span>
        <span class="sg-label">${esc(TagNS.label(t))}</span>
        <span class="sg-raw">${esc(t.name)}</span>
        <span class="sg-count">${t.count}</span>
      </button>`).join("");
    box.hidden = false;
  }

  /* 选中联想项 = 勾上这个标签，和侧栏勾选是同一套筛选状态 */
  function chooseSearchTag(i) {
    const t = S.searchHits[i];
    if (!t) return;
    pushNav();
    commitSearch.cancel();
    S.q = "";
    const input = $("searchInput");
    input.value = "";
    hideSearchSuggest();
    input.blur();
    if (!S.tagSel.includes(t.name)) S.tagSel.push(t.name);
    // 如果这一组是折起来的，展开它，让侧栏的勾选项看得见
    const ns = TagNS.nsOf(t);
    if (S.collapsedNs.delete(ns)) {
      try { localStorage.setItem("tagGroupsCollapsed", JSON.stringify([...S.collapsedNs])); } catch (_) {}
    }
    if (Detail.isOpen()) go("");
    renderTags();
    renderHeader();
    loadComics();
    toast(`已按标签筛选：${TagNS.label(t)}`);
  }

  /* ---------------- 设置里的标签管理：增加 / 改名 / 删除 ---------------- */
  function tagAdminRow(t) {
    return `
      <div class="tag-admin-row" data-admin-name="${esc(t.name)}">
        <span class="swatch" style="background:${safeColor(t.color)}"></span>
        <span class="tag-admin-name">
          <b>${esc(TagNS.label(t))}</b>
          <em>${esc(t.name)}</em>
        </span>
        <span class="tag-admin-count">${t.count} 本</span>
        <button class="mini-btn" data-act="edit" title="改名 / 改显示名">编辑</button>
        <button class="mini-btn danger" data-act="remove" title="删除这个标签">删除</button>
      </div>`;
  }

  function renderTagAdmin() {
    const box = $("tagAdminList");
    if (!box) return;

    const q = (S.tagQuery || "").trim().toLowerCase();
    const list = q
      ? S.tags.filter((t) => `${t.name} ${TagNS.label(t)}`.toLowerCase().includes(q))
      : S.tags;

    if (!S.tags.length) {
      box.innerHTML = `<p class="tag-admin-empty">还没有任何标签。点上面的「＋ 新建标签」开始。</p>`;
      $("tagAdminHint").textContent = "改名、改显示名、删除都在这里。";
      renderHubValues();
      return;
    }

    if (!list.length) {
      box.innerHTML = `<p class="tag-admin-empty">没有匹配「${esc(S.tagQuery)}」的标签。</p>`;
      $("tagAdminHint").textContent = `共 ${S.tags.length} 个标签，没有匹配项。`;
      return;
    }

    const groups = TagNS.group(list);
    box.innerHTML = groups.map((g) => `
      <div class="tag-admin-group">
        <div class="tag-admin-head">${esc(g.label)}<em>${g.tags.length}</em></div>
        ${g.tags.map(tagAdminRow).join("")}
      </div>`).join("");

    const total = S.tags.length;
    const used = S.tags.reduce((n, t) => n + (t.count > 0 ? 1 : 0), 0);
    $("tagAdminHint").textContent = q
      ? `筛出 ${list.length} / ${total} 个标签。改名、删除会同步到打了它的每一本作品。`
      : `共 ${total} 个标签（${used} 个正在使用）。改名、删除都会同步到打了它的作品，并重写每本的 json。`;
    renderHubValues();
  }

  function startTagEdit(row, name) {
    const t = S.tags.find((x) => x.name === name);
    if (!t) return;
    row.classList.add("editing");
    row.innerHTML = `
      <div class="tag-admin-form">
        <label>
          <span>标签名</span>
          <input class="tag-admin-input" data-field="name" value="${esc(t.name)}"
                 placeholder="例如 language:chinese" spellcheck="false" />
        </label>
        <label>
          <span>显示名</span>
          <input class="tag-admin-input" data-field="display" value="${esc(t.display || "")}"
                 placeholder="留空则自动（如汉语）" spellcheck="false" />
        </label>
      </div>
      <div class="tag-admin-actions">
        <button class="btn ghost sm" data-act="cancel">取消</button>
        <button class="btn primary sm" data-act="save">保存</button>
      </div>`;
    row.querySelector('[data-field="name"]')?.focus();
  }

  async function commitTagEdit(row, oldName) {
    const nameEl = row.querySelector('[data-field="name"]');
    const dispEl = row.querySelector('[data-field="display"]');
    const raw = (nameEl?.value || "").trim();
    const display = (dispEl?.value || "").trim();
    if (!raw) { toast("标签名不能为空"); nameEl?.focus(); return; }

    const canon = TagNS.parse(raw).name;
    if (!canon) { toast("标签名不能为空"); return; }
    if (canon === oldName && display === (S.tags.find((x) => x.name === oldName)?.display || "")) {
      renderTagAdmin();
      return;
    }
    const btn = row.querySelector('[data-act="save"]');
    if (btn) btn.disabled = true;
    try {
      const res = await API.updateTag(oldName, { name: canon, display });
      S.tags = res.tags || [];
      S.tagSel = S.tagSel.map((n) => (n === oldName ? canon : n));
      renderTags();
      renderTagAdmin();
      await loadComics();
      if (Detail.isOpen()) Detail.refresh();
      toast(canon === oldName ? "显示名已更新" : `已改名为 ${canon}，相关作品一起更新`);
    } catch (err) {
      toast("保存失败：" + err.message);
      if (btn) btn.disabled = false;
    }
  }

  async function deleteTag(name) {
    const found = S.tags.find((t) => t.name === name);
    const label = found ? TagNS.label(found) : name;
    const n = found ? found.count : 0;
    const msg = n
      ? `删除标签「${label}」？\n打了这个标签的 ${n} 本作品会一并去掉它（作品文件本身不会被删除）。`
      : `删除标签「${label}」？`;
    if (!confirm(msg)) return;
    try {
      const res = await API.deleteTag(name);
      S.tags = res.tags || [];
      S.tagSel = S.tagSel.filter((x) => x !== name);
      renderTags();
      renderTagAdmin();
      await loadComics();
      if (Detail.isOpen()) Detail.refresh();
      toast(n ? `标签已删除，${n} 本作品同步移除` : "标签已删除");
    } catch (err) {
      toast("删除失败：" + err.message);
    }
  }

  /* 新建标签（侧栏 + 或标签弹窗里的「＋ 新建标签」） */
  function openNewTag(fromComic) {
    S.newTagFrom = Boolean(fromComic);
    // 上次选的分类（旧的 "circle" / 空值统一回落到「混合」）
    const savedNs = localStorage.getItem("tag-ns") || "";
    S.newTagNs = TagNS.ORDER.includes(savedNs) ? savedNs
      : (TagNS.ALIASES[savedNs] || "mixed");
    $("newTagName").value = "";
    $("newTagDisplay").value = "";
    const pick = (ns) => {
      S.newTagNs = ns;
      localStorage.setItem("tag-ns", ns);
      paintCatPicker($("newTagCats"), ns, pick);
      $("newTagName").placeholder = `内容，例如 ${NS_SAMPLE[ns] || "热血"}`;
      updateNewTagHint();
      $("newTagName").focus();
    };
    paintCatPicker($("newTagCats"), S.newTagNs, pick);
    $("newTagName").placeholder = `内容，例如 ${NS_SAMPLE[S.newTagNs] || "热血"}`;
    updateNewTagHint();
    $("newTagModal").hidden = false;
    setTimeout(() => $("newTagName").focus(), 60);
  }

  function updateNewTagHint() {
    const value = $("newTagName").value.trim();
    const disp = $("newTagDisplay").value.trim();
    if (!value) {
      $("newTagHint").textContent =
        `分类「${TagNS.labelOf(S.newTagNs)}」，保存为 ${S.newTagNs}:…`;
      return;
    }
    const p = TagNS.parse(value);
    // 用户自己写了分类前缀（language:chinese）就用它，否则用上面选中的分类
    const ns = p.prefixed ? p.ns : (S.newTagNs || "mixed");
    const raw = `${ns}:${p.value}`;
    const shown = disp || TagNS.label(raw);
    $("newTagHint").textContent = `保存为 ${raw} · 界面显示「${shown}」`;
  }

  /* ---------------------------------------------------------------- 设置页 */
  /* 设置是一层菜单 + 若干独立子页：首页只放条目，重度内容（如标签管理）都进子页，
     否则以后越加越多，首页会变成一堵墙。新增设置项请沿用这个模式。 */
  const SET_PAGES = {
    "": { title: "设置", sub: "漫画库、标签与外观" },
    library: { title: "漫画库文件夹", sub: "存放漫画压缩包的目录" },
    tags: { title: "标签管理", sub: "新建、改名、改显示名与删除" },
    sidecar: { title: "标签跟随文件", sub: "标签与阅读进度的保存方式" },
    appearance: { title: "外观", sub: "深色 / 浅色主题" },
    about: { title: "关于 MangaShelf", sub: "版本与当前库信息" },
  };

  const Settings = {
    current: null, // null 表示设置页没打开
    ready: null,   // 本次打开时的数据加载 Promise

    isOpen() { return this.current !== null; },

    open(page = "") {
      const cur = location.hash.replace(/^#/, "");
      S.settingsOrigin = cur.startsWith("settings") ? "" : cur;
      this.go(page);
    },

    go(page = "") {
      const target = "settings" + (page ? "=" + page : "");
      if (location.hash === "#" + target) this.show(page);
      else location.hash = target;
    },

    /* 子页里返回上一层；首页返回来源（书架 / 详情页） */
    back() {
      if (this.current) { this.go(""); return; }
      this.close();
    },

    /* 直接离开设置页，回到打开前的页面 */
    close() {
      const origin = S.settingsOrigin || "";
      S.settingsOrigin = "";
      go(origin);
    },

    async show(page = "") {
      const key = Object.prototype.hasOwnProperty.call(SET_PAGES, page) ? page : "";
      const first = this.current === null;
      this.current = key;

      // 先把页面立起来，再等数据；数据回来后统一渲染，避免路径框短暂空白
      $("settingsPage").hidden = false;
      await this.ensure();
      if (this.current === null) return; // 等待期间被关掉了

      $("setTitle").textContent = SET_PAGES[key].title;
      $("setSub").textContent = SET_PAGES[key].sub;
      $("setBackLabel").textContent = key
        ? "设置"
        : (S.settingsOrigin.startsWith("comic=") ? "返回详情" : "返回书架");

      document.querySelectorAll(".set-view").forEach((v) => {
        v.hidden = v.dataset.view !== (key || "home");
      });
      const view = document.querySelector(`.set-view[data-view="${key || "home"}"]`);
      if (view) view.scrollTop = 0;

      if (first || key === "tags") renderTagAdmin();
      renderHubValues();
    },

    /* 设置数据只拉一次；关掉设置后下次重新拉 */
    ensure() {
      if (!this.ready) this.ready = this.load();
      return this.ready;
    },

    hide() {
      if (this.current === null) return;
      this.current = null;
      this.ready = null;
      $("settingsPage").hidden = true;
      S.tagQuery = "";
      const s = $("tagSearch");
      if (s) s.value = "";
      const v = document.querySelector('.set-view[data-view="tags"]');
      if (v) v.scrollTop = 0;
    },

    async load() {
      try {
        const res = await API.settings();
        S.settings = res.settings;
        S.tagFile = res.tagFile || S.tagFile;
        S.library = res.settings.library_path || S.library;
        S.sidecar = res.settings.sidecar !== false;
        $("libPathInput").value = res.settings.library_path || "";
        $("sidecarToggle").checked = S.sidecar;
        // 本地存过的主题优先，避免打开设置时界面突然换色
        applyTheme(localStorage.getItem("theme") || res.settings.theme || "dark");
        try {
          const t = await API.tags();
          S.tags = t.tags || [];
          renderTags();
        } catch (_) { /* 标签列表读不到也不影响其他设置 */ }
        updateTagFileUI();
        renderTagAdmin();
        updateLibInfo({});
      } catch (err) {
        toast("读取设置失败：" + err.message);
      }
    },
  };

  function openSettings(page = "") {
    Settings.open(page);
  }

  async function saveSettings() {
    const path = $("libPathInput").value.trim();
    if (!path) { toast("请填写漫画库文件夹路径"); return; }
    const btn = $("saveSettingsBtn");
    btn.disabled = true;
    try {
      const res = await API.saveSettings({
        library_path: path,
        rescan: true,
        theme: document.body.dataset.theme,
        sidecar: $("sidecarToggle").checked,
      });
      S.libraryExists = res.libraryExists;
      S.library = res.settings.library_path;
      S.sidecar = res.settings.sidecar !== false;
      updateLibInfo({ library: S.library, stats: S.stats });

      Settings.close();
      if (!res.libraryExists) {
        toast("这个文件夹还不存在，已创建后会开始扫描");
      } else {
        toast("已保存，开始扫描");
      }
      S.tagSel = [];
      S.mode = "";
      renderTags();
      await loadComics();
      S.wasScanning = true;
      pollScan();
    } catch (err) {
      toast("保存失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  /* ---------------------------------------------------------- 右键菜单
     桌面应用里「刷新 / 另存为 / 打印 / 检查元素」毫无意义，整套换掉。
     输入框例外：那里要系统菜单才贴得了字。 */
  const CTX_ICONS = {
    folder: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5V6a2 2 0 0 1 2-2h4l2 2.5h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
    copy: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 5.5A1.5 1.5 0 0 0 13.5 4H6a2 2 0 0 0-2 2v7.5A1.5 1.5 0 0 0 5.5 15"/></svg>`,
    refresh: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 4v5h-5"/></svg>`,
  };
  let ctxItems = [];   // 当前菜单项（带各自的 run 回调）
  let ctxOpenedAt = 0; // 刚弹出来的菜单别被抬起的那一下点击立刻关掉

  function closeCtxMenu() {
    const el = $("ctxMenu");
    if (!el || el.hidden) return;
    el.hidden = true;
    el.innerHTML = "";
    ctxItems = [];
  }

  function openCtxMenu(x, y, items, title = "") {
    const el = $("ctxMenu");
    if (!el || !items.length) return;
    ctxItems = items;
    el.innerHTML =
      (title ? `<div class="ctx-title" title="${esc(title)}">${esc(title)}</div>` : "") +
      items.map((it, i) => it.sep
        ? `<div class="ctx-sep"></div>`
        : `<button class="ctx-item" role="menuitem" data-i="${i}"${it.disabled ? " disabled" : ""}>
             ${it.icon || ""}<span class="ctx-label">${esc(it.label)}</span>
             ${it.hint ? `<span class="ctx-hint">${esc(it.hint)}</span>` : ""}
           </button>`).join("");
    el.hidden = false;
    ctxOpenedAt = Date.now();
    // 先摆到左上角量一次尺寸，再按视口边缘收回来，避免菜单被裁掉
    el.style.left = "0px";
    el.style.top = "0px";
    const r = el.getBoundingClientRect();
    el.style.left = Math.max(6, Math.min(x, window.innerWidth - r.width - 6)) + "px";
    el.style.top = Math.max(6, Math.min(y, window.innerHeight - r.height - 6)) + "px";
  }

  /* 右键落在某一本作品上：想到新功能就往这个数组里加一项 */
  function comicMenu(comic) {
    return [{
      icon: CTX_ICONS.folder,
      label: "打开文件夹",
      run: async () => {
        try {
          const res = await API.revealComic(comic.id);
          toast(res && res.mode === "folder"
            ? "已打开源文件所在文件夹"
            : "已在资源管理器中定位到源文件");
        } catch (err) {
          const data = err.data || {};
          const msg = err.message || "";
          const p = data.path;
          if (p && (data.reason === "low-integrity" || /拒绝访问|权限|WinError 5/.test(msg))) {
            // 进程被系统降权（常见于工作区里的开发版）：驱不动资源管理器。
            // 不做无用的重试，直接把路径塞进剪贴板，粘到地址栏就能用。
            try {
              await copyText(p);
              toast("当前程序没有权限打开资源管理器，已把文件路径复制到剪贴板");
              return;
            } catch (_) { /* 复制也失败就照原样报错 */ }
          }
          toast("打不开文件夹：" + msg);
        }
      },
    }, {
      icon: CTX_ICONS.copy,
      label: "复制文件路径",
      run: async () => {
        try {
          const [detail, st] = await Promise.all([API.comic(comic.id), API.state()]);
          const library = String((st && st.library) || "");
          const sep = library.includes("\\") ? "\\" : "/";
          const rel = String((detail && detail.path) || "").replace(/[\\/]+/g, sep);
          const full = library.replace(/[\\/]+$/, "") + sep + rel;
          await copyText(full);
          toast("已复制文件路径");
        } catch (err) {
          toast("复制失败：" + err.message);
        }
      },
    }];
  }

  /* 复制到剪贴板：非安全上下文里 navigator.clipboard 不可用，退到 textarea + execCommand */
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
      }
    } catch (_) { /* 落到下面的兜底 */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) throw new Error("浏览器不允许写剪贴板");
  }

  /* 打开库目录。进程被系统降权时（工作区里的开发版）打不开资源管理器，
     此时退而求其次——把库路径复制到剪贴板，粘到地址栏就能用。 */
  async function openLibraryFolder() {
    try {
      await API.reveal();
    } catch (err) {
      const data = err.data || {};
      const msg = err.message || "";
      if (data.path && (data.reason === "low-integrity" || /拒绝访问|权限|WinError 5/.test(msg))) {
        try {
          await copyText(data.path);
          toast("当前程序没有权限打开资源管理器，已把库路径复制到剪贴板");
          return;
        } catch (_) { /* 复制也失败就照原样报错 */ }
      }
      toast("打不开文件夹：" + msg);
    }
  }

  function comicForContext(target) {
    const card = target.closest?.(".card[data-id]");
    if (card) {
      const id = card.dataset.id;
      return S.comics.find((c) => c.id === id) || S.allComics.find((c) => c.id === id) || { id };
    }
    if (Detail.isOpen() && target.closest?.("#detail")) {
      const id = Detail.currentId();
      if (!id) return null;
      const hit = [...(S.allComics || []), ...(S.comics || [])].find((c) => c.id === id);
      return hit || { id, title: $("detailTitle")?.textContent || "" };
    }
    return null;
  }

  function bindContextMenu() {
    const el = $("ctxMenu");
    if (!el) return;

    document.addEventListener("contextmenu", (e) => {
      const t = e.target;
      // 输入框 / 可编辑区域保留系统菜单：不然没法粘贴
      if (t.closest?.("input, textarea, [contenteditable='true']")) {
        closeCtxMenu();
        return;
      }
      e.preventDefault();
      closeCtxMenu();

      const comic = comicForContext(t);
      if (comic) {
        openCtxMenu(e.clientX, e.clientY, comicMenu(comic), comic.title || "");
        return;
      }
      // 空白处只留「刷新」，别把浏览器的另存为 / 打印 / 检查放出来
      openCtxMenu(e.clientX, e.clientY, [
        { icon: CTX_ICONS.refresh, label: "刷新", run: () => refreshAll() },
      ]);
    });

    el.addEventListener("click", async (e) => {
      const btn = e.target.closest(".ctx-item");
      if (!btn || btn.disabled) return;
      const it = ctxItems[Number(btn.dataset.i)];
      closeCtxMenu();
      if (it && typeof it.run === "function") await it.run();
    });

    // 关菜单的时机：点到别处、滚动、缩放、Esc、路由变化
    document.addEventListener("click", (e) => {
      if (Date.now() - ctxOpenedAt < 260) return;   // 触发菜单的那次点击
      if (!e.target.closest?.("#ctxMenu")) closeCtxMenu();
    });
    document.addEventListener("scroll", closeCtxMenu, true);
    window.addEventListener("resize", closeCtxMenu);
    window.addEventListener("blur", closeCtxMenu);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeCtxMenu();
    });
  }

  /* ---------------------------------------------------------------- 事件 */
  function bind() {
    // 卡片
    shelf.addEventListener("click", (e) => {
      const card = e.target.closest(".card");
      if (!card) return;
      const comic = S.comics.find((c) => c.id === card.dataset.id);
      if (!comic) return;

      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "tags") { openTagModal(comic); return; }

      // 点封面 / 卡片一律进入详情页，不再直接开始阅读
      pushNav();
      goDetail(comic.id);
    });

    // 详情页的「＋ 编辑标签」由 detail.js 直接回调 Detail.onEditTags，这里不再重复接管

    // 侧栏：全部作品 / 未进行标记 / 更新中 / Favorite。
    // 换栏目时清空下面的标签勾选；勾标签时则保留栏目，两边叠加筛选。
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = btn.dataset.tag || "";
        // 已经在同一栏目里就不用记一步
        if (next !== S.mode || S.tagSel.length) pushNav();
        S.mode = next;
        S.tagSel = [];
        closeNav();
        if (Detail.isOpen()) go("");
        renderTags();
        loadComics();
      });
    });

    // 侧栏标签：点分类标题折叠 / 展开；点标签行勾选筛选，可多选（取交集）
    $("tagList").addEventListener("click", (e) => {
      const head = e.target.closest(".tag-group-head");
      if (head) { toggleTagGroup(head.dataset.ns || ""); return; }
      const row = e.target.closest(".tag-row");
      if (!row) return;
      toggleTag(row.dataset.tagName || "");
    });

    // 设置里的标签管理
    $("tagAdminList").addEventListener("click", (e) => {
      const row = e.target.closest("[data-admin-name]");
      if (!row) return;
      const name = row.dataset.adminName;
      if (e.target.closest("[data-act=edit]")) return startTagEdit(row, name);
      if (e.target.closest("[data-act=save]")) return commitTagEdit(row, name);
      if (e.target.closest("[data-act=cancel]")) return renderTagAdmin();
      if (e.target.closest("[data-act=remove]")) return deleteTag(name);
    });

    $("searchInput").addEventListener("input", (e) => {
      renderSearchSuggest(e.target.value);
      commitSearch(e.target.value);
    });
    $("searchInput").addEventListener("focus", (e) => renderSearchSuggest(e.target.value));
    $("searchInput").addEventListener("keydown", (e) => {
      const box = $("searchSuggest");
      if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !box.hidden && S.searchHits.length) {
        e.preventDefault();
        const n = S.searchHits.length;
        S.searchPick = e.key === "ArrowDown"
          ? (S.searchPick + 1) % n
          : (S.searchPick <= 0 ? n - 1 : S.searchPick - 1);
        paintSuggestPick();
        return;
      }
      if (e.key === "Enter") {
        if (!box.hidden && S.searchPick >= 0) {
          e.preventDefault();
          chooseSearchTag(S.searchPick);
        } else {
          commitSearch.cancel();
          hideSearchSuggest();
          pushNav();
          S.q = e.target.value.trim();
          if (Detail.isOpen()) go("");
          loadComics();
          e.target.blur();
        }
        return;
      }
      if (e.key === "Escape" && !box.hidden) {
        e.preventDefault();
        hideSearchSuggest();
      }
    });
    $("searchSuggest").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-i]");
      if (btn) chooseSearchTag(Number(btn.dataset.i));
    });
    $("searchSuggest").addEventListener("mousemove", (e) => {
      const btn = e.target.closest("button[data-i]");
      if (!btn) return;
      const i = Number(btn.dataset.i);
      if (i !== S.searchPick) { S.searchPick = i; paintSuggestPick(); }
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-wrap")) hideSearchSuggest();
    });

    $("sortSelect").addEventListener("change", (e) => {
      S.sort = e.target.value;
      loadComics();
    });

    $("viewBack")?.addEventListener("click", navBack);

    document.querySelectorAll(".vt-btn").forEach((b) => {
      b.addEventListener("click", () => {
        S.view = b.dataset.view;
        document.querySelectorAll(".vt-btn").forEach((x) => x.classList.toggle("active", x === b));
        shelf.dataset.view = S.view;
        localStorage.setItem("view", S.view);
      });
    });

    $("scanBtn").addEventListener("click", () => runScan(false));
    $("themeBtn").addEventListener("click", () => {
      applyTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
    });
    $("settingsBtn").addEventListener("click", () => openSettings());

    // 设置页：菜单 → 子页
    $("setBack").addEventListener("click", () => Settings.back());
    document.querySelectorAll(".set-row").forEach((row) => {
      row.addEventListener("click", () => Settings.go(row.dataset.go || ""));
    });
    $("tagSearch").addEventListener("input", debounce((e) => {
      S.tagQuery = e.target.value;
      renderTagAdmin();
    }, 140));

    $("tagAddBtn").addEventListener("click", () => openNewTag());
    $("newTagSave").addEventListener("click", createTag);
    $("newTagName").addEventListener("input", updateNewTagHint);
    $("newTagDisplay").addEventListener("input", updateNewTagHint);
    $("newTagDisplay").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); createTag(); }
    });
    $("newTagName").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); $("newTagDisplay").focus(); }
    });

    // 标签弹窗：每个分类一个下拉，选中即加入
    $("tagSelects").addEventListener("change", (e) => {
      const sel = e.target.closest("select[data-ns]");
      if (!sel || !sel.value) return;
      const name = sel.value;
      sel.value = "";
      addTagFromCatalog(name);
    });
    $("tagNewBtn").addEventListener("click", () => openNewTag(true));
    $("tagChips").addEventListener("click", (e) => {
      const ed = e.target.closest("[data-ei]");
      if (ed) { editChipDisplay(Number(ed.dataset.ei)); return; }
      const rm = e.target.closest("[data-ri]");
      if (!rm) return;
      S.editingTags.splice(Number(rm.dataset.ri), 1);
      renderChips();
    });
    $("tagSaveBtn").addEventListener("click", saveTags);

    // 设置弹窗
    $("pickBtn").addEventListener("click", async () => {
      const btn = $("pickBtn");
      btn.disabled = true;
      try {
        const res = await API.pickFolder();
        if (res && res.path) $("libPathInput").value = res.path;
        else if (!res || !res.path) toast("没有选择文件夹，也可以直接粘贴路径");
      } catch (err) {
        toast("无法打开选择框，请直接粘贴路径");
      } finally {
        btn.disabled = false;
      }
    });
    $("saveSettingsBtn").addEventListener("click", saveSettings);
    $("sidecarSyncBtn").addEventListener("click", async () => {
      const btn = $("sidecarSyncBtn");
      btn.disabled = true;
      btn.textContent = "写出中…";
      try {
        const res = await API.sidecarSync();
        if (res.tagFile) { S.tagFile = res.tagFile; updateTagFileUI(); }
        if (!res.enabled) toast("「标签跟随文件」还没开启，先打开开关再保存");
        else if (res.written) toast(`已为 ${res.written} 本写出标签文件`);
        else toast("所有作品的标签文件都是最新的");
      } catch (err) {
        toast("写出失败：" + err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "立即写出";
      }
    });
    $("clearTagsBtn").addEventListener("click", async () => {
      const total = (S.tags || []).length;
      if (!total) { toast("当前没有标签"); return; }
      const ok = confirm(
        `清空全部 ${total} 个标签？\n\n` +
        "标签会从所有作品上移除，每本旁的 json 和库根的 mangashelf-tags.json 也会一起清掉。\n" +
        "漫画文件和阅读进度不受影响。"
      );
      if (!ok) return;
      const btn = $("clearTagsBtn");
      btn.disabled = true;
      try {
        const res = await API.clearTags();
        S.tags = res.tags || [];
        S.tagFile = res.tagFile || S.tagFile;
        renderTags();
        updateTagFileUI();
        renderTagAdmin();
        await Promise.all([loadComics(), loadTags()]);
        toast(`已清空 ${res.removed} 个标签`);
      } catch (err) {
        toast("清空失败：" + err.message);
      } finally {
        btn.disabled = false;
      }
    });
    $("themeSeg").addEventListener("click", (e) => {
      const b = e.target.closest("[data-theme-val]");
      if (b) applyTheme(b.dataset.themeVal);
    });

    // 弹窗关闭
    document.querySelectorAll(".modal").forEach((modal) => {
      modal.addEventListener("click", (e) => {
        if (e.target === modal || e.target.closest("[data-close]")) modal.hidden = true;
      });
    });

    // 移动端抽屉
    $("menuBtn").addEventListener("click", () => document.body.classList.toggle("nav-open"));
    document.addEventListener("click", (e) => {
      if (!document.body.classList.contains("nav-open")) return;
      if (e.target.closest(".sidebar") || e.target.closest("#menuBtn")) return;
      closeNav();
    });

    // 快捷键
    document.addEventListener("keydown", (e) => {
      if (Reader.isOpen()) return;
      const typing = ["input", "textarea", "select"].includes((e.target.tagName || "").toLowerCase());
      if (e.key === "Escape") {
        if (!$("searchSuggest").hidden) { hideSearchSuggest(); return; }
        const opened = [...document.querySelectorAll(".modal")].filter((m) => !m.hidden);
        if (opened.length) {
          opened.forEach((m) => { m.hidden = true; });
        } else if (Settings.isOpen()) {
          Settings.back();
        } else if (Detail.isOpen()) {
          go("");
        }
        closeNav();
        if (typing && e.target.id === "searchInput") e.target.blur();
        return;
      }
      if (Settings.isOpen()) return;
      if (e.key === "/" && !typing) {
        e.preventDefault();
        if (Detail.isOpen()) go("");
        $("searchInput").focus();
      }
    });

    // 阅读器关闭后只更新进度条，避免整页重排
    Reader.onClose = () => {
      let changed = false;
      document.querySelectorAll(".card").forEach((card) => {
        const c = S.comics.find((x) => x.id === card.dataset.id);
        if (!c) return;
        const pct = c.pages > 0 && c.lastPage > 0
          ? Math.min(100, ((c.lastPage + 1) / c.pages) * 100) : 0;
        if (pct <= 0) return;
        let bar = card.querySelector(".card-progress");
        if (!bar) {
          bar = document.createElement("div");
          bar.className = "card-progress";
          card.querySelector(".card-cover").appendChild(bar);
        }
        bar.style.width = pct.toFixed(1) + "%";
        changed = true;
      });
      if (changed) renderCounts();

      // 从详情页进入的阅读器，关闭后回到详情；否则清掉深链回到书架
      const back = S.readerOrigin;
      S.readerOrigin = "";
      if (back) {
        if (location.hash.startsWith("#read=")) go("comic=" + back);
        else if (Detail.isOpen()) Detail.refresh();
      } else if (location.hash.startsWith("#read=")) {
        history.replaceState(null, "", location.pathname + location.search);
      }
    };

    bindContextMenu();
    bindSeries();
  }

  /* 系列页与主程序的接口：后退、进详情、进系列、改完刷新详情 */
  function bindSeries() {
    if (typeof Series === "undefined") return;
    window.openMenu = openCtxMenu;   // 详情页 / 系列页共用同一个菜单组件
    Series.onBack = () => navBack();
    Series.onOpenComic = (id) => {
      pushNav();
      goDetail(id);
    };
    Series.onOpenSeries = (name) => {
      pushNav();
      go("series=" + encodeURIComponent(name));
    };
    const refreshDetailIf = (test) => {
      if (Detail.isOpen() && test(Detail.currentId())) Detail.refresh();
    };
    Series.onComicChanged = () => refreshDetailIf(() => true);
    Series.onRenamed = () => refreshDetailIf(() => true);
    Series.onDissolved = () => refreshDetailIf(() => true);
  }

  function closeNav() {
    document.body.classList.remove("nav-open");
  }

  /* ---------------------------------------------------------------- 路由 */
  // #comic=<id>            漫画详情页
  // #read=<id>&p=<page>    阅读器（page 从 0 开始）
  // #settings[=page]       设置页（page 为空时是菜单首页）
  function go(hash) {
    const target = hash ? "#" + hash : "#";
    if (location.hash === target) Router.apply();
    else location.hash = target;
  }

  const goDetail = (id) => go("comic=" + id);

  async function openReader(id, page) {
    if (Reader.isOpen()) return;
    let comic = S.allComics.find((c) => c.id === id) || S.comics.find((c) => c.id === id);
    if (!comic) {
      try {
        comic = await API.comic(id);
      } catch (err) {
        toast("打不开这本漫画：" + err.message);
        go("");
        return;
      }
    }
    if (!comic || !comic.pages) {
      toast(comic?.error || "这本漫画暂时打不开");
      return;
    }
    S.readerOrigin = Detail.isOpen() ? Detail.currentId() : "";
    Reader.open(comic, {
      page: Number.isFinite(page) ? page : comic.lastPage,
      backLabel: S.readerOrigin ? "详情" : "书架",
    });
  }

  const Router = {
    apply() {
      closeCtxMenu();
      const p = new URLSearchParams(location.hash.replace(/^#/, ""));
      const rid = p.get("read");
      // 浏览器后退/前进离开阅读页时，收起阅读器
      if (!rid && Reader.isOpen()) Reader.close();
      if (rid) {
        const page = Number(p.get("p"));
        openReader(rid, Number.isFinite(page) ? page : NaN);
        return;
      }

      // 设置页：独立页面，自己管菜单与子页
      if (location.hash.startsWith("#settings")) {
        Series.hide();
        Settings.show(p.get("settings") || "");
        return;
      }
      Settings.hide();

      // 系列页：#series=<名字>
      const sname = p.get("series");
      if (sname) {
        if (Detail.isOpen()) Detail.close();
        Series.show(sname);
        return;
      }
      Series.hide();

      const cid = p.get("comic");
      if (cid) {
        Series.hide();
        Detail.open(cid);
        return;
      }
      if (Detail.isOpen()) Detail.close();
    },
  };

  function bindDetail() {
    Detail.onRead = (comic, page) => {
      const p = new URLSearchParams();
      p.set("read", comic.id);
      p.set("p", String(Math.max(0, Math.min(comic.pages - 1, page || 0))));
      go(p.toString());
    };
    Detail.onEditTags = (comic) => openTagModal(comic);
    // 详情页点系列名字：进系列页（后退能回到这本的详情）
    Detail.onOpenSeries = (name) => {
      pushNav();
      go("series=" + encodeURIComponent(name));
    };
    // 详情页改了作品分类：书架上的标签跟着变，不用整页重载
    Detail.onCategory = (comic) => {
      for (const list of [S.comics, S.allComics]) {
        const hit = (list || []).find((c) => c.id === comic.id);
        if (hit) hit.category = comic.category;
      }
      renderShelf();
    };
    // 详情页改了文件名：书架上的书名 / 路径跟着变，id 没变所以缓存仍可用
    Detail.onRename = (comic) => {
      for (const list of [S.comics, S.allComics]) {
        const hit = (list || []).find((c) => c.id === comic.id);
        if (hit) {
          hit.path = comic.path;
          hit.title = comic.title;
        }
      }
      renderShelf();
    };
    // 详情页重置了阅读进度：书架卡片上的进度条同步清掉
    Detail.onProgress = (comic) => {
      for (const list of [S.comics, S.allComics]) {
        const hit = (list || []).find((c) => c.id === comic.id);
        if (hit) hit.lastPage = comic.lastPage;
      }
      renderShelf();
    };
    // 更新状态 / 喜爱会改变侧栏归属：改完立即刷新计数和当前列表
    const syncDetailMeta = (comic) => {
      for (const list of [S.comics, S.allComics]) {
        const hit = (list || []).find((c) => c.id === comic.id);
        if (hit) {
          if ("status" in comic) hit.status = comic.status;
          if ("favorite" in comic) hit.favorite = comic.favorite;
        }
      }
      renderCounts();
      if (S.mode === "__updating__" || S.mode === "__favorite__") loadComics();
      else renderShelf();
    };
    Detail.onStatus = syncDetailMeta;
    Detail.onFavorite = syncDetailMeta;
    Detail.onFilterTag = (name) => {
      // 详情页点标签也算勾选，保留当前侧栏模式，得到「模式 ∩ 标签」
      pushNav();
      S.tagSel = [name];
      go("");
      renderTags();
      loadComics();
    };
    // 详情页的「返回书架」同样按后退栈走：从筛选结果进来的，退回那一个筛选结果
    Detail.onBack = () => navBack();
  }

  async function createTag() {
    const value = $("newTagName").value.trim();
    if (!value) return;
    const p = TagNS.parse(value);
    const ns = p.prefixed ? p.ns : (S.newTagNs || "mixed");
    const name = `${ns}:${p.value}`;
    const display = $("newTagDisplay").value.trim();
    try {
      const res = await API.createTag(name, display);
      S.tags = res.tags || [];
      renderTags();
      renderTagAdmin();
      $("newTagModal").hidden = true;
      const created = S.tags.find((t) => t.name === name) || { name, display };
      if (S.newTagFrom && S.tagTarget && !$("tagModal").hidden) {
        // 从「管理标签」里新建的，直接挂到这本上（仍需点保存才写入）
        addTagFromCatalog(created.name);
        renderTagSelects();
      }
      S.newTagFrom = false;
      toast(`标签「${TagNS.label(created)}」已创建`);
    } catch (err) {
      toast("创建失败：" + err.message);
    }
  }

  /* ---------------------------------------------------------------- 启动 */
  async function init() {
    applyTheme(localStorage.getItem("theme") || "dark");
    try {
      S.collapsedNs = new Set(JSON.parse(localStorage.getItem("tagGroupsCollapsed") || "[]"));
    } catch (_) { S.collapsedNs = new Set(); }
    S.view = localStorage.getItem("view") === "list" ? "list" : "grid";
    shelf.dataset.view = S.view;
    document.querySelectorAll(".vt-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === S.view);
    });

    bind();
    bindDetail();
    renderSkeleton();

    try {
      const st = await API.state();
      S.library = st.library;
      S.libraryExists = st.libraryExists;
      S.stats = st.stats;
      S.sidecar = !(st.config && st.config.sidecar === false);
      S.tagFile = (st.config && st.config.tagFile) || null;
      updateTagFileUI();
      updateLibInfo(st);
      updateScanUI(st.scan);
      if (st.scan && st.scan.running) {
        S.wasScanning = true;
        S.scanning = true;
        pollScan();
      }
    } catch (err) {
      toast("无法连接到本地服务：" + err.message);
    }

    await Promise.all([loadComics(), loadTags()]);

    // 首次使用且库为空时，主动引导
    if (!S.comics.length && S.libraryExists && !S.scanning) {
      renderEmpty();
    }

    // 支持 #comic=<id> 详情页与 #read=<id>&p=<page> 深链
    window.addEventListener("hashchange", () => Router.apply());
    Router.apply();

    // 不自动轮询：目录有新文件时点顶部「扫描」按钮手动刷新
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // PWA：手机「添加到主屏幕」后可以全屏使用。
  // 局域网走的是 http://192.168.x.x，浏览器不给注册 Service Worker，
  // 这里失败就是失败，静默跳过，不影响任何功能。
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    });
  }
})();
