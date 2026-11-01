"""
ruri 的「廚房」：每天自動去樂天抓排行，整理好存成 data/latest.json。

流程：
  1. 從環境變數讀取金鑰（金鑰放在 GitHub Secrets，不寫在程式裡）
  2. 照 config/genres.json 的清單，一個分類一個分類去問樂天排行 API
  3. 套用排除規則（例如美容家電排除按摩器）
  4. 存成 data/latest.json（網站讀這個）和 data/history/日期.json（累積起來算本月熱門）

只用 Python 內建的工具，不需要另外安裝套件。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601"
SEARCH_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
GENRE_ENDPOINT = "https://openapi.rakuten.co.jp/ichibagt/api/IchibaGenre/Search/20260701"
TAIWAN = timezone(timedelta(hours=8))

# ① 讀取金鑰（在 GitHub 上設定成 Secrets）
APP_ID = os.environ.get("RAKUTEN_APP_ID", "")
ACCESS_KEY = os.environ.get("RAKUTEN_ACCESS_KEY", "")
AFFILIATE_ID = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
SITE_URL = os.environ.get("SITE_URL", "")  # 你的網站網址，要跟樂天「允許的網站」一致
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "")  # YouTube Data API 金鑰（沒有設定就跳過 YouTube）


def call_api(endpoint, params):
    """呼叫樂天 API（排行和商品搜尋共用）"""
    params = {"applicationId": APP_ID, "affiliateId": AFFILIATE_ID, "format": "json", "formatVersion": 2, **params}
    url = endpoint + "?" + urllib.parse.urlencode(params)
    origin = SITE_URL.rstrip("/")
    request = urllib.request.Request(url, headers={
        "accessKey": ACCESS_KEY,        # 存取密鑰放在標頭，不會出現在網址裡
        "Referer": SITE_URL,            # 告訴樂天「這是從我登記的網站發出的」
        "Origin": origin,
        "User-Agent": "ruri-ranking-bot",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_ranking(genre_id):
    """問樂天：這個分類今天賣最好的是哪些？"""
    return call_api(ENDPOINT, {"genreId": genre_id})


def first_image(item):
    """樂天的圖片欄位有兩種格式，都處理"""
    images = item.get("mediumImageUrls") or []
    if not images:
        return None
    first = images[0]
    url = first.get("imageUrl") if isinstance(first, dict) else first
    return url.replace("_ex=128x128", "_ex=300x300") if url else None


def simplify(item):
    """只留下網站會用到的欄位"""
    item = item.get("Item", item)  # 兩種回應格式都處理
    return {
        "rank": item.get("rank"),
        "itemCode": item.get("itemCode"),
        "itemName": item.get("itemName", ""),
        "price": int(item.get("itemPrice") or 0),
        "rating": float(item.get("reviewAverage") or 0),
        "reviews": int(item.get("reviewCount") or 0),
        "shopName": item.get("shopName", ""),
        "shopCode": item.get("shopCode", ""),
        "image": first_image(item),
        "url": item.get("affiliateUrl") or item.get("itemUrl", ""),
    }


RISK_WORDS = []   # main() 會從 config/genres.json 的 riskExclude 讀進來


def apply_rules(item, rule):
    """排除規則：名稱含有 exclude 的字就排除；有 keepOnly 時，名稱要含有其中一個字才保留；含有 riskExclude 的字一律排除"""
    name = item["itemName"]
    if any(word in name for word in RISK_WORDS):
        item["exclude"] = "法規或安全疑慮"
        return item
    reason = rule.get("excludeReason", "非美容相關")
    if any(word in name for word in rule.get("exclude", [])):
        item["exclude"] = reason
    keep = rule.get("keepOnly")
    if keep and not any(word in name for word in keep):
        item["exclude"] = reason
    return item


def main():
    missing = [n for n, v in [("RAKUTEN_APP_ID", APP_ID), ("RAKUTEN_ACCESS_KEY", ACCESS_KEY), ("SITE_URL", SITE_URL)] if not v]
    if missing:
        sys.exit("缺少設定：" + "、".join(missing) + "。請到 GitHub 的 Settings → Secrets 新增。")

    config = json.loads((ROOT / "config" / "genres.json").read_text(encoding="utf-8"))
    RISK_WORDS.extend(config.get("riskExclude", []))
    result = {"updated": datetime.now(TAIWAN).isoformat(timespec="minutes"), "groups": {}}
    failed = []

    # ② 一個分類一個分類去抓
    for key, rule in config["groups"].items():
        try:
            data = fetch_ranking(rule["genreId"])
            items = [apply_rules(simplify(i), rule) for i in data.get("Items", [])]
            result["groups"][key] = items
            print(f"✓ {rule['name']}：{len(items)} 個商品")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            print(f"✗ {rule['name']} 失敗（HTTP {e.code}）：{body}")
            failed.append(key)
        except Exception as e:  # 網路斷線等其他錯誤
            print(f"✗ {rule['name']} 失敗：{e}")
            failed.append(key)
        time.sleep(1.5)  # 每次之間休息 1.5 秒，避免問太快被樂天擋

    if not result["groups"]:
        sys.exit("全部分類都抓取失敗，這次不更新網站資料。")

    # 抓失敗的分類，沿用上一次的資料，網站才不會出現空白
    latest_path = ROOT / "data" / "latest.json"
    if failed and latest_path.exists():
        old = json.loads(latest_path.read_text(encoding="utf-8"))
        for key in failed:
            if key in old.get("groups", {}):
                result["groups"][key] = old["groups"][key]
                print(f"  → {key} 沿用上次的資料")

    # ③ 存檔
    text = json.dumps(result, ensure_ascii=False, indent=1)
    latest_path.write_text(text, encoding="utf-8")
    today = datetime.now(TAIWAN).strftime("%Y-%m-%d")
    history = ROOT / "data" / "history"
    history.mkdir(parents=True, exist_ok=True)
    (history / f"{today}.json").write_text(text, encoding="utf-8")
    print(f"已儲存 data/latest.json 和 data/history/{today}.json")

    # ④ 算本月熱門：統計過去 30 天，每個商品出現在排行榜上幾天
    build_monthly(history)
    # ⑤ 商品資料庫：所有上榜過的商品都留下來，下榜了也查得到
    build_archive(history)
    # ⑥ 達人推薦的商品：去樂天搜尋，抓圖片、價格和分潤連結
    build_experts()
    # ⑦ YouTube：最近 30 天的熱門美妝影片，以及說明欄提到的商品
    build_youtube()
    # ⑧ 品項排行：每個品項（腮紅、化妝水⋯）自己的樂天排行
    build_types(config)
    # ⑨ 年度熱門：去樂天搜尋得獎商品，抓圖片和價格
    build_awards()
    # ⑩ 日本選物店（Hands、無印良品）在樂天的官方店
    build_shops(config)


def youtube_get(path, params):
    url = "https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode({**params, "key": YOUTUBE_KEY})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ruri-bot"}), timeout=30) as r:
        return json.load(r)


def build_youtube():
    """用 config/youtube.json 的關鍵字搜尋影片，從說明欄找出提到品牌的那幾行，存成 data/youtube.json"""
    cfg_path = ROOT / "config" / "youtube.json"
    if not YOUTUBE_KEY or not cfg_path.exists():
        print("（沒有設定 YOUTUBE_API_KEY，略過 YouTube）")
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    brands = sorted(cfg.get("brands", []), key=len, reverse=True)
    genres = json.loads((ROOT / "config" / "genres.json").read_text(encoding="utf-8"))
    risk = genres.get("riskExclude", []) + ["まつ毛", "まつげ", "속눈썹", "탈모", "염색", "제모기", "니들", "스피큘"]
    since = (datetime.now(timezone.utc) - timedelta(days=cfg.get("days", 30))).strftime("%Y-%m-%dT%H:%M:%SZ")
    videos = {}
    try:
        for q in cfg["queries"]:
            res = youtube_get("search", {"part": "snippet", "q": q["q"], "type": "video", "order": "relevance",
                                         "publishedAfter": since, "maxResults": cfg.get("perQuery", 15),
                                         "relevanceLanguage": q.get("lang", "ja"), "regionCode": q.get("region", "JP")})
            for it in res.get("items", []):
                vid = it["id"]["videoId"]
                videos.setdefault(vid, {"country": q["country"], "cat": q["cat"]})
        ids = list(videos)
        out = []
        for i in range(0, len(ids), 50):
            res = youtube_get("videos", {"part": "snippet,statistics", "id": ",".join(ids[i:i + 50])})
            for v in res.get("items", []):
                sn, st = v["snippet"], v.get("statistics", {})
                views = int(st.get("viewCount", 0))
                if views < cfg.get("minViews", 0):
                    continue
                mentions = []
                for line in sn.get("description", "").splitlines():
                    line = line.strip(" ・-•*▶︎▷►✔︎✅☑︎◎○●□■◆◇☆★♡♥")
                    if any(w in line for w in risk):
                        continue   # 有法規或安全疑慮的商品不列出
                    if 4 <= len(line) <= 90 and "http" not in line and any(b.lower() in line.lower() for b in brands):
                        if line not in mentions:
                            mentions.append(line)
                if not mentions:
                    continue
                thumbs = sn.get("thumbnails", {})
                out.append({"id": v["id"], "title": sn["title"], "channel": sn["channelTitle"],
                            "date": sn["publishedAt"][:10], "views": views,
                            "thumb": (thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
                            "mentions": mentions[:12], **videos[v["id"]]})
        out.sort(key=lambda x: -x["views"])
        data = {"updated": datetime.now(TAIWAN).isoformat(timespec="minutes"), "videos": out[:40]}
        (ROOT / "data" / "youtube.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已更新 YouTube（{len(out)} 支影片有提到商品）")
    except Exception as e:  # YouTube 出錯不影響其他資料
        print(f"✗ YouTube 更新失敗：{e}")


def search_found(items, out_path, label):
    """items 裡每個有 kw 的商品去樂天搜尋一次，名稱要含有 mustHave 的每個字才算找到；結果存到 out_path"""
    old = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    found = {}
    for it in items:
        kw = " ".join(w for w in (it.get("kw") or "").split() if len(w) >= 2)   # 樂天不接受單一字元的關鍵字（例如 N、R）
        if not kw:
            continue
        try:
            data = call_api(SEARCH_ENDPOINT, {"keyword": kw, "hits": 10, "sort": "standard", "availability": 1})
            must = it.get("mustHave") or []
            hit = None
            results = data.get("Items") or data.get("items") or []   # 新版 API 可能用小寫 items
            for raw in results:
                item = simplify(raw)
                if any(w in item["itemName"] for w in RISK_WORDS):
                    continue
                flat = item["itemName"].lower().replace(" ", "").replace("　", "")
                if all(m.lower().replace(" ", "") in flat for m in must):
                    hit = item
                    break
            print(f"  {it['id']}：樂天搜尋到 {len(results)} 筆，{'有' if hit else '沒有'}符合的商品")
            if hit:
                found[it["id"]] = {k: hit[k] for k in ("itemCode", "itemName", "price", "rating", "reviews", "shopName", "image", "url")}
            elif it["id"] in old:
                found[it["id"]] = old[it["id"]]   # 這次找不到就沿用上次的
        except urllib.error.HTTPError as e:
            print(f"✗ {label}搜尋失敗（{it['id']}）HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:300]}")
            if it["id"] in old:
                found[it["id"]] = old[it["id"]]
        except Exception as e:
            print(f"✗ {label}搜尋失敗（{it['id']}）：{e}")
            if it["id"] in old:
                found[it["id"]] = old[it["id"]]
        time.sleep(1.2)
    out_path.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已更新{label}商品（找到 {len(found)} 個）")


def build_experts():
    """讀 data/experts.json，每個有 kw 的商品去樂天搜尋一次，結果存到 data/experts_items.json"""
    path = ROOT / "data" / "experts.json"
    if not path.exists():
        return
    experts = json.loads(path.read_text(encoding="utf-8"))
    search_found(experts.get("items", []), ROOT / "data" / "experts_items.json", "達人推薦")


def build_awards():
    """讀 data/awards.json，用「品牌＋日文商品名」（或 rkw）去樂天搜尋，結果存到 data/awards_items.json"""
    path = ROOT / "data" / "awards.json"
    if not path.exists():
        return
    try:
        awards = json.loads(path.read_text(encoding="utf-8"))
        def kw(a):
            if a.get("rkw"):
                return a["rkw"]
            name = re.sub(r"（.*?）|\(.*?\)", " ", a.get("name", ""))
            name = re.sub(r"[＜＞<>「」]", " ", name)
            extra = [w for w in a.get("mustHave", []) if w.lower() not in name.lower()]
            return " ".join([name] + extra).strip()
        items = [{**a, "kw": kw(a)} for a in awards.get("items", [])]
        search_found(items, ROOT / "data" / "awards_items.json", "年度熱門")
    except Exception as e:
        print(f"✗ 年度熱門搜尋失敗：{e}")


def genre_children(genre_id):
    """問樂天：這個分類底下有哪些子分類？回傳 [(genreId, 名稱)]"""
    data = call_api(GENRE_ENDPOINT, {"genreId": genre_id})
    out = []
    for c in data.get("children") or []:
        c = c.get("child", c)   # 兩種回應格式都處理
        gid = c.get("genreId")
        name = c.get("genreName") or c.get("nameJa") or ""
        if gid:
            out.append((int(gid), name))
    return out


def find_type_genres(config):
    """從大分類往下找子分類，名稱符合 match 的就記下來（例如「チーク」→ cheek）"""
    tg = config["typeGenres"]
    skip = tg.get("skip", [])
    def which(name):
        for key, words in tg["match"]:
            if any(w in name for w in words):
                return key
        return None
    found = {}   # 品項 → [{genreId, name, root}]
    seen = set()   # 已經看過的子分類（「美容綜合」底下會再遇到彩妝、保養，不重複找）
    descend = tg.get("descend", [])
    budget = {"calls": 0}

    def children(gid):
        try:
            kids = genre_children(gid)
        except urllib.error.HTTPError as e:
            print(f"✗ 子分類查詢失敗（{gid}）HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:200]}")
            kids = []
        except Exception as e:
            print(f"✗ 子分類查詢失敗（{gid}）：{e}")
            kids = []
        budget["calls"] += 1
        time.sleep(1.1)
        return kids

    def walk(start, root, max_depth, only=None, limit=200):
        """從 start 這個分類往下找，名稱符合的子分類記到 found；only 有寫時，只找這幾個品項"""
        queue = [(start, 1)]
        used = 0
        while queue and budget["calls"] < 320 and used < limit:
            gid, depth = queue.pop(0)
            used += 1
            for kid, name in children(gid):
                if kid in seen:
                    continue
                seen.add(kid)
                if any(w in name for w in skip):
                    print(f"  略過子分類 {name}（{kid}）")
                    continue
                key = None if name in descend else which(name)
                if key and only and key not in only:
                    key = None
                if key:
                    found.setdefault(key, []).append({"genreId": kid, "name": name, "root": root})
                    print(f"  子分類 {name}（{kid}）→ {key}")
                elif depth < max_depth:
                    queue.append((kid, depth + 1))

    for root in tg["roots"]:
        if root in config["groups"]:
            seen.add(config["groups"][root]["genreId"])
    for root in tg["roots"]:
        rule = config["groups"].get(root)
        if rule:
            walk(rule["genreId"], root, tg.get("maxDepth", 3))
    # 美容以外的大分類（日用品雜貨、廚房、包包⋯）：從樂天最上層找名稱相符的分類，只找指定的品項
    extras = tg.get("extraRoots", [])
    if extras:
        top = children(0)
        for ex in extras:
            hit = next(((gid, name) for gid, name in top if ex["nameHas"] in name), None)
            if not hit:
                print(f"✗ 樂天最上層找不到「{ex['nameHas']}」")
                continue
            print(f"  從「{hit[1]}」（{hit[0]}）找 {', '.join(ex['only'])}")
            walk(hit[0], ex.get("root", "daily"), ex.get("depth", 3), set(ex["only"]), ex.get("limit", 60))
    return found


def build_types(config):
    """品項排行：每個品項抓它的樂天子分類排行（一個品項有好幾個子分類時，合併後依名次排），存到 data/types.json"""
    if "typeGenres" not in config:
        return
    try:
        cache = ROOT / "data" / "type_genres.json"
        old = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        fresh = old.get("updated", "") >= (datetime.now(TAIWAN) - timedelta(days=30)).strftime("%Y-%m-%d")
        sig = json.dumps({k: config["typeGenres"].get(k) for k in ("roots", "match", "skip", "descend", "extraRoots")}, ensure_ascii=False, sort_keys=True)
        if old.get("types") and fresh and old.get("sig") == sig:
            genres = old["types"]
        else:
            print("重新尋找樂天子分類⋯")
            genres = find_type_genres(config)
            if genres:
                cache.write_text(json.dumps({"updated": datetime.now(TAIWAN).strftime("%Y-%m-%d"), "sig": sig, "types": genres}, ensure_ascii=False, indent=1), encoding="utf-8")
            else:
                genres = old.get("types", {})
        out_path = ROOT / "data" / "types.json"
        prev = json.loads(out_path.read_text(encoding="utf-8")).get("types", {}) if out_path.exists() else {}
        out = {}
        for key, glist in genres.items():
            merged = {}
            ok = False
            for g in glist[:3]:   # 一個品項最多抓 3 個子分類
                try:
                    data = fetch_ranking(g["genreId"])
                    rule = {k: v for k, v in config["groups"].get(g["root"], {}).items() if k != "keepOnly"}   # 子分類本身已經是品項，不用 keepOnly
                    for raw in data.get("Items", []):
                        item = apply_rules(simplify(raw), rule)
                        if item.get("exclude"):
                            continue
                        code = item["itemCode"]
                        if code not in merged or (item["rank"] or 99) < (merged[code]["rank"] or 99):
                            merged[code] = {**item, "root": g["root"]}
                    ok = True
                except urllib.error.HTTPError as e:
                    print(f"✗ 品項排行失敗（{key} {g['name']}）HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:200]}")
                except Exception as e:
                    print(f"✗ 品項排行失敗（{key} {g['name']}）：{e}")
                time.sleep(1.2)
            if ok:
                items = sorted(merged.values(), key=lambda x: x["rank"] or 99)[:30]
                out[key] = {"genres": [g["name"] for g in glist[:3]], "items": items}
                print(f"✓ 品項 {key}：{len(items)} 個商品")
            elif key in prev:
                out[key] = prev[key]   # 這次失敗就沿用上次的
        if out:
            data = {"updated": datetime.now(TAIWAN).isoformat(timespec="minutes"), "types": out}
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"已更新品項排行（{len(out)} 個品項）")
    except Exception as e:   # 品項排行出錯不影響其他資料
        print(f"✗ 品項排行更新失敗：{e}")


def build_shops(config):
    """日本選物店（Hands、無印良品）在樂天的官方店：抓美容分類裡評價數最多的商品，存到 data/shops.json"""
    cfg = config.get("shops")
    if not cfg:
        return
    out_path = ROOT / "data" / "shops.json"
    prev = json.loads(out_path.read_text(encoding="utf-8")).get("shops", {}) if out_path.exists() else {}
    out = {}
    for shop in cfg["list"]:
        try:
            code = shop.get("shopCode") or (prev.get(shop["key"]) or {}).get("shopCode", "")
            if not code:   # 不知道店家代號：先搜尋，找店名含 shopNameHas 的店
                data = call_api(SEARCH_ENDPOINT, {"keyword": shop["findKeyword"], "hits": 30})
                for raw in data.get("Items") or data.get("items") or []:
                    it = simplify(raw)
                    if shop["shopNameHas"] in it["shopName"]:
                        code = it["shopCode"]
                        print(f"  找到 {shop['name']} 的樂天店：{it['shopName']}（{code}）")
                        break
                time.sleep(1.2)
            if not code:
                print(f"✗ 找不到 {shop['name']} 的樂天店")
                if shop["key"] in prev:
                    out[shop["key"]] = prev[shop["key"]]
                continue
            data = call_api(SEARCH_ENDPOINT, {"shopCode": code, "genreId": cfg.get("genreId", 100939), "hits": 30,
                                              "sort": "-reviewCount", "availability": 1})
            items = []
            for raw in data.get("Items") or data.get("items") or []:
                it = simplify(raw)
                if shop["shopNameHas"] not in it["shopName"] or any(w in it["itemName"] for w in RISK_WORDS):
                    continue
                it["rank"] = len(items) + 1
                items.append(it)
            print(f"✓ {shop['name']}：{len(items)} 個商品")
            if items:
                out[shop["key"]] = {"name": shop["name"], "shopCode": code, "shopName": items[0]["shopName"], "items": items}
            elif shop["key"] in prev:
                out[shop["key"]] = prev[shop["key"]]
        except urllib.error.HTTPError as e:
            print(f"✗ {shop['name']} 失敗 HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:200]}")
            if shop["key"] in prev:
                out[shop["key"]] = prev[shop["key"]]
        except Exception as e:
            print(f"✗ {shop['name']} 失敗：{e}")
            if shop["key"] in prev:
                out[shop["key"]] = prev[shop["key"]]
        time.sleep(1.2)
    if out:
        data = {"updated": datetime.now(TAIWAN).isoformat(timespec="minutes"), "shops": out}
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def build_archive(history, keep_days=120):
    """把每天的排行合併成一份商品資料庫：最後一次的商品資料＋上榜紀錄＋價格變化"""
    files = sorted(history.glob("*.json"))[-keep_days:]
    products = {}
    for f in files:
        day = f.stem[:10]
        snapshot = json.loads(f.read_text(encoding="utf-8"))
        for group, items in snapshot.get("groups", {}).items():
            for item in items:
                code = item.get("itemCode")
                if not code:
                    continue
                p = products.setdefault(code, {"firstSeen": day, "groups": {}, "prices": {}})
                p["item"] = {k: v for k, v in item.items() if k != "rank"}
                p["lastSeen"] = day
                g = p["groups"].setdefault(group, {"best": 99, "days": 0, "last": None})
                g["best"] = min(g["best"], item.get("rank") or 99)
                g["days"] += 1
                g["last"] = item.get("rank")
                g["lastDay"] = day
                p["prices"][day] = item.get("price")
    for p in products.values():
        values = [v for v in p["prices"].values() if v]
        p["priceMin"], p["priceMax"] = (min(values), max(values)) if values else (None, None)
        p["prices"] = dict(list(p["prices"].items())[-30:])   # 只留最近 30 天的價格
    out = {"updated": datetime.now(TAIWAN).isoformat(timespec="minutes"), "days": len(files), "products": products}
    (ROOT / "data" / "products.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"已更新商品資料庫（共 {len(products)} 個商品）")


def build_monthly(history, days=30):
    files = sorted(history.glob("*.json"))[-days:]
    stats = {}  # 分類 → 商品代號 → 統計
    for f in files:
        snapshot = json.loads(f.read_text(encoding="utf-8"))
        for group, items in snapshot.get("groups", {}).items():
            bucket = stats.setdefault(group, {})
            for item in items:
                s = bucket.setdefault(item["itemCode"], {"days": 0, "rankSum": 0, "best": 99})
                s["days"] += 1
                s["rankSum"] += item["rank"] or 30
                s["best"] = min(s["best"], item["rank"] or 99)
                s["item"] = item  # 留下最新一天的商品資料（價格、圖片、連結）
    monthly = {"updated": datetime.now(TAIWAN).isoformat(timespec="minutes"),
               "days": len(files), "groups": {}}
    for group, bucket in stats.items():
        ranked = sorted(bucket.values(), key=lambda s: (-s["days"], s["rankSum"] / s["days"]))
        monthly["groups"][group] = [
            {**s["item"], "rank": i + 1, "daysOn": s["days"], "bestRank": s["best"],
             "avgRank": round(s["rankSum"] / s["days"], 1)}
            for i, s in enumerate(ranked[:30])
        ]
    (ROOT / "data" / "month.json").write_text(json.dumps(monthly, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已更新本月熱門（統計 {len(files)} 天）")


if __name__ == "__main__":
    main()
