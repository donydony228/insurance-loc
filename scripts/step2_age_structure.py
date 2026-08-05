"""
Step 2: 年齡結構指標 — 性別 x 五歲年齡組人口、40-64歲/65歲以上占比、老化指數、扶養比。

資料源: 內政部戶政司 OpenAPI ODRP014「村里戶數、單一年齡人口(新增區域代碼)」
  https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{yyymm}
  (免申請 API Key,依 COUNTY 分批查詢,逐村里回傳單一歲人口數)

統計年月固定用 11506,對齊 data/population/鄉鎮市區_人口統計.csv(Step 1 產出)使用的同一期資料,
方便之後直接用「縣市、鄉鎮市區」join。

輸出:
  data/population/age_structure/raw_village_age_11506.csv   村里級單一歲人口(中間產物,供之後其他年齡切法重算用)
  data/population/age_structure/鄉鎮市區_年齡結構.csv         彙總到鄉鎮市區的五歲分組 + 衍生指標
"""

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "data", "population", "age_structure")
RAW_PATH = os.path.join(OUT_DIR, "raw_village_age_11506.csv")
OUT_PATH = os.path.join(OUT_DIR, "鄉鎮市區_年齡結構.csv")

API_BASE = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/11506"
YYYMM = "11506"

COUNTIES = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "基隆市", "新竹市", "新竹縣", "苗栗縣", "彰化縣", "南投縣",
    "雲林縣", "嘉義市", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
    "臺東縣", "澎湖縣", "金門縣", "連江縣",
]

MAX_AGE_SINGLE = 100  # 0..99 單歲欄位 + 100up


def fetch_county(county: str):
    rows = []
    page = 1
    while True:
        qs = urllib.parse.urlencode({"COUNTY": county, "PAGE": str(page)})
        url = f"{API_BASE}?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.load(resp)
        data = d.get("responseData", [])
        rows.extend(data)
        total_page = int(d.get("totalPage", 1) or 1)
        if page >= total_page or not data:
            break
        page += 1
        time.sleep(0.2)
    return rows


def age_field_names():
    names = []
    for age in range(MAX_AGE_SINGLE):
        names.append((f"people_age_{age:03d}_m", f"people_age_{age:03d}_f", age))
    names.append(("people_age_100up_m", "people_age_100up_f", 100))
    return names


AGE_FIELDS = age_field_names()


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def bucket_5y(age: int) -> str:
    if age >= 100:
        return "100up"
    lo = (age // 5) * 5
    return f"{lo:02d}_{lo+4:02d}"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    raw_rows = []
    for county in COUNTIES:
        print(f"抓取 {county} ...")
        raw_rows.extend(fetch_county(county))

    print(f"共取得 {len(raw_rows)} 筆村里資料")

    raw_fieldnames = [
        "statistic_yyymm", "district_code", "site_id", "village",
        "household_no", "people_total", "people_total_m", "people_total_f",
    ] + [f for pair in AGE_FIELDS for f in pair[:2]]

    with open(RAW_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=raw_fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(raw_rows)

    # 彙總到 縣市+鄉鎮市區:五歲分組 x 性別
    agg = defaultdict(lambda: defaultdict(int))  # (縣市,鄉鎮市區) -> {bucket_m/_f: count}
    totals = defaultdict(lambda: {"household_no": 0, "people_total": 0, "people_total_m": 0, "people_total_f": 0})

    for r in raw_rows:
        site = r["site_id"]  # 例如 "臺北市松山區"
        city, district = None, None
        for c in COUNTIES:
            if site.startswith(c):
                city, district = c, site[len(c):]
                break
        if city is None:
            continue
        key = (city, district)

        totals[key]["household_no"] += to_int(r.get("household_no"))
        totals[key]["people_total"] += to_int(r.get("people_total"))
        totals[key]["people_total_m"] += to_int(r.get("people_total_m"))
        totals[key]["people_total_f"] += to_int(r.get("people_total_f"))

        for m_field, f_field, age in AGE_FIELDS:
            b = bucket_5y(age)
            agg[key][f"age_{b}_m"] += to_int(r.get(m_field))
            agg[key][f"age_{b}_f"] += to_int(r.get(f_field))

    bucket_labels = [bucket_5y(a) for a in range(0, 101, 5)]
    bucket_labels = list(dict.fromkeys(bucket_labels))  # 00_04..95_99, 100up

    out_fieldnames = ["縣市", "鄉鎮市區", "統計年月", "戶數", "人口數", "男", "女"]
    for b in bucket_labels:
        out_fieldnames += [f"age_{b}_m", f"age_{b}_f", f"age_{b}_total"]
    out_fieldnames += [
        "0_14歲人口", "15_64歲人口", "40_64歲人口", "65歲以上人口",
        "40_64歲占比", "65歲以上占比", "老化指數", "扶幼比", "扶老比", "扶養比",
    ]

    out_rows = []
    for key in sorted(agg.keys()):
        city, district = key
        buckets = agg[key]
        t = totals[key]
        row = {
            "縣市": city, "鄉鎮市區": district, "統計年月": YYYMM,
            "戶數": t["household_no"], "人口數": t["people_total"],
            "男": t["people_total_m"], "女": t["people_total_f"],
        }
        pop_0_14 = pop_15_64 = pop_40_64 = pop_65up = 0
        for b in bucket_labels:
            m = buckets.get(f"age_{b}_m", 0)
            f = buckets.get(f"age_{b}_f", 0)
            row[f"age_{b}_m"] = m
            row[f"age_{b}_f"] = f
            row[f"age_{b}_total"] = m + f

            if b == "100up":
                lo = 100
            else:
                lo = int(b.split("_")[0])
            total = m + f
            if lo <= 10:  # 0-4, 5-9, 10-14
                pop_0_14 += total
            if 15 <= lo <= 60:  # 15-19 .. 60-64
                pop_15_64 += total
            if 40 <= lo <= 60:  # 40-44 .. 60-64
                pop_40_64 += total
            if lo >= 65:
                pop_65up += total

        people_total = row["人口數"] or 1  # 避免除以 0
        row["0_14歲人口"] = pop_0_14
        row["15_64歲人口"] = pop_15_64
        row["40_64歲人口"] = pop_40_64
        row["65歲以上人口"] = pop_65up
        row["40_64歲占比"] = round(pop_40_64 / people_total * 100, 2)
        row["65歲以上占比"] = round(pop_65up / people_total * 100, 2)
        row["老化指數"] = round(pop_65up / pop_0_14 * 100, 1) if pop_0_14 else None
        row["扶幼比"] = round(pop_0_14 / pop_15_64 * 100, 1) if pop_15_64 else None
        row["扶老比"] = round(pop_65up / pop_15_64 * 100, 1) if pop_15_64 else None
        row["扶養比"] = round((pop_0_14 + pop_65up) / pop_15_64 * 100, 1) if pop_15_64 else None
        out_rows.append(row)

    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    print(f"\n彙總 {len(out_rows)} 個鄉鎮市區 -> {OUT_PATH}")
    print(f"村里級原始資料 -> {RAW_PATH}")


if __name__ == "__main__":
    main()
