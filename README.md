# ruri 日韓熱賣情報站

每天自動更新的日韓美妝熱賣排行網站。

## 檔案說明

| 檔案 | 用途 | 比喻 |
|---|---|---|
| `index.html` | 網站本體（畫面） | 用餐區 |
| `scripts/fetch_rankings.py` | 每天去樂天抓排行的程式 | 廚房 |
| `.github/workflows/daily.yml` | 排程：每天早上 6 點叫廚房開工 | 鬧鐘 |
| `config/genres.json` | 要抓哪些分類、排除規則 | 採購清單 |
| `data/latest.json` | 最新的排行資料（自動產生） | 今天的菜 |
| `data/history/` | 每天的排行紀錄（自動產生） | 冰箱 |
| `data/labels.json` | 商品的中文名稱、品牌、國家（手動整理） | 菜單說明 |

## 需要設定的 Secrets

在 GitHub 專案的 **Settings → Secrets and variables → Actions** 新增：

| 名稱 | 內容 |
|---|---|
| `RAKUTEN_APP_ID` | 樂天應用程式 ID |
| `RAKUTEN_ACCESS_KEY` | 樂天存取密鑰 |
| `RAKUTEN_AFFILIATE_ID` | 樂天聯盟 ID |
| `SITE_URL` | 網站網址，例如 `https://你的帳號.github.io/ruri/` |

## 新增商品的中文名稱

打開 `data/labels.json`，用商品的 `itemCode`（在 `data/latest.json` 裡找）新增一筆：

```json
"店家代號:商品編號": { "brand": "品牌", "name": "中文商品名", "country": "日本" }
```

`country` 可以填：日本、韓國、其他。
