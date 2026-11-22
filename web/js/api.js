/* API 封装：统一错误处理与 JSON 解析 */
const API = (() => {
  async function req(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { /* 非 JSON 响应 */ }
    if (!res.ok) {
      const msg = (data && data.error) || `${method} ${path} 失败 (${res.status})`;
      const err = new Error(msg);
      err.status = res.status;
      err.data = data || null;   // 保留响应体：调用方可能要拿 path 之类的信息
      throw err;
    }
    return data;
  }

  return {
    state: () => req("GET", "/api/state"),
    // 书架。tags 传数组时取交集（同时拥有这些标签）
    list: ({ tag = "", tags = [], q = "", sort = "recent" } = {}) => {
      const p = new URLSearchParams();
      const picked = (tags && tags.length) ? tags : (tag ? [tag] : []);
      for (const t of picked) p.append("tag", t);
      if (q) p.set("q", q);
      if (sort) p.set("sort", sort);
      return req("GET", "/api/comics?" + p.toString());
    },
    comic: (id) => req("GET", `/api/comics/${id}`),
    saveProgress: (id, page) => req("PUT", `/api/comics/${id}`, { lastPage: page }),
    saveCategory: (id, category) => req("PUT", `/api/comics/${id}`, { category }),
    saveStatus: (id, status) => req("PUT", `/api/comics/${id}`, { status }),
    saveFavorite: (id, favorite) => req("PUT", `/api/comics/${id}`, { favorite }),
    saveSeries: (id, series, seriesOrder) => req("PUT", `/api/comics/${id}`,
      seriesOrder === undefined ? { series } : { series, seriesOrder }),
    // 系列
    seriesList: () => req("GET", "/api/series"),
    seriesGet: (name) => req("GET", "/api/series?name=" + encodeURIComponent(name)),
    seriesOrder: (name, ids) => req("POST", "/api/series/order", { name, ids }),
    seriesRename: (from, to) => req("POST", "/api/series/rename", { from, to }),
    seriesDelete: (name) => req("DELETE", "/api/series?name=" + encodeURIComponent(name)),
    renameFile: (id, filename) => req("POST", `/api/comics/${id}/rename`, { filename }),
    revealComic: (id) => req("POST", `/api/comics/${id}/reveal`),
    setTags: (id, tags) => req("PUT", `/api/comics/${id}/tags`, { tags }),
    // 标签
    tags: () => req("GET", "/api/tags"),
    createTag: (name, display = "") => req("POST", "/api/tags", { name, display }),
    updateTag: (name, patch) => req("PUT", `/api/tags?name=${encodeURIComponent(name)}`, patch),
    deleteTag: (name) => req("DELETE", `/api/tags?name=${encodeURIComponent(name)}`),
    clearTags: () => req("POST", "/api/tags/clear"),
    // 扫描
    scan: (force = false) => req("POST", "/api/scan", { force }),
    progress: () => req("GET", "/api/scan/progress"),
    // 设置
    settings: () => req("GET", "/api/settings"),
    saveSettings: (patch) => req("PUT", "/api/settings", patch),
    pickFolder: () => req("POST", "/api/system/pick-folder"),
    reveal: () => req("POST", "/api/system/reveal"),
    // 标签跟随文件
    sidecarSync: (force = false) => req("POST", "/api/sidecar/sync", { force }),
  };
})();
