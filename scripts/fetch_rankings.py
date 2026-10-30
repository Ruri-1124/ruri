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


def build_experts():
    """讀 data/experts.json，每個有 kw 的商品去樂天搜尋一次，結果存到 data/experts_items.json"""
    path = ROOT / "data" / "experts.json"
    if not path.exists():
        return
    experts = json.loads(path.read_text(encoding="utf-8"))
    out_path = ROOT / "data" / "experts_items.json"
    old = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    found = {}
    for it in experts.get("items", []):
        kw = it.get("kw")
        if not kw:
            continue
        try:
            data = call_api(SEARCH_ENDPOINT, {"keyword": kw, "hits": 10, "sort": "standard", "availability": 1})
            must = it.get("mustHave") or []
            hit = None
            results = data.get("Items") or data.get("items") or []   # 新版 API 可能用小寫 items
            for raw in results:
                item = simplify(raw)
                if all(m.lower() in item["itemName"].lower() for m in must):
                    hit = item
                    break
            print(f"  {it['id']}：樂天搜尋到 {len(results)} 筆，{'有' if hit else '沒有'}符合的商品")
            if hit:
                found[it["id"]] = {k: hit[k] for k in ("itemCode", "itemName", "price", "rating", "reviews", "shopName", "image", "url")}
            elif it["id"] in old:
                found[it["id"]] = old[it["id"]]   # 這次找不到就沿用上次的
        except urllib.error.HTTPError as e:
            print(f"✗ 達人推薦搜尋失敗（{it['id']}）HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:300]}")
            if it["id"] in old:
                found[it["id"]] = old[it["id"]]
        except Exception as e:
            print(f"✗ 達人推薦搜尋失敗（{it['id']}）：{e}")
            if it["id"] in old:
                found[it["id"]] = old[it["id"]]
        time.sleep(1.5)
    out_path.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已更新達人推薦商品（找到 {len(found)} 個）")


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
