/* 标签分类：语言 / 原作 / 角色 / 艺术家 / 社团 / 女性 / 男性 / 混合 / 其他
   存储在库里的名字形如 language:chinese，界面显示为「语言 · 汉语」。
   社团用 group（circle 是旧写法）；没写分类的普通标签归入 mixed（混合），
   显式 other: / misc: / tag: 归入 other（其他）。 */
const TagNS = (() => {
  const ORDER = ["language", "parody", "character", "artist", "group", "female", "male", "mixed", "other"];
  const LABELS = {
    language: "语言",
    parody: "原作",
    character: "角色",
    artist: "艺术家",
    group: "社团",
    female: "女性",
    male: "男性",
    mixed: "混合",
    other: "其他",
  };
  const ALIASES = {
    language: "language", lang: "language", languages: "language",
    parody: "parody", series: "parody", original: "parody", origin: "parody",
    character: "character", char: "character", chara: "character", role: "character",
    artist: "artist", author: "artist", art: "artist", illustrator: "artist",
    group: "group", circle: "group", club: "group", publisher: "group",
    female: "female", females: "female",
    male: "male", males: "male",
    mixed: "mixed",
    other: "other", misc: "other", tag: "other",
  };
  /* 常见取值的默认显示名（仅用于界面，不改存储的标签名） */
  const DISPLAY = {
    "language:chinese": "汉语",
    "language:zh": "汉语",
    "language:zh-cn": "简体中文",
    "language:zh-tw": "繁体中文",
    "language:simplified": "简体",
    "language:traditional": "繁体",
    "language:japanese": "日语",
    "language:ja": "日语",
    "language:english": "英语",
    "language:en": "英语",
    "language:korean": "韩语",
    "language:ko": "韩语",
    "language:cantonese": "粤语",
    "language:spanish": "西班牙语",
    "language:es": "西班牙语",
    "language:french": "法语",
    "language:fr": "法语",
    "language:german": "德语",
    "language:de": "德语",
    "language:russian": "俄语",
    "language:ru": "俄语",
    "language:thai": "泰语",
    "language:vietnamese": "越南语",
    "language:indonesian": "印尼语",
    "language:portuguese": "葡萄牙语",
    "language:translated": "翻译",
    "language:raw": "生肉",
  };

  /* 拆分标签名 → { name: 规范名, ns: 分类, value: 内容, prefixed: 用户是否写了分类前缀 } */
  function parse(raw) {
    const text = String(raw || "").trim();
    if (!text) return { name: "", ns: "mixed", value: "", prefixed: false };
    const i = text.indexOf(":");
    if (i > 0) {
      const key = text.slice(0, i).trim().toLowerCase();
      const value = text.slice(i + 1).trim();
      if (value && Object.prototype.hasOwnProperty.call(ALIASES, key)) {
        const ns = ALIASES[key];
        return { name: `${ns}:${value}`, ns, value, prefixed: true };
      }
    }
    // 没写分类（或写了不认识的前缀）：整个字符串当内容，归入「混合」
    if (text.toLowerCase().startsWith("mixed:")) {
      return { name: text, ns: "mixed", value: text.slice(6).trim(), prefixed: false };
    }
    return { name: `mixed:${text}`, ns: "mixed", value: text, prefixed: false };
  }

  function nsOf(tag) {
    if (!tag) return "mixed";
    if (typeof tag === "object") {
      if (tag.ns) return ALIASES[tag.ns] || tag.ns;
      return parse(tag.name || "").ns;
    }
    return parse(tag).ns;
  }

  /* 界面显示名：优先用后端给的 label，其次内置翻译，最后用原值 */
  function label(tag) {
    if (!tag) return "";
    if (typeof tag === "string") {
      const p = parse(tag);
      return DISPLAY[`${p.ns}:${p.value.toLowerCase()}`] || p.value;
    }
    if (tag.label) return tag.label;
    const p = parse(tag.name);
    return DISPLAY[`${p.ns}:${p.value.toLowerCase()}`] || p.value;
  }

  /* 分类内排序：只看标签名本身（去掉分类前缀的内容），A→Z，与显示名无关 */
  const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

  function sortValue(tag) {
    if (!tag) return "";
    if (typeof tag === "string") return parse(tag).value;
    return parse(tag.name || "").value;
  }

  function compare(a, b) {
    const byValue = collator.compare(sortValue(a), sortValue(b));
    if (byValue) return byValue;
    return collator.compare((a && a.name) || "", (b && b.name) || "");
  }

  /* 分组：只返回有内容的分类，顺序固定；每组内部按标签名 A→Z */
  function group(tags) {
    const buckets = new Map();
    for (const t of tags || []) {
      const ns = nsOf(t);
      if (!buckets.has(ns)) buckets.set(ns, []);
      buckets.get(ns).push(t);
    }
    // 已知分类按固定顺序在前，万一出现没登记的分类也不吞掉
    const known = ORDER.filter((ns) => buckets.has(ns) && buckets.get(ns).length);
    const extra = [...buckets.keys()].filter((ns) => !ORDER.includes(ns) && buckets.get(ns).length);
    return known.concat(extra).map((ns) => ({
      ns,
      label: LABELS[ns] || "混合",
      tags: buckets.get(ns).slice().sort(compare),
    }));
  }

  return {
    ORDER,
    LABELS,
    ALIASES,
    parse,
    nsOf,
    label,
    group,
    compare,
    sortValue,
    labelOf: (ns) => LABELS[ns] || "混合",
  };
})();
