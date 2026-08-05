"""
Step 3: 成長與遷徙指標(鄉鎮市區級)。

資料來源:內政部戶政司 OPEN DATA API(同 Phase 1/2,https://www.ris.gov.tw/rs-opendata/api/Main/docs/v1)
  - ODRP048「各鄉鎮市區人口密度」(年資料):抓 109 年、114 年兩個年底人口數,算近 5 年人口成長率。
  - ODRP011「遷入遷出統計表(新增區域代碼)」(村里級、月資料):in_total_m/f、out_total_m/f。
  - ODRP012「動態資料統計表(新增區域代碼)」(村里級、月資料,跟 Phase 1 用同一支):
    birth_total、death_m、death_f。

出生/死亡/遷入遷出這三份都是月資料,但 API 只保留約 18 個月的滾動窗口(嘗試過 113 年前面
幾個月已經查無資料),抓不到完整「114 年整年」。改抓最近 12 個月的滾動窗口
(114/07 ~ 115/06,對齊 Phase 1/2 用的 11506 這個月),同時當作近似的年出生/死亡/遷徙數,
三份指標(粗出生率、自然增加率、淨遷入率)分母都用 Phase 1 產出的 11506 人口數,口徑一致。

用法:
  python3 scripts/step3_growth_migration.py
"""

import csv
import json
import os
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_TABLE_PATH = os.path.join(BASE_DIR, "data", "reference", "鄉鎮市區代碼表.csv")
POP_PATH = os.path.join(BASE_DIR, "data", "population", "鄉鎮市區_人口統計.csv")
OUT_DIR = os.path.join(BASE_DIR, "data", "population", "growth_migration")
OUT_PATH = os.path.join(OUT_DIR, "鄉鎮市區_成長遷徙指標.csv")

API_BASE = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore"

GROWTH_START_YYY = "109"
GROWTH_END_YYY = "114"
# 近 12 個月滾動窗口,對齊 Phase 1/2 的 11506
ROLLING_MONTHS = ["11407", "11408", "11409", "11410", "11411", "11412",
                   "11501", "11502", "11503", "11504", "11505", "11506"]


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


def load_code_table():
    with open(CODE_TABLE_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # code -> (縣市, 鄉鎮市區, site_id 組字串)
    return {r["行政區代碼"]: (r["縣市"], r["鄉鎮市區"], r["縣市"] + r["鄉鎮市區"]) for r in rows}


def load_reference_population():
    with open(POP_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {r["行政區代碼"]: int(r["人口數"]) for r in rows}


def town_code(district_code):
    return district_code[:8]


def aggregate_odrp048_population(yyy):
    rows = fetch_all_pages("ODRP048", yyy)
    return {r["site_id"]: r["people_total"] for r in rows if r["people_total"] not in ("", "…")}


def aggregate_rolling_migration():
    """ODRP011:12 個月加總 in_total(m+f)、out_total(m+f),依 district_code 前 8 碼彙總。"""
    agg = {}
    for ym in ROLLING_MONTHS:
        print(f"  下載 ODRP011 遷入遷出統計表 {ym}...")
        rows = fetch_all_pages("ODRP011", ym)
        for r in rows:
            code = town_code(r["district_code"])
            entry = agg.setdefault(code, {"in_total": 0, "out_total": 0})
            entry["in_total"] += int(r["in_total_m"]) + int(r["in_total_f"])
            entry["out_total"] += int(r["out_total_m"]) + int(r["out_total_f"])
    return agg


def aggregate_rolling_vital():
    """ODRP012:12 個月加總 birth_total、death_m+death_f,依 district_code 前 8 碼彙總。"""
    agg = {}
    for ym in ROLLING_MONTHS:
        print(f"  下載 ODRP012 動態資料統計表 {ym}...")
        rows = fetch_all_pages("ODRP012", ym)
        for r in rows:
            code = town_code(r["district_code"])
            entry = agg.setdefault(code, {"birth_total": 0, "death_total": 0})
            entry["birth_total"] += int(r["birth_total"])
            entry["death_total"] += int(r["death_m"]) + int(r["death_f"])
    return agg


def main():
    code_table = load_code_table()
    ref_population = load_reference_population()

    print(f"下載 ODRP048 人口密度(年資料,{GROWTH_START_YYY} / {GROWTH_END_YYY})用來算近 5 年成長率...")
    pop_start = aggregate_odrp048_population(GROWTH_START_YYY)
    pop_end = aggregate_odrp048_population(GROWTH_END_YYY)

    print("彙總近 12 個月遷入遷出統計(ODRP011)...")
    migration = aggregate_rolling_migration()

    print("彙總近 12 個月出生死亡統計(ODRP012)...")
    vital = aggregate_rolling_vital()

    os.makedirs(OUT_DIR, exist_ok=True)
    unmatched_growth = 0
    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "行政區代碼", "縣市", "鄉鎮市區",
            f"{GROWTH_START_YYY}年底人口", f"{GROWTH_END_YYY}年底人口", "近5年人口成長率_百分比",
            "統計基準人口_11506",
            "遷入人數_近12月", "遷出人數_近12月", "淨遷入人數_近12月", "淨遷入率_千分比",
            "出生數_近12月", "死亡數_近12月", "粗出生率_千分比", "粗死亡率_千分比", "自然增加率_千分比",
        ])

        for code, (city, town, site_id) in sorted(code_table.items()):
            p_start = pop_start.get(site_id)
            p_end = pop_end.get(site_id)
            if p_start and p_end and int(p_start) > 0:
                p_start_i, p_end_i = int(p_start), int(p_end)
                growth_rate = round((p_end_i - p_start_i) / p_start_i * 100, 2)
            else:
                p_start_i = p_start or ""
                p_end_i = p_end or ""
                growth_rate = ""
                unmatched_growth += 1

            base_pop = ref_population.get(code)

            mig = migration.get(code, {"in_total": 0, "out_total": 0})
            net_migration = mig["in_total"] - mig["out_total"]
            net_migration_rate = round(net_migration / base_pop * 1000, 2) if base_pop else ""

            vit = vital.get(code, {"birth_total": 0, "death_total": 0})
            crude_birth_rate = round(vit["birth_total"] / base_pop * 1000, 2) if base_pop else ""
            crude_death_rate = round(vit["death_total"] / base_pop * 1000, 2) if base_pop else ""
            natural_growth_rate = (
                round((vit["birth_total"] - vit["death_total"]) / base_pop * 1000, 2) if base_pop else ""
            )

            w.writerow([
                code, city, town,
                p_start_i, p_end_i, growth_rate,
                base_pop or "",
                mig["in_total"], mig["out_total"], net_migration, net_migration_rate,
                vit["birth_total"], vit["death_total"], crude_birth_rate, crude_death_rate, natural_growth_rate,
            ])

    print(f"\n輸出: {OUT_PATH} ({len(code_table)} 筆)")
    if unmatched_growth:
        print(f"警告: {unmatched_growth} 個鄉鎮市區在 {GROWTH_START_YYY}/{GROWTH_END_YYY} 年人口密度資料中名稱對不上,人口成長率留空")


if __name__ == "__main__":
    main()
