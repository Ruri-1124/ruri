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
TAIWAN = timezone(timedelta(hours=8))

# ① 讀取金鑰（在 GitHub 上設定成 Secrets）
APP_ID = os.environ.get("RAKUTEN_APP_ID", "")
ACCESS_KEY = os.environ.get("RAKUTEN_ACCESS_KEY", "")
AFFILIATE_ID = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
SITE_URL = os.environ.get("SITE_URL", "")  # 你的網站網址，要跟樂天「允許的網站」一致


def fetch_ranking(genre_id):
    """問樂天：這個分類今天賣最好的是哪些？"""
    params = {
        "applicationId": APP_ID,
        "affiliateId": AFFILIATE_ID,
        "genreId": genre_id,
        "format": "json",
        "formatVersion": 2,
    }
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    origin = SITE_URL.rstrip("/")
    request = urllib.request.Request(url, headers={
        "accessKey": ACCESS_KEY,        # 存取密鑰放在標頭，不會出現在網址裡
        "Referer": SITE_URL,            # 告訴樂天「這是從我登記的網站發出的」
        "Origin": origin,
        "User-Agent": "ruri-ranking-bot",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


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


def apply_rules(item, rule):
    """排除規則：名稱含有 exclude 的字就排除；有 keepOnly 時，名稱要含有其中一個字才保留"""
    name = item["itemName"]
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


if __name__ == "__main__":
    main()
