"""分类标签端到端验证：命名空间、显示名、详情页分行、侧栏分组、弹窗添加。

用法：D:/python/python.exe tools/test_tag_ns.py
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_detail import api, launch, wait, wait_scan  # noqa: E402
from shot import BASE, library_dir  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = library_dir()
TEST_NAME = "__tag_ns_test__.cbz"

passed = 0
failed = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label} {extra}")


def make_archive() -> None:
    out = LIB / TEST_NAME
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i in range(5):
            im = Image.new("RGB", (600, 900), (26, 30, 48))
            d = ImageDraw.Draw(im)
            d.text((60, 60), f"TAG PAGE {i + 1}", fill=(240, 240, 250))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())


def find_id() -> str:
    for item in api("/api/comics")["items"]:
        if item["title"] == "__tag_ns_test__":
            return item["id"]
    raise SystemExit("找不到测试作品")


CASES = [
    {"name": "language:chinese"},  # 不给 display，应自动显示「汉语」
    {"name": "parody:test_series", "display": "测试原作"},
    {"name": "character:test_char", "display": "测试角色"},
    {"name": "artist:test_artist", "display": "测试画师"},
    {"name": "group:test_circle", "display": "测试社团"},
    {"name": "female:test_female", "display": "测试女性"},
    {"name": "male:test_male", "display": "测试男性"},
    {"name": "测试其他"},
    {"name": "other:test_other", "display": "测试其他分类"},
]
CLEANUP = []  # 实际写入后的规范名在 PUT 之后再补，见 main()


def main() -> None:
    # 库里本来就有同名标签时，测试结束后绝不能删掉（那是用户的数据）
    preexisting = {t["name"] for t in api("/api/tags")["tags"]}
    make_archive()
    wait_scan()
    cid = find_id()
    print(f"测试作品 id={cid}")

    print("1) 接口：分类、显示名、排序")
    res = api(f"/api/comics/{cid}/tags", "PUT", {"tags": CASES})
    tags = res["tags"]
    CLEANUP.extend(t["name"] for t in tags)
    CLEANUP.extend(["female:test_ui", "language:japanese", "language:thai",
                    "group:legacy_test", "other:legacy_test"])
    check("返回 9 个标签", len(tags) == 9, str(len(tags)))
    check("分类顺序正确",
          [t["nsLabel"] for t in tags] ==
          ["语言", "原作", "角色", "艺术家", "社团", "女性", "男性", "混合", "其他"],
          json.dumps([t["nsLabel"] for t in tags], ensure_ascii=False))
    plain_tag = next((t for t in tags if t["value"] == "测试其他"), {})
    check("没写分类的标签存成 mixed:", plain_tag.get("name") == "mixed:测试其他",
          json.dumps(plain_tag, ensure_ascii=False))
    check("社团分类用 group:", any(t["name"] == "group:test_circle" for t in tags),
          json.dumps([t["name"] for t in tags], ensure_ascii=False))
    check("language:chinese 自动显示为汉语", tags[0]["label"] == "汉语", tags[0]["label"])
    check("自定义显示名生效", tags[3]["label"] == "测试画师", tags[3]["label"])
    check("原始标签名保留", tags[0]["name"] == "language:chinese", tags[0]["name"])

    all_tags = {t["name"]: t for t in api("/api/tags")["tags"]}
    check("标签目录含分类信息",
          all_tags.get("language:chinese", {}).get("ns") == "language"
          and all_tags.get("language:chinese", {}).get("nsLabel") == "语言")

    alias = api("/api/tags", "POST", {"name": "lang:japanese"})["tag"]
    check("别名 lang: 归一化为 language:", alias["name"] == "language:japanese", alias["name"])
    check("日语显示名", alias["label"] == "日语", alias["label"])

    legacy = api("/api/tags", "POST", {"name": "circle:legacy_test"})["tag"]
    check("旧写法 circle: 归一化为 group:", legacy["name"] == "group:legacy_test", legacy["name"])
    check("社团分类名是「社团」", legacy["nsLabel"] == "社团", legacy["nsLabel"])

    other_tag = next((t for t in tags if t["name"] == "other:test_other"), {})
    check("新分类 other 存成 other:", other_tag.get("name") == "other:test_other",
          json.dumps(other_tag, ensure_ascii=False))
    check("新分类显示名是「其他」", other_tag.get("nsLabel") == "其他", str(other_tag.get("nsLabel")))
    check("自定义显示名保留", other_tag.get("label") == "测试其他分类", str(other_tag.get("label")))

    legacy_other = api("/api/tags", "POST", {"name": "misc:legacy_test"})["tag"]
    check("别名 misc: 归一化为 other:", legacy_other["name"] == "other:legacy_test", legacy_other["name"])
    check("其他分类名是「其他」", legacy_other["nsLabel"] == "其他", legacy_other["nsLabel"])

    print("2) 按原始标签名筛选")
    hit = api("/api/comics?tag=" + urllib.parse.quote("language:chinese"))
    check("筛选命中测试作品",
          any(i["id"] == cid for i in hit["items"]),
          json.dumps([i["title"] for i in hit["items"]], ensure_ascii=False))

    proc, cdp = launch(1440, 900)
    try:
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait(cdp, "!document.getElementById('detail').hidden", 25)
        wait(cdp, "document.querySelectorAll('#detailTags .tag-line').length >= 9", 15)

        print("3) 详情页按分类分行")
        rows = cdp.js("""[...document.querySelectorAll('#detailTags .tag-line')].map(r => ({
            label: (r.querySelector('.tag-line-label') || {}).textContent || '',
            chips: [...r.querySelectorAll('.d-tag')].map(c => c.textContent.trim())
        }))""")
        labels = [r["label"] for r in rows if r["label"]]
        check("分类行顺序与命名一致",
              labels == ["语言", "原作", "角色", "艺术家", "社团", "女性", "男性", "混合", "其他"],
              json.dumps(labels, ensure_ascii=False))
        lang = next((r for r in rows if r["label"] == "语言"), {"chips": []})
        check("语言行显示「汉语」而非 chinese", "汉语" in lang["chips"], json.dumps(lang["chips"], ensure_ascii=False))
        plain = next((r for r in rows if r["label"] == "混合"), {"chips": []})
        check("普通标签归入混合", "测试其他" in plain["chips"], json.dumps(plain["chips"], ensure_ascii=False))
        other_row = next((r for r in rows if r["label"] == "其他"), {"chips": []})
        check("other: 标签归入「其他」行", "测试其他分类" in other_row["chips"],
              json.dumps(other_row["chips"], ensure_ascii=False))

        print("4) 标签弹窗分组 + 原始名")
        cdp.js("document.getElementById('detailEdit').click()")
        cdp.js("document.getElementById('dAddTag').click()")
        wait(cdp, "!document.getElementById('tagModal').hidden", 10)
        modal = cdp.js("""({
            groups: [...document.querySelectorAll('#tagChips .chip-group-label')].map(e => e.textContent.trim()),
            raws: [...document.querySelectorAll('#tagChips .chip-text em')].map(e => e.textContent.trim()),
            selectCats: [...document.querySelectorAll('#tagSelects .cat-name')].map(e => e.textContent.trim()),
            selects: document.querySelectorAll('#tagSelects select').length,
            hasInput: !!document.getElementById('tagInput'),
        })""")
        check("弹窗按分类分组", modal["groups"] ==
              ["语言", "原作", "角色", "艺术家", "社团", "女性", "男性", "混合", "其他"],
              json.dumps(modal["groups"], ensure_ascii=False))
        check("原始标签名可见", "language:chinese" in modal["raws"], json.dumps(modal["raws"], ensure_ascii=False))
        check("每个分类一个下拉", modal["selects"] == len(modal["selectCats"]) and modal["selects"] >= 1,
              json.dumps(modal, ensure_ascii=False))
        check("下拉分类名合法", all(
              c in ["语言", "原作", "角色", "艺术家", "社团", "女性", "男性", "混合", "其他"]
              for c in modal["selectCats"]),
              json.dumps(modal["selectCats"], ensure_ascii=False))
        check("标签输入框已改为下拉", not modal["hasInput"], str(modal["hasInput"]))

        print("5) 通过下拉添加标签（显示名自动推导）")
        api("/api/tags", "POST", {"name": "female:test_ui"})
        api("/api/tags", "POST", {"name": "language:thai"})
        # 重开弹窗，让它重新读取一次标签目录
        cdp.js("document.getElementById('tagModal').hidden = true")
        cdp.js("document.getElementById('dAddTag').click()")
        wait(cdp, "!document.getElementById('tagModal').hidden", 10)
        check("新建标签出现在下拉里", wait(cdp,
              "[...document.querySelectorAll('#tagSelects select')]"
              ".some(s => [...s.options].some(o => o.value === 'female:test_ui'))", 10))
        cdp.js("""(() => {
            const s = [...document.querySelectorAll('#tagSelects select')]
              .find(x => [...x.options].some(o => o.value === 'female:test_ui'));
            s.value = 'female:test_ui';
            s.dispatchEvent(new Event('change', { bubbles: true }));
        })()""")
        time.sleep(0.3)
        added = cdp.js("[...document.querySelectorAll('#tagChips .chip-text em')].map(e => e.textContent.trim())")
        check("界面添加的原始名为 female:test_ui", "female:test_ui" in added, json.dumps(added, ensure_ascii=False))
        chips = cdp.js("[...document.querySelectorAll('#tagChips .chip-text b')].map(e => e.textContent.trim())")
        check("未填显示名时按原文显示", "test_ui" in chips, json.dumps(chips, ensure_ascii=False))
        check("弹窗内已无显示名输入框",
              cdp.js("!document.getElementById('tagDisplay')"))

        # 语言分类下选 thai，应自动显示「泰语」
        cdp.js("""(() => {
            const s = [...document.querySelectorAll('#tagSelects select')]
              .find(x => [...x.options].some(o => o.value === 'language:thai'));
            s.value = 'language:thai';
            s.dispatchEvent(new Event('change', { bubbles: true }));
        })()""")
        time.sleep(0.3)
        chips2 = cdp.js("[...document.querySelectorAll('#tagChips .chip-text b')].map(e => e.textContent.trim())")
        check("语言标签自动显示为「泰语」", "泰语" in chips2, json.dumps(chips2, ensure_ascii=False))

        cdp.js("document.getElementById('tagSaveBtn').click()")
        wait(cdp, "document.getElementById('tagModal').hidden", 12)
        saved = {t["name"]: t for t in api(f"/api/comics/{cid}")["tags"]}
        check("保存后写入 female:test_ui", "female:test_ui" in saved, json.dumps(list(saved), ensure_ascii=False))
        check("保存后按原文显示", saved.get("female:test_ui", {}).get("label") == "test_ui",
              json.dumps(saved.get("female:test_ui"), ensure_ascii=False))
        check("保存后 language:thai 显示泰语", saved.get("language:thai", {}).get("label") == "泰语",
              json.dumps(saved.get("language:thai"), ensure_ascii=False))

        print("6) 修改已有标签的显示名")
        api("/api/tags?name=" + urllib.parse.quote("female:test_ui"), "PUT", {"display": "改后显示"})
        again = {t["name"]: t for t in api(f"/api/comics/{cid}")["tags"]}
        check("显示名已更新", again.get("female:test_ui", {}).get("label") == "改后显示",
              json.dumps(again.get("female:test_ui"), ensure_ascii=False))

        print("7) 侧栏按分类分组")
        cdp.js("document.getElementById('tagModal').hidden = true")
        cdp.js("location.hash = ''")
        wait(cdp, "!document.getElementById('shelfView').hidden", 10)
        time.sleep(0.5)
        side = cdp.js("""({
            groups: [...document.querySelectorAll('#tagList .tag-group-head')].map(e => e.childNodes[0].textContent.trim()),
            names: [...document.querySelectorAll('#tagList .tname')].map(e => e.textContent.trim()),
        })""")
        check("侧栏出现分类标题", "语言" in side["groups"] and "女性" in side["groups"],
              json.dumps(side["groups"], ensure_ascii=False))
        check("侧栏显示的是显示名", "汉语" in side["names"] and "language:chinese" not in side["names"],
              json.dumps(side["names"], ensure_ascii=False))

        errs = cdp.js("window.__errors || []")
        check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        try:
            for suffix in (".json", ".mangashelf.json", ""):
                (LIB / (TEST_NAME + suffix)).unlink(missing_ok=True)
        except OSError:
            pass
        wait_scan()
        for name in CLEANUP:
            if name in preexisting:
                continue
            try:
                api("/api/tags?name=" + urllib.parse.quote(name), "DELETE")
            except Exception:
                pass

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
