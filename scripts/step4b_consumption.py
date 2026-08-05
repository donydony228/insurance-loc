"""
Step 4(補充,可選項目): 平均消費支出。

TODO 裡標注這項「精度可能只到縣市」,主計總處家庭收支調查本來就只做到縣市層級的抽樣,沒有
鄉鎮市區精度,所以這裡不用假裝加工出鄉鎮市區數字,直接照 TODO 的做法退回縣市層級,
標成 fallback。

資料來源:行政院主計總處「家庭收支調查-平均每戶消費支出按區域別分」(data.gov.tw 資料集 9420)
  https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/232214/011-平均每戶消費支出按區域別分.csv
  寬表(年 x 20 個縣市),最新到 2024 年(113 年)。注意:家庭收支調查抽樣不含金門縣、連江縣,
  這兩縣輸出留空,分析時這兩縣要用其他 fallback(例如全國平均)或直接標記無資料。

用法:
  python3 scripts/step4b_consumption.py
"""

import csv
import os
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_TABLE_PATH = os.path.join(BASE_DIR, "data", "reference", "鄉鎮市區代碼表.csv")
OUT_DIR = os.path.join(BASE_DIR, "data", "income")
COUNTY_OUT_PATH = os.path.join(OUT_DIR, "縣市_平均消費支出.csv")
TOWN_FALLBACK_OUT_PATH = os.path.join(OUT_DIR, "鄉鎮市區_平均消費支出_縣市退回值.csv")

SOURCE_URL = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/232214/011-平均每戶消費支出按區域別分.csv"


def main():
    print(f"下載 {SOURCE_URL} ...")
    safe_url = urllib.parse.quote(SOURCE_URL, safe=":/")
    req = urllib.request.Request(safe_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig")
    reader = csv.reader(text.splitlines())
    header = next(reader)
    rows = list(reader)

    latest = rows[-1]
    latest_year = latest[0]
    county_cols = [h.replace("-元", "") for h in header[1:]]  # 跳過「年」
    county_values = {county: value for county, value in zip(county_cols, latest[1:])}

    os.makedirs(OUT_DIR, exist_ok=True)

    # 縣市層級原始表(存最新一年 + 全年度序列,方便之後要看趨勢)
    with open(COUNTY_OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["年度"] + county_cols)
        for r in rows:
            w.writerow(r)
    print(f"輸出縣市層級全年度表: {COUNTY_OUT_PATH} ({len(rows)} 個年度)")

    # 鄉鎮市區退回縣市值(方便直接跟其他鄉鎮市區級指標 join)
    with open(CODE_TABLE_PATH, encoding="utf-8-sig") as f:
        towns = list(csv.DictReader(f))

    missing_counties = set()
    with open(TOWN_FALLBACK_OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["行政區代碼", "縣市", "鄉鎮市區", "統計年度", "平均每戶消費支出_元", "資料層級"])
        for t in towns:
            city = t["縣市"]
            value = county_values.get(city, "")
            if not value:
                missing_counties.add(city)
            w.writerow([t["行政區代碼"], city, t["鄉鎮市區"], latest_year, value, "縣市退回值"])

    print(f"輸出鄉鎮市區退回值表: {TOWN_FALLBACK_OUT_PATH} ({len(towns)} 筆,最新年度 {latest_year})")
    if missing_counties:
        print(f"以下縣市家庭收支調查無抽樣資料,已留空: {sorted(missing_counties)}")


if __name__ == "__main__":
    main()
