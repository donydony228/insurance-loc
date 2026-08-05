"""
Step 1: 核心規模指標(鄉鎮市區級)。

資料來源:內政部戶政司 OPEN DATA API (https://www.ris.gov.tw/rs-opendata/api/Main/docs/v1)
  - ODRP012「動態資料統計表(新增區域代碼)」:村里級、月資料,含 district_code(區域別代碼,
    11 碼 = 5 碼縣市代碼 + 3 碼鄉鎮市區代碼 + 3 碼村里代碼),household_no(戶數)、
    people_total(人口數)。取前 8 碼、加總村里即得鄉鎮市區數字。
  - ODRP048「各鄉鎮市區人口密度」:鄉鎮市區級、年資料,直接提供 area(土地面積,km²)與
    population_density(人口密度),不需自算。

副產物:district_code 的前 8 碼本身就是內政部戶政司標準的鄉鎮市區代碼,一併輸出成代碼對照表,
對應 TODO 第 0 節「確認全台鄉鎮市區清單/代碼對照表」。

用法:
  python3 scripts/step1_population.py [yyymm] [yyy]
  預設抓最新可用月份/年份(目前為 114 年最新年資料、11506 最新月資料)。
"""

import csv
import json
import os
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF_PATH = os.path.join(BASE_DIR, "data", "reference", "zipcode.json")
CODE_TABLE_OUT = os.path.join(BASE_DIR, "data", "reference", "鄉鎮市區代碼表.csv")
POP_DIR = os.path.join(BASE_DIR, "data", "population")
POP_OUT = os.path.join(POP_DIR, "鄉鎮市區_人口統計.csv")

API_BASE = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore"
DEFAULT_YYYMM = "11506"
DEFAULT_YYY = "114"


def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def fetch_all_pages(code, period):
    page = 1
    rows = []
    while True:
        url = f"{API_BASE}/{code}/{period}?PAGE={page}"
        data = fetch_json(url)
        if data.get("responseCode") != "OD-0101-S":
            break
        rows.extend(data["responseData"])
        total_page = int(data.get("totalPage", "1") or "1")
        if page >= total_page:
            break
        page += 1
    return rows


def load_city_names():
    with open(REF_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    # zipcode.json 的頂層 key 已是內政部官方縣市名(含「臺」字形),依長度由長到短比對前綴
    return sorted(raw.keys(), key=len, reverse=True)


def split_site_id(site_id, city_names):
    for city in city_names:
        if site_id.startswith(city):
            return city, site_id[len(city):]
    return site_id, ""  # 東沙群島/南沙群島等特殊區域無鄉鎮市區層級


def aggregate_odrp012(rows):
    """村里 -> 鄉鎮市區(district_code 前 8 碼:5 碼縣市代碼 + 3 碼鄉鎮市區代碼)彙總戶數、人口數。

    直轄市的縣市代碼(如臺北市 63000、新北市 65000)後面接 3 碼鄉鎮市區碼才會分開各區,
    只取前 5 碼會把整個直轄市所有區合併成一筆,務必取到第 8 碼。
    """
    agg = {}  # code -> {site_id, household_no, people_total}
    for r in rows:
        code = r["district_code"][:8]
        entry = agg.setdefault(code, {"site_id": r["site_id"], "household_no": 0, "people_total": 0})
        entry["household_no"] += int(r["household_no"])
        entry["people_total"] += int(r["people_total"])
    return agg


def main():
    yyymm = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_YYYMM
    yyy = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_YYY

    city_names = load_city_names()

    print(f"下載 ODRP012 動態資料統計表(村里級,{yyymm})...")
    village_rows = fetch_all_pages("ODRP012", yyymm)
    print(f"  取得 {len(village_rows)} 筆村里資料")

    town_agg = aggregate_odrp012(village_rows)
    print(f"  彙總為 {len(town_agg)} 個鄉鎮市區")

    print(f"下載 ODRP048 各鄉鎮市區人口密度(年資料,{yyy})...")
    density_rows = fetch_all_pages("ODRP048", yyy)
    print(f"  取得 {len(density_rows)} 筆鄉鎮市區面積/密度資料")
    density_by_site = {r["site_id"]: r for r in density_rows}

    # 代碼對照表(Phase 0 附產物)
    os.makedirs(os.path.dirname(CODE_TABLE_OUT), exist_ok=True)
    with open(CODE_TABLE_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["行政區代碼", "縣市", "鄉鎮市區"])
        for code, entry in sorted(town_agg.items()):
            city, town = split_site_id(entry["site_id"], city_names)
            w.writerow([code, city, town])
    print(f"輸出代碼對照表: {CODE_TABLE_OUT} ({len(town_agg)} 筆)")

    # 人口/戶數/密度整合表(Phase 1)
    os.makedirs(POP_DIR, exist_ok=True)
    unmatched_density = 0
    with open(POP_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "行政區代碼", "縣市", "鄉鎮市區", "統計年月", "戶數", "人口數", "平均戶量",
            "土地面積_km2", "人口密度_人每km2", "面積密度統計年",
        ])
        for code, entry in sorted(town_agg.items()):
            city, town = split_site_id(entry["site_id"], city_names)
            household_no = entry["household_no"]
            people_total = entry["people_total"]
            avg_household_size = round(people_total / household_no, 3) if household_no else ""

            density_row = density_by_site.get(entry["site_id"])
            area = density_row["area"] if density_row else ""
            density = density_row["population_density"] if density_row else ""
            if density_row is None:
                unmatched_density += 1

            w.writerow([
                code, city, town, yyymm, household_no, people_total, avg_household_size,
                area, density, yyy if density_row else "",
            ])

    print(f"輸出人口統計表: {POP_OUT} ({len(town_agg)} 筆)")
    if unmatched_density:
        print(f"警告: {unmatched_density} 個鄉鎮市區在 ODRP048 找不到對應面積/密度資料(名稱可能不一致,需人工核對)")


if __name__ == "__main__":
    main()
