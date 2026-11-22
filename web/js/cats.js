/* 作品分类：描述作品本身属于哪一类，和标签（语言、原作、角色…）是两回事。
   存储用短键（dojinshi / manga / artistcg / gamecg / non-h / imageset），
   界面显示官方写法（Dojinshi / Manga / ArtistCG / GameCG / Non-H / ImageSet）。 */
const ComicCat = (() => {
  const LIST = [
    { key: "dojinshi", label: "Dojinshi", color: "#ff6b9d" },   // 红
    { key: "manga", label: "Manga", color: "#ff9f43" },        // 橙黄
    { key: "artistcg", label: "ArtistCG", color: "#ffd166" },  // 浅黄
    { key: "gamecg", label: "GameCG", color: "#2fd39a" },      // 绿
    { key: "non-h", label: "Non-H", color: "#38bdf8" },        // 天蓝
    { key: "imageset", label: "ImageSet", color: "#a06bff" },  // 紫
  ];
  const BY_KEY = Object.fromEntries(LIST.map((c) => [c.key, c]));
  const ALIASES = {
    dojinshi: "dojinshi", doujinshi: "dojinshi", doujin: "dojinshi", dojin: "dojinshi",
    manga: "manga", comic: "manga",
    artistcg: "artistcg", "artist cg": "artistcg", artcg: "artistcg", cg: "artistcg",
    gamecg: "gamecg", "game cg": "gamecg",
    "non-h": "non-h", nonh: "non-h", "non h": "non-h", "non-hentai": "non-h", safe: "non-h",
    imageset: "imageset", "image set": "imageset", "image-set": "imageset",
  };

  function norm(value) {
    const k = String(value || "").trim().toLowerCase().replace(/_/g, " ");
    if (!k) return "";
    if (BY_KEY[k]) return k;
    return ALIASES[k] || "";
  }

  const label = (value) => (BY_KEY[norm(value)] || {}).label || "";
  const color = (value) => (BY_KEY[norm(value)] || {}).color || "var(--muted)";
  const has = (value) => !!BY_KEY[norm(value)];

  return { LIST, norm, label, color, has };
})();
