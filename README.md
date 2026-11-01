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
| `data/awards.json` | 年度熱門：日本美妝大賞得獎商品（手動整理） | 得獎菜色 |

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

## 品項分類（腮紅、化妝水⋯）

網站會用商品名稱自動判斷品項，規則在 `index.html` 的 `ITYPES`。如果某個商品被分錯，在 `data/labels.json` 那筆加上 `"type"` 就能手動指定，例如：

```json
"店家代號:商品編號": { "brand": "品牌", "desc": "腮紅", "type": "cheek" }
```

品項代號：foundation 粉底、primer 妝前、concealer 遮瑕、powder 蜜粉、cheek 腮紅、highlight 打亮修容、eye 眼影、liner 眼線、mascara 睫毛膏、lash 睫毛保養、brow 眉彩、lip 唇彩、tool 刷具、makeupset 彩妝組、cleansing 卸妝、wash 洗面乳、toner 化妝水、serum 精華液、emulsion 乳液乳霜、mask 面膜、sunscreen 防曬、skinset 保養組、body 身體、shampoo 洗潤髮、hairoil 護髮油、styling 造型、scalp 頭皮梳子、dryer 吹風機、iron 離子夾、facial 美顏器、shaver 除毛、detergent 洗衣精、softener 柔軟精、shower 蓮蓬頭、clean 清潔消毒、bath 泡澡沐浴。

## 更新年度熱門

打開 `data/awards.json`，在 `items` 新增一筆，`awards` 寫得了哪個獎（`award` 要對應上面 `awards` 清單的 `key`）。`mustHave` 是在樂天排行裡找同一個商品的關鍵字，找到就會自動用樂天的圖片和價格。
