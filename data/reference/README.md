# reference/

- `zipcode.json` — 中華郵政 6 碼郵遞區號路名對照表(縣市 → 鄉鎮市區 → 路名 → 門牌範圍/郵遞區號)。
  來源: https://github.com/gnehs/TaiwanZipcode (`src/assets/zipcode.json`),原始資料為中華郵政公開資料。
  用途: `scripts/step0_geocode.py` 用來把通訊處地址反查出所屬鄉鎮市區。
- `twtown2010.json` — 全台鄉鎮市區行政區界 GeoJSON(2010 年制,375 個 feature,含少數海域重複面/舊行政區名需另外對照)。
  來源: https://github.com/ronnywang/twgeojson (`twtown2010.3.json`)。
  用途: `scripts/step3_build_dashboard_layer.py` 畫儀表板的鄉鎮市區 choropleth 圖層。
